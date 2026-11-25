"""Authenticated, initially unlisted takeoff page on the EXISTING MeasuredField host.

Only the HTML route is account-protected; /static/takeoff holds public,
secret-free software assets. Projects/PDFs are never sent to the server here;
browser IndexedDB is partitioned by the current signed-in user+organization.
"""
from __future__ import annotations

import hashlib
import logging
import html
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..deps import BASE_DIR, LoginRequired, Principal, get_principal, member_api
from ..db import TenantSession, get_db
from ..security import rate_limited
from ..services import takeoff_assistant

router = APIRouter()
PILOT_HTML = BASE_DIR / "templates" / "takeoff_pilot.html"
CSP = (
    "default-src 'none'; "
    "script-src 'self' 'wasm-unsafe-eval'; "
    "worker-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


def _pilot_org_ids() -> set[uuid.UUID]:
    raw = os.getenv("MF_TAKEOFF_PILOT_ORG_IDS", "")
    try:
        return {uuid.UUID(item.strip()) for item in raw.split(",") if item.strip()}
    except (ValueError, AttributeError):
        # Invalid configuration must not become an open pilot.
        return set()


def pilot_enabled() -> bool:
    return os.getenv("MF_TAKEOFF_PILOT_ENABLED", "false") == "true"


def render_takeoff_page(principal: Principal) -> str:
    """Small, audited adaptation of the *same* verified guided-app HTML."""
    if principal.org is None:
        raise HTTPException(403, "A MeasuredField business membership is required.")
    if not PILOT_HTML.is_file():
        raise HTTPException(503, "The takeoff page is not deployed.")
    source = PILOT_HTML.read_text(encoding="utf-8")
    if source.count("<head>") != 1 or source.count('<div class="shell">') != 1:
        raise HTTPException(503, "Takeoff application assets are not ready.")
    scope = hashlib.sha256(
        (str(principal.user.id) + ":" + str(principal.org.id)).encode("ascii")
    ).hexdigest()[:32]
    metadata = (
        '<meta name="mf-takeoff-storage-scope" content="' + scope + '">'
        '<base href="/static/takeoff/public/guided/">'
    )
    source = source.replace("<head>", "<head>" + metadata, 1)
    # A document <base> otherwise redirects this skip link to the asset URL.
    source = source.replace('href="#content"', 'href="/takeoff#content"', 1)
    identity = (
        '<div class="mf-takeoff-account" role="status">'
        'MeasuredField business: <strong>' + html.escape(principal.org.name) +
        '</strong> · Signed in as <strong>' + html.escape(principal.user.name) +
        '</strong> · Projects and plan files stay in this browser, under this account.'
        '</div>'
    )
    source = source.replace('<div class="shell">', identity + '<div class="shell">', 1)
    source = source.replace('</head>', '<meta name="mf-takeoff-csrf" content="' + html.escape(getattr(principal, 'csrf', ''), quote=True) + '"></head>', 1)
    return source


@router.get("/takeoff", include_in_schema=False)
@router.get("/takeoff/", include_in_schema=False)
def takeoff_page(
    request: Request,
    principal: Principal | None = Depends(get_principal),
):
    # No public navigation link or indexable route during the contractor pilot.
    if not pilot_enabled():
        raise HTTPException(404, "Not found")
    if principal is None:
        raise LoginRequired("/takeoff")
    if principal.org is None or not principal.can("read"):
        raise HTTPException(403, "Access to a MeasuredField business is required.")
    if principal.org.id not in _pilot_org_ids():
        raise HTTPException(404, "Not found")
    content = render_takeoff_page(principal)
    response = HTMLResponse(content)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response

@router.get("/api/capabilities", include_in_schema=False)
def takeoff_capabilities(principal: Principal | None = Depends(get_principal)):
    """Return a real capability check, not a false promise of AI inference.

    The contextual assistant requires explicit deployment approval for external data,
    provider configuration, CSRF, membership and quota checks.
    """
    if not pilot_enabled():
        raise HTTPException(404, "Not found")
    if (principal is None or principal.org is None or
            not principal.can("read") or principal.org.id not in _pilot_org_ids()):
        raise HTTPException(401, "Sign in to an authorized business.")
    response = JSONResponse({
        "ai_review": False, "provider": None,
        "mode": "disabled_pending_secure_provider_adapter", "writes": False,
        "assistant": takeoff_assistant.enabled() and principal.can("documents.write"),
        "assistant_demo": takeoff_assistant.demo_enabled() and principal.can("documents.write"),
        "assistant_provider": "NVIDIA" if takeoff_assistant.enabled() else None
    })
    response.headers["Cache-Control"] = "no-store"
    return response

@router.post("/api/takeoff/assistant", include_in_schema=False)
async def takeoff_chat(
    request: Request,
    principal: Principal = Depends(member_api("documents.write")),
    db: TenantSession = Depends(get_db),
):
    if not pilot_enabled() or principal.org.id not in _pilot_org_ids():
        raise HTTPException(404, "Not found")
    if not (takeoff_assistant.enabled() or takeoff_assistant.demo_enabled()):
        raise HTTPException(503, "AI assistant is not enabled")
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "Expected JSON")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > takeoff_assistant.MAX_BODY:
            raise HTTPException(413, "Request too large")
    import json
    try:
        payload = json.loads(raw)
        demo = payload == {"demo": True, "consent": True}
        if demo and takeoff_assistant.demo_enabled():
            body = takeoff_assistant.demo_body()
        elif not takeoff_assistant.enabled():
            raise HTTPException(403, "Only the fixed fictional demonstration is approved for external inference")
        else:
            body = takeoff_assistant.prepare(payload)
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(400, "Check your question, work-area image and sharing consent") from None
    # Durable reservations; count attempts even if provider refuses or times out.
    # Separate global and organization limits; no client-chosen identity or model.
    org_limited = rate_limited(db, "takeoff-ai-org:" + str(principal.org.id), limit=20, window_seconds=86400)
    if org_limited:
        db.commit()
        raise HTTPException(429, "Daily workspace assistant limit reached")
    global_limited = rate_limited(db, "takeoff-ai-global", limit=40, window_seconds=86400)
    db.commit()
    if global_limited:
        raise HTTPException(429, "Daily assistant limit reached")
    try:
        result = await takeoff_assistant.infer(body)
    except Exception as exc:
        logging.getLogger(__name__).warning("Takeoff provider failed: %s", type(exc).__name__)
        # Do not expose raw provider messages, supplied drawings, or credentials.
        raise HTTPException(502, "AI provider could not complete this request. Try a shorter question.") from None
    return JSONResponse({**result, "synthetic_demo": demo}, headers={"Cache-Control":"no-store"})
