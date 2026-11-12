"""Request-scoped identity and authorization.

The tenant for a request is derived from the server-side session and a membership row,
never from a path, query string, header or body value supplied by the browser. Object ids
in URLs are looked up inside that tenant's row-level-security context, so an id belonging
to another organization behaves exactly like an id that does not exist (404).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from fastapi import Depends, Form, Header, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from .config import get_settings
from .db import TenantSession, get_db
from .models import Membership, Organization, Session, User
from .security import SESSION_COOKIE, can, client_address, csrf_ok, csrf_token, hash_ip, load_session

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@dataclass
class Principal:
    user: User
    session: Session
    org: Organization | None = None
    role: str | None = None

    def can(self, permission: str) -> bool:
        return can(self.role, permission)

    @property
    def csrf(self) -> str:
        return csrf_token(self.session)


class LoginRequired(Exception):
    def __init__(self, next_path: str = "/app"):
        self.next_path = next_path


def client_key(request: Request) -> str:
    """Hashed client address for rate limiting, derived strictly from the ASGI socket.

    Arbitrary forwarded headers (such as X-Forwarded-For) are ignored at the application
    layer to prevent client-spoofed rate-limit bypasses. Reverse-proxy client attribution
    is NOT production-ready at the app layer and must be handled via explicitly configured
    ASGI trusted proxies (populating request.client.host from verified proxy peers only)
    and/or edge rate limiting.
    """
    peer = (request.client.host if request.client and request.client.host else "") or "unknown"
    # MF_TRUSTED_PROXY_HOPS=0 (default) keeps the behaviour described above. A positive,
    # verified hop count selects only the entry our own proxy wrote; see client_address.
    return hash_ip(client_address(peer, request.headers.get("x-forwarded-for"),
                                  get_settings().trusted_proxy_hops))


def parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(404, "Not found") from None


def get_principal(request: Request, db: TenantSession = Depends(get_db)) -> Principal | None:
    session = load_session(db, request.cookies.get(SESSION_COOKIE))
    if session is None:
        return None
    user = db.get(User, session.user_id)
    if user is None or user.disabled_at is not None:
        return None
    principal = Principal(user=user, session=session)
    db.set_context(None, user.id)
    if session.active_org_id:
        membership = db.scalar(select(Membership).where(
            Membership.user_id == user.id, Membership.org_id == session.active_org_id))
        if membership is not None:
            db.set_context(membership.org_id, user.id)
            org = db.get(Organization, membership.org_id)
            if org is not None and org.status != "deleted":
                principal.org, principal.role = org, membership.role
            else:
                db.set_context(None, user.id)
    request.state.principal = principal
    return principal


def require_user(request: Request, principal: Principal | None = Depends(get_principal)) -> Principal:
    if principal is None:
        raise LoginRequired(request.url.path)
    return principal


def _check_csrf(principal: Principal, token: str | None) -> None:
    if not csrf_ok(principal.session, token):
        raise HTTPException(403, "Your session expired or the form was not sent from this site. Reload and try again.")


def member(permission: str = "read"):
    """GET routes inside a workspace."""
    def dependency(principal: Principal = Depends(require_user)) -> Principal:
        if principal.org is None:
            raise HTTPException(403, "You are not a member of a workspace.")
        if not principal.can(permission):
            raise HTTPException(403, "Your role does not allow this.")
        return principal
    return dependency


def member_post(permission: str):
    """State-changing form routes: membership + permission + CSRF token."""
    def dependency(principal: Principal = Depends(require_user), csrf: str = Form(default="")) -> Principal:
        _check_csrf(principal, csrf)
        if principal.org is None:
            raise HTTPException(403, "You are not a member of a workspace.")
        if not principal.can(permission):
            raise HTTPException(403, "Your role does not allow this.")
        return principal
    return dependency


def member_api(permission: str):
    """JSON API routes. Unsafe methods need the X-CSRF-Token header."""
    def dependency(request: Request, principal: Principal | None = Depends(get_principal),
                   x_csrf_token: str | None = Header(default=None)) -> Principal:
        if principal is None:
            raise HTTPException(401, "Sign in required.")
        if request.method not in ("GET", "HEAD"):
            _check_csrf(principal, x_csrf_token)
        if principal.org is None or not principal.can(permission):
            raise HTTPException(403, "Not allowed.")
        return principal
    return dependency


def operator(principal: Principal = Depends(require_user)) -> Principal:
    if not principal.user.is_platform_admin:
        raise HTTPException(404, "Not found")
    return principal


def operator_post(principal: Principal = Depends(require_user), csrf: str = Form(default="")) -> Principal:
    if not principal.user.is_platform_admin:
        raise HTTPException(404, "Not found")
    _check_csrf(principal, csrf)
    return principal


def user_post(principal: Principal = Depends(require_user), csrf: str = Form(default="")) -> Principal:
    _check_csrf(principal, csrf)
    return principal


# Signed, short-lived notice tokens prevent arbitrary ?ok= / ?err= UI spoofing.
# Redirect construction has no Request object, so signing the notice preserves all
# existing back(path, ok=..., err=...) call sites without client-supplied messages.
NOTICE_TTL_SECONDS = 300
NOTICE_PARAM = "mf_notice"
NOTICE_CONTEXT = b"measuredfield.flash.v1:"


def safe_local_redirect(value: str) -> bool:
    """Allow only local absolute paths; reject browser-normalized authority tricks."""
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 1024:
        return False
    if "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return False
    parsed = urlsplit(value)
    prefix = unquote(parsed.path[:32])
    return (not parsed.scheme and not parsed.netloc
            and parsed.path.startswith("/") and not parsed.path.startswith("//")
            and not prefix.startswith("//") and "\\" not in prefix
            and all(ord(c) >= 32 and ord(c) != 127 for c in prefix))


def _notice_token(kind: str, message: str, *, issued: int | None = None,
                  target_path: str = "/") -> str:
    if kind not in ("ok", "err"):
        raise ValueError("Invalid notice kind")
    # Notices are valid only on the destination path that issued them.
    if not safe_local_redirect(target_path) or "?" in target_path or "#" in target_path:
        raise ValueError("Invalid notice destination")
    payload = {"kind": kind, "message": str(message)[:200], "target": target_path,
               "issued": int(time.time()) if issued is None else int(issued)}
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(get_settings().secret_key.encode("utf-8"),
                         NOTICE_CONTEXT + body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def _verified_notice(token: str, *, target_path: str | None = None) -> tuple[str, str]:
    if not token or len(token) > 2048 or token.count(".") != 1:
        return "", ""
    try:
        body, signature = token.split(".", 1)
        if len(signature) != 64 or len(body) > 1980:
            return "", ""
        expected = hmac.new(get_settings().secret_key.encode("utf-8"),
                            NOTICE_CONTEXT + body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return "", ""
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        payload = json.loads(raw)
        kind, message, issued = payload["kind"], payload["message"], payload["issued"]
        target = payload["target"]
        now = int(time.time())
        if (kind not in ("ok", "err") or not isinstance(message, str)
                or type(issued) is not int or len(message) > 200
                or not isinstance(target, str) or not safe_local_redirect(target)
                or "?" in target or "#" in target
                or (target_path is not None and target != target_path)
                or issued < now - NOTICE_TTL_SECONDS or issued > now + 30):
            return "", ""
        return kind, message
    except (ValueError, TypeError, KeyError, UnicodeError, base64.binascii.Error):
        return "", ""


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    settings = get_settings()
    principal = getattr(request.state, "principal", None)
    kind, message = _verified_notice(request.query_params.get(NOTICE_PARAM, ""),
                                     target_path=request.url.path)
    ctx = {
        "brand": settings.brand_name, "settings": settings, "principal": principal,
        "csrf": principal.csrf if principal else "", "path": request.url.path,
        "flash": message if kind == "ok" else "",
        "flash_error": message if kind == "err" else "",
    }
    ctx.update(context or {})
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def back(path: str, ok: str = "", err: str = ""):
    from fastapi.responses import RedirectResponse
    if not safe_local_redirect(path):
        raise ValueError("Redirect destination must be an internal absolute path")
    parsed = urlsplit(path)
    kind, message = ("ok", ok) if ok else (("err", err) if err else ("", ""))
    # Remove any stale/attacker-supplied notice and preserve the URL fragment.
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if k != NOTICE_PARAM]
    if message:
        query.append((NOTICE_PARAM, _notice_token(kind, message, target_path=parsed.path)))
    location = urlunsplit(("", "", parsed.path, urlencode(query), parsed.fragment))
    return RedirectResponse(location, status_code=303)
