"""Application assembly.

One process serves three surfaces, chosen by the request's Host header:

* a hostname listed in MF_APP_HOSTS  -> marketing site, sign-in, private workspace, operator console
* any other hostname                 -> a customer's published website (looked up in site_hosts)

``/healthz`` and ``/readyz`` answer on any hostname so the platform health check works
before DNS is configured.
"""
from __future__ import annotations

import ipaddress
import logging
import mimetypes
import re
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from starlette.concurrency import run_in_threadpool

from .config import get_settings
from .db import finish_request_session, get_engine, isolation_self_check
from .deps import BASE_DIR, LoginRequired, render
from .routers import auth, operator, public, site_host, workspace
from .services import jobs

log = logging.getLogger("mf")

APP_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
           "connect-src 'self'; frame-src 'self'; form-action 'self' https://checkout.stripe.com; "
           "base-uri 'none'; frame-ancestors 'self'; object-src 'none'")


def _security_headers(response, *, csp: str = APP_CSP, production: bool) -> None:
    headers = response.headers
    headers.setdefault("Content-Security-Policy", csp)
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if production:
        headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    problems = settings.validate_for_production()
    if settings.database_url:
        get_engine()
        problems += isolation_self_check()
    if problems and settings.is_production:
        # Fail closed: refuse to serve traffic with a configuration that cannot isolate tenants.
        raise RuntimeError("Refusing to start: " + " | ".join(problems))
    for problem in problems:
        log.warning("startup check: %s", problem)
    yield


def create_main_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="MeasuredField", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    # Windows/older OS MIME registries may not recognize WebP. Browsers and
    # social preview crawlers need the correct type rather than octet-stream.
    mimetypes.add_type("image/webp", ".webp", strict=True)
    mimetypes.add_type("text/vtt", ".vtt", strict=True)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    for module in (public, auth, workspace, operator):
        app.include_router(module.router)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        started = time.monotonic()
        request_id = secrets.token_hex(8)
        request.state.request_id = request_id
        # Cross-site request defense in depth (the CSRF token is the primary control).
        if request.method not in ("GET", "HEAD", "OPTIONS") and not request.url.path.startswith("/webhooks/"):
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc.lower() != request.headers.get("host", "").lower():
                return PlainTextResponse("Cross-site request refused.", status_code=403)
        try:
            response = await call_next(request)
        except Exception:
            await run_in_threadpool(finish_request_session, request, success=False)
            raise
        jobs_pending = await run_in_threadpool(finish_request_session, request,
                                               success=response.status_code < 400)
        if jobs_pending and settings.inline_jobs:
            await run_in_threadpool(jobs.run_due, 25)
        _security_headers(response, production=settings.is_production)
        if request.url.path.startswith(("/app", "/operator", "/api", "/assessment/r", "/order", "/p/")):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Request-ID"] = request_id
        # Path only: never the query string, body, cookies or client address.
        log.info("request id=%s method=%s path=%s status=%s ms=%d", request_id, request.method,
                 request.url.path, response.status_code, int((time.monotonic() - started) * 1000))
        return response

    @app.exception_handler(LoginRequired)
    async def login_required(request: Request, exc: LoginRequired):
        return RedirectResponse("/login?next=" + quote(exc.next_path, safe="/"), status_code=303)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/") or request.url.path.startswith("/webhooks/"):
            return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)
        return render(request, "error.html", {"status": exc.status_code, "message": str(exc.detail)},
                      status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        # The traceback goes to the server log; the visitor gets a reference id only.
        log.exception("unhandled error id=%s path=%s", getattr(request.state, "request_id", "-"), request.url.path)
        body = {"error": "Something went wrong on our side.", "reference": getattr(request.state, "request_id", "")}
        if request.url.path.startswith("/api/"):
            return JSONResponse(body, status_code=500)
        return render(request, "error.html", {"status": 500, "message": body["error"],
                                              "reference": body["reference"]}, status_code=500)

    return app


# Validate HTTP authority BEFORE app/site dispatch. The original colon split
# allowed malformed host ports and ambiguous IPv6/duplicate Host headers.
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def canonical_host(raw: bytes) -> str | None:
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    if not value or len(value) > 280 or value.strip() != value:
        return None
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in value):
        return None
    if any(ch in value for ch in ("/", "\\", "@", "#", "?", ",", "%")):
        return None
    if value.startswith("["):
        match = re.fullmatch(r"\[([0-9A-Fa-f:.]+)\](?::([0-9]+))?", value)
        if not match:
            return None
        try:
            hostname = str(ipaddress.IPv6Address(match.group(1)))
        except ValueError:
            return None
        port = match.group(2)
    else:
        if value.count(":") > 1:
            return None
        hostname, colon, port = value.partition(":")
        hostname = hostname.lower()
        if hostname.endswith("."):
            hostname = hostname[:-1]
        if not hostname or len(hostname) > 253:
            return None
        if not all(_LABEL.fullmatch(label) for label in hostname.split(".")):
            return None
        if not colon:
            port = None
    if port is not None and (not port.isascii() or not port.isdecimal()
                             or not 1 <= int(port) <= 65535):
        return None
    return hostname


class HostRouter:
    """ASGI dispatcher: health checks, then app hosts vs. customer-site hosts."""

    def __init__(self, main_app, sites_app):
        self.main_app, self.sites_app = main_app, sites_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.main_app(scope, receive, send)
        if scope["type"] != "http":
            return await self.main_app(scope, receive, send)
        raw_hosts = [value for key, value in scope.get("headers", [])
                     if key.lower() == b"host"]
        host = canonical_host(raw_hosts[0]) if len(raw_hosts) == 1 else None
        if host is None:
            response = PlainTextResponse("Invalid Host header.", status_code=400)
            _security_headers(response, production=get_settings().is_production)
            return await response(scope, receive, send)
        # Downstream site routing must use the SAME validated hostname.
        scope["mf.canonical_host"] = host
        path = scope.get("path", "")
        if path in ("/healthz", "/readyz"):
            return await self._health(path, scope, receive, send)
        if host in get_settings().app_host_set:
            return await self.main_app(scope, receive, send)
        return await self.sites_app(scope, receive, send)

    async def _health(self, path, scope, receive, send):
        status, body = 200, {"status": "ok"}
        if path == "/readyz":
            try:
                with get_engine().connect() as conn:
                    conn.execute(text("SELECT 1"))
                body = {"status": "ready"}
            except Exception:  # noqa: BLE001
                status, body = 503, {"status": "unavailable"}
        await JSONResponse(body, status_code=status)(scope, receive, send)


def create_app() -> HostRouter:
    return HostRouter(create_main_app(), site_host.create_sites_app())


app = create_app()

__all__ = ["app", "create_app", "HTTPException"]
