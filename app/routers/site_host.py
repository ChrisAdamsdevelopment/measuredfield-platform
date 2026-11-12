"""Published customer websites, served from shared infrastructure by hostname.

This sub-application is reached only for hostnames that are NOT platform hosts. It has
no cookies, no sessions and no access to anything but the one site its hostname maps to.
The owning organization always comes from the ``site_hosts`` lookup of the Host header;
a lead cannot name its own destination.
"""
from __future__ import annotations

import json
import logging

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from ..config import get_settings
from ..db import session_scope
from ..security import client_address, hash_ip, rate_limited
from ..services import jobs, sitegen, sites

log = logging.getLogger("mf.sites")

SITE_CSP = ("default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
MAX_BODY = 8192


def _headers(extra: dict | None = None) -> dict:
    headers = {"Content-Security-Policy": SITE_CSP, "X-Content-Type-Options": "nosniff",
               "Referrer-Policy": "strict-origin-when-cross-origin", "X-Frame-Options": "DENY"}
    if get_settings().is_production:
        headers["Strict-Transport-Security"] = "max-age=31536000"
    headers.update(extra or {})
    return headers


def _client(request: Request) -> str:
    # Derive client key solely from the ASGI socket peer (request.client.host).
    # Unverified forwarded headers (e.g. X-Forwarded-For) are ignored at the app layer
    # to prevent spoofed rate-limit bypasses; reverse-proxy handling is not production-ready
    # here and must be solved with verified ASGI trusted proxies and/or edge rate limiting.
    peer = (request.client.host if request.client and request.client.host else "") or "unknown"
    return hash_ip(client_address(peer, request.headers.get("x-forwarded-for"),
                                  get_settings().trusted_proxy_hops))


def _not_found() -> Response:
    return PlainTextResponse("No website is published at this address.", status_code=404, headers=_headers())


def _load(hostname: str, want: str):
    with session_scope() as db:
        host = sites.resolve_host(db, hostname)
        if host is None:
            return None
        version = sites.published_version(db, host)
        if version is None:
            return None
        return version.html if want == "html" else version.css


async def page(request: Request) -> Response:
    body = await run_in_threadpool(_load, (request.scope.get("mf.canonical_host") or request.headers.get("host", "")), "html")
    if body is None:
        return _not_found()
    return Response(body, media_type="text/html; charset=utf-8", headers=_headers({"Cache-Control": "public, max-age=60"}))


async def stylesheet(request: Request) -> Response:
    body = await run_in_threadpool(_load, (request.scope.get("mf.canonical_host") or request.headers.get("host", "")), "css")
    if body is None:
        return _not_found()
    return Response(body, media_type="text/css; charset=utf-8", headers=_headers({"Cache-Control": "public, max-age=60"}))


async def script(request: Request) -> Response:
    return Response(sitegen.SITE_JS, media_type="text/javascript; charset=utf-8",
                    headers=_headers({"Cache-Control": "public, max-age=3600"}))


async def robots(request: Request) -> Response:
    return PlainTextResponse("User-agent: *\nAllow: /\n", headers=_headers())


async def _json_body(request: Request):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        return None
    raw = await request.body()
    if not 2 <= len(raw) <= MAX_BODY:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    host = request.headers.get("host", "")
    return origin in ("https://" + host, "http://" + host)


def _store_lead(hostname: str, client: str, payload: dict):
    with session_scope() as db:
        host = sites.resolve_host(db, hostname)
        if host is None:
            return 404, {"error": "Not found"}
        version = sites.published_version(db, host)
        if version is None:
            return 404, {"error": "Not found"}
        if rate_limited(db, f"lead:{host.site_id}:{client}", limit=5, window_seconds=600) or \
                rate_limited(db, f"lead-site:{host.site_id}", limit=200, window_seconds=3600):
            db.commit()
            return 429, {"error": "Please wait a few minutes before sending another request."}
        try:
            clean = sites.validate_lead(payload, [s["name"] for s in version.content.get("services", [])])
        except sites.SiteError as exc:
            return 422, {"error": str(exc)}
        if clean is None:  # honeypot: answer as if it worked, store nothing
            return 201, {"ok": True}
        lead = sites.capture_lead(db, host, clean)
        jobs.enqueue(db, "notify_new_lead", {"lead_id": str(lead.id)}, key=f"lead-notice:{lead.id}", org_id=host.org_id)
        return 201, {"ok": True}


async def lead(request: Request) -> Response:
    if not _same_origin(request):
        return JSONResponse({"error": "Cross-site request refused."}, status_code=403, headers=_headers())
    payload = await _json_body(request)
    if payload is None:
        return JSONResponse({"error": "Invalid request."}, status_code=422, headers=_headers())
    status, body = await run_in_threadpool(_store_lead, (request.scope.get("mf.canonical_host") or request.headers.get("host", "")), _client(request), payload)
    if status == 201 and get_settings().inline_jobs:
        await run_in_threadpool(jobs.run_due, 10)
    return JSONResponse(body, status_code=status, headers=_headers({"Cache-Control": "no-store"}))


def _store_event(hostname: str, client: str, kind: str) -> None:
    with session_scope() as db:
        host = sites.resolve_host(db, hostname)
        if host is None or sites.published_version(db, host) is None:
            return
        if rate_limited(db, f"event:{host.site_id}:{client}", limit=30, window_seconds=600):
            return
        sites.record_event(db, host, kind)


async def event(request: Request) -> Response:
    payload = await _json_body(request)
    if payload and _same_origin(request) and payload.get("kind") in ("page_view", "form_start"):
        await run_in_threadpool(_store_event, (request.scope.get("mf.canonical_host") or request.headers.get("host", "")), _client(request), payload["kind"])
    return Response(status_code=204, headers=_headers())


async def fallback(request: Request, exc) -> Response:  # noqa: ARG001
    return _not_found()


async def server_error(request: Request, exc) -> Response:
    log.exception("customer site error path=%s", request.url.path)
    return PlainTextResponse("This website is temporarily unavailable.", status_code=500, headers=_headers())


def create_sites_app() -> Starlette:
    return Starlette(
        routes=[Route("/", page), Route("/index.html", page), Route("/style.css", stylesheet),
                Route("/site.js", script), Route("/robots.txt", robots),
                Route("/api/lead", lead, methods=["POST"]), Route("/api/event", event, methods=["POST"])],
        exception_handlers={404: fallback, 405: fallback, 500: server_error},
    )
