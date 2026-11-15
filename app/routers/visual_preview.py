"""Development-only browsable visual preview demo router (MF-016).

Provides a local, end-to-end interactive Before/After website improvement studio
at `/dev/visual-preview` using MF-014 static HTML diagnostics, MF-015 evidence-linked
visual proposals, and MF-016 owner-attested temporary raster media ingestion.

Security & Environment Boundaries:
- Disabled (404) in `production`.
- Disabled (404) in `staging` unless `MF_ENABLE_STAGING_VISUAL_DEMO=1` AND the
  caller is an authenticated platform operator (`principal.user.is_platform_admin`).
- Requires CSRF token verification and per-client rate limiting on all POST routes.
- Never downloads or stores full candidate media until the owner explicitly attests
  rights (`owner_rights_confirmed == "yes"`).
- Serves all CSS, JS, and re-encoded raster media from same-origin endpoints with
  zero inline styles and zero third-party hotlinks (`img-src 'self' data:`).
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import html
import os
import re
import secrets
import socket
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..config import get_settings
from ..db import get_db
from ..deps import Principal, client_key, get_principal
from ..schemas.ai_visual import BrandAssetCandidate, VisualDesignProposal
from ..schemas.preview import (
    DiagnosticObservationCard,
    PreviewConceptProfile,
)
from ..schemas.preview_media import IngestedPreviewMedia, OwnerMediaAttestation
from ..services.brand_assets import extract_asset_candidates, sniff_asset_candidate
from ..services.gemini_preview import build_deterministic_visual_proposal
from ..services.preview_composition import compose_interactive_visual_preview
from ..services.preview_media import (
    DEFAULT_PREVIEW_MEDIA_STORE,
    PreviewMediaError,
    PreviewMediaStore,
    download_and_ingest_attested_media,
    generate_synthetic_fixture_media_bytes,
)
from ..services.scanner import SSRFBlockedError, inspect_public_url
from ..services.widgets import default_widget_for_industry

router = APIRouter(prefix="/dev/visual-preview", tags=["dev_visual_preview"])

DEV_STUDIO_CSP = (
    "default-src 'none'; "
    "style-src 'self'; "
    "script-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'"
)

_COOKIE_NAME = "mf_dev_preview_sid"
_SESSION_ID_RE = re.compile(r"^[a-f0-9]{24,64}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{24,96}$")

# Synthetic fixture constants for offline local demonstration
SYNTHETIC_FIXTURE_URL = "https://granitepeak-remodeling.example.com/"
SYNTHETIC_LOGO_URL = "https://granitepeak-remodeling.example.com/assets/granite-peak-logo.png"
SYNTHETIC_HERO_URL = "https://granitepeak-remodeling.example.com/assets/granite-peak-kitchen-hero.jpg"

SYNTHETIC_FIXTURE_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Granite Peak Kitchen &amp; Bath Remodeling</title>
  <meta property="og:image" content="https://granitepeak-remodeling.example.com/assets/granite-peak-kitchen-hero.jpg">
  <link rel="icon" href="/favicon.png" type="image/png">
</head>
<body>
  <header class="site-header">
    <img src="/assets/granite-peak-logo.png" alt="Granite Peak Remodeling emblem" width="320" height="96" class="brand-logo">
    <nav><a href="#services">Services</a> <a href="#about">About</a></nav>
  </header>
  <main>
    <section class="hero-section">
      <h2>Welcome to Our Site</h2>
      <p>Granite Peak Kitchen &amp; Bath Remodeling serves homeowners in Prescott, AZ with custom cabinetry, tile showers, and structural additions.</p>
      <img src="/assets/granite-peak-kitchen-hero.jpg" alt="Completed Prescott custom walnut kitchen island" width="640" height="360">
      <p>Call our office at (928) 555-0148 to discuss your project.</p>
    </section>
  </main>
</body>
</html>
"""

DEV_STUDIO_BAR_CSS = """
/* === MF-016 Local Visual Demo Control Bar === */
.mf-dev-control-banner {
  background: #0f172a;
  color: #f8fafc;
  border-bottom: 2px solid #334155;
  padding: 1rem 0;
  font-size: 0.9rem;
}
.mf-dev-control-banner h1 {
  font-size: 1.2rem;
  color: #ffffff;
  margin: 0 0 0.35rem 0;
}
.mf-dev-control-banner p {
  margin: 0 0 0.75rem 0;
  color: #cbd5e1;
}
.mf-dev-forms-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 1rem;
  margin-top: 0.75rem;
}
.mf-dev-card {
  background: #1e293b;
  border: 1px solid #475569;
  border-radius: 8px;
  padding: 0.9rem 1rem;
}
.mf-dev-card h2 {
  font-size: 0.98rem;
  color: #f8fafc;
  margin: 0 0 0.5rem 0;
}
.mf-dev-field {
  margin-bottom: 0.6rem;
}
.mf-dev-field label {
  display: block;
  font-weight: 600;
  font-size: 0.82rem;
  color: #e2e8f0;
  margin-bottom: 0.25rem;
}
.mf-dev-field input[type="text"],
.mf-dev-field input[type="url"],
.mf-dev-field select {
  width: 100%;
  padding: 0.45rem 0.6rem;
  border-radius: 5px;
  border: 1px solid #64748b;
  background: #0f172a;
  color: #f8fafc;
  font-size: 0.88rem;
}
.mf-dev-checkbox-label {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  font-size: 0.84rem;
  color: #fde68a;
  margin: 0.6rem 0;
  cursor: pointer;
}
.mf-dev-btn {
  display: inline-block;
  background: #2563eb;
  color: #ffffff;
  font-weight: 600;
  font-size: 0.86rem;
  padding: 0.48rem 0.95rem;
  border: none;
  border-radius: 6px;
  cursor: pointer;
}
.mf-dev-btn-secondary {
  background: #059669;
}
.mf-dev-status-pill {
  display: inline-block;
  padding: 0.2rem 0.55rem;
  border-radius: 999px;
  font-size: 0.76rem;
  font-weight: 700;
  background: #1d4ed8;
  color: #ffffff;
  margin-bottom: 0.4rem;
}
.mf-dev-alert {
  background: #7f1d1d;
  border: 1px solid #f87171;
  color: #fef2f2;
  padding: 0.6rem 0.85rem;
  border-radius: 6px;
  margin-bottom: 0.75rem;
  font-weight: 600;
}
"""


@dataclasses.dataclass
class VisualPreviewSession:
    """Ephemeral in-memory session state for `/dev/visual-preview`."""

    session_id: str
    created_at_epoch: float
    expires_at_epoch: float
    is_synthetic_fixture: bool
    inspected_url: str
    profile: PreviewConceptProfile
    card: DiagnosticObservationCard
    assets: list[BrandAssetCandidate]
    proposal: VisualDesignProposal
    attestations: list[OwnerMediaAttestation] = dataclasses.field(default_factory=list)
    last_notice: str = ""


class VisualPreviewSessionRegistry:
    """Bounded ephemeral registry of active visual preview sessions."""

    def __init__(self, *, max_sessions: int = 32, ttl_seconds: int = 1800) -> None:
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, VisualPreviewSession] = {}

    def purge_expired(self, now: float | None = None) -> None:
        current = time.time() if now is None else now
        expired = [
            sid for sid, sess in self._sessions.items() if sess.expires_at_epoch <= current
        ]
        for sid in expired:
            self._sessions.pop(sid, None)
            DEFAULT_PREVIEW_MEDIA_STORE.purge_session(sid)

    def put(self, session: VisualPreviewSession) -> None:
        self.purge_expired()
        if len(self._sessions) >= self.max_sessions and session.session_id not in self._sessions:
            oldest_sid = min(
                self._sessions, key=lambda k: self._sessions[k].created_at_epoch
            )
            self._sessions.pop(oldest_sid, None)
            DEFAULT_PREVIEW_MEDIA_STORE.purge_session(oldest_sid)
        self._sessions[session.session_id] = session

    def get(self, session_id: str, now: float | None = None) -> VisualPreviewSession | None:
        self.purge_expired(now=now)
        if not _SESSION_ID_RE.match(session_id or ""):
            return None
        return self._sessions.get(session_id)

    def clear(self) -> None:
        self._sessions.clear()


DEFAULT_VISUAL_SESSION_REGISTRY = VisualPreviewSessionRegistry()

# Per-client fixed-window rate limiter for inspection and media ingestion POSTs
_RATE_WINDOW_SECONDS = 60.0
_MAX_POSTS_PER_WINDOW = 15
_client_post_timestamps: dict[str, list[float]] = {}


def reset_visual_preview_rate_limits() -> None:
    """Clear in-memory rate limit counters (used by tests)."""
    _client_post_timestamps.clear()


def _enforce_rate_limit(request: Request, max_requests: int = _MAX_POSTS_PER_WINDOW) -> None:
    now = time.time()
    key = client_key(request)
    history = [ts for ts in _client_post_timestamps.get(key, []) if now - ts < _RATE_WINDOW_SECONDS]
    if len(history) >= max_requests:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded for visual preview studio actions. Please wait a moment.",
        )
    history.append(now)
    _client_post_timestamps[key] = history


def _require_visual_demo_allowed(request: Request) -> None:
    """Enforce MF-016 environment gate:
    - Always 404 in `production`.
    - Always 404 in `staging` unless `MF_ENABLE_STAGING_VISUAL_DEMO=1` AND caller is platform admin.
    """
    cfg = get_settings()
    env = (os.environ.get("MF_ENV") or cfg.env or "development").lower()
    if env == "production":
        raise HTTPException(status_code=404, detail="Not found")
    if env == "staging":
        staging_flag = os.environ.get("MF_ENABLE_STAGING_VISUAL_DEMO", "") == "1"
        if not staging_flag:
            raise HTTPException(status_code=404, detail="Not found")
        principal: Principal | None = getattr(request.state, "principal", None)
        if principal is None and cfg.database_url:
            try:
                db = get_db(request)
                principal = get_principal(request, db)
            except Exception:
                principal = None
        is_operator = bool(principal and principal.user and principal.user.is_platform_admin)
        if not is_operator:
            raise HTTPException(status_code=404, detail="Not found")


def _get_or_create_cookie_sid(request: Request) -> str:
    raw = request.cookies.get(_COOKIE_NAME, "")
    if _SESSION_ID_RE.match(raw):
        return raw
    return secrets.token_hex(16)


def _compute_csrf_token(cookie_sid: str) -> str:
    cfg = get_settings()
    msg = f"mf-dev-visual-preview-csrf:{cookie_sid}".encode("utf-8")
    return hmac.new(cfg.secret_key.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _verify_csrf_token(request: Request, submitted_token: str) -> str:
    cookie_sid = request.cookies.get(_COOKIE_NAME, "")
    if not _SESSION_ID_RE.match(cookie_sid) or not submitted_token:
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid.")
    expected = _compute_csrf_token(cookie_sid)
    if not hmac.compare_digest(expected, submitted_token):
        raise HTTPException(status_code=403, detail="CSRF token mismatch.")
    return cookie_sid


def _build_synthetic_fixture_transport() -> httpx.MockTransport:
    """Create a deterministic MockTransport serving the synthetic contractor HTML and raster assets."""
    logo_bytes = generate_synthetic_fixture_media_bytes("logo", include_exif=False)
    hero_bytes = generate_synthetic_fixture_media_bytes("hero", include_exif=True)

    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/granite-peak-logo.png") or path.endswith("/favicon.png"):
            return httpx.Response(
                200,
                headers={"content-type": "image/png", "content-length": str(len(logo_bytes))},
                content=logo_bytes,
            )
        if path.endswith("/granite-peak-kitchen-hero.jpg"):
            return httpx.Response(
                200,
                headers={"content-type": "image/jpeg", "content-length": str(len(hero_bytes))},
                content=hero_bytes,
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=SYNTHETIC_FIXTURE_HTML.encode("utf-8"),
        )

    return httpx.MockTransport(_handler)


def _synthetic_dns_resolver(host: str, port: int = 443, *_args: Any) -> list[Any]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def _create_inspection_session(
    *,
    mode: str,
    target_url: str,
    business_name: str,
    city: str,
    industry: str,
    transport: httpx.BaseTransport | None = None,
    dns_resolver: Any = None,
) -> VisualPreviewSession:
    """Execute Stage 1: static HTML diagnostic scan + candidate brand asset discovery."""
    is_synthetic = mode == "synthetic_fixture"
    effective_url = SYNTHETIC_FIXTURE_URL if is_synthetic else target_url.strip()
    effective_transport = _build_synthetic_fixture_transport() if is_synthetic else transport
    effective_dns = _synthetic_dns_resolver if is_synthetic else dns_resolver

    card = inspect_public_url(
        effective_url,
        resolver=effective_dns,
        transport=effective_transport,
    )
    if is_synthetic:
        raw_candidates = extract_asset_candidates(SYNTHETIC_FIXTURE_HTML, SYNTHETIC_FIXTURE_URL)
        assets = [
            sniff_asset_candidate(c, resolver=effective_dns, transport=effective_transport)
            for c in raw_candidates
        ]
    elif card.scan_state == "completed":
        raw_candidates = []
        try:
            from ..services.brand_assets import _VerifiedTransportWrapper
            from ..services.scanner import (
                PinnedPublicIPTransport,
                build_pinned_request,
                validate_and_resolve_url,
            )

            target = validate_and_resolve_url(effective_url, resolver=effective_dns)
            req_transport = (
                _VerifiedTransportWrapper(effective_transport)
                if effective_transport is not None
                else PinnedPublicIPTransport()
            )
            with httpx.Client(transport=req_transport, follow_redirects=False, timeout=4.0) as client:
                req = build_pinned_request(target)
                resp = client.send(req)
                if resp.status_code == 200 and "html" in resp.headers.get("content-type", "").lower():
                    html_text = resp.content[:262144].decode("utf-8", errors="replace")
                    raw_candidates = extract_asset_candidates(html_text, effective_url)
        except Exception:
            raw_candidates = []
        assets = [
            sniff_asset_candidate(c, resolver=effective_dns, transport=effective_transport)
            for c in raw_candidates[:8]
        ]
    else:
        assets = []

    resolved_name = (
        business_name.strip()
        or (card.observed_title.split("|")[0].strip() if card.observed_title else "")
        or "Granite Peak Kitchen & Bath Remodeling"
    )
    resolved_city = city.strip() or "Prescott, AZ"
    resolved_industry = (
        industry.strip()
        if industry in {"remodeling", "construction", "cleaning", "landscaping", "professional", "retail", "wellness", "food", "other"}
        else "remodeling"
    )

    profile = PreviewConceptProfile(
        name=resolved_name[:80],
        industry=resolved_industry,
        city=resolved_city[:80],
        headline=card.observed_h1 or "Welcome",
        intro=f"{resolved_name[:80]} serves {resolved_city[:80]} with residential remodeling craftsmanship.",
        phone="(928) 555-0148",
        email="projects@example.com",
        color="slate",
        services=[
            "Custom Kitchen Remodeling",
            "Walk-In Tile Showers & Baths",
            "Structural Room Additions",
            "Cabinetry & Countertop Installation",
        ],
        widgets=[default_widget_for_industry(resolved_industry)],
    )
    proposal = build_deterministic_visual_proposal(
        card,
        assets,
        industry=resolved_industry,
    )

    now = time.time()
    session_id = secrets.token_hex(16)
    session = VisualPreviewSession(
        session_id=session_id,
        created_at_epoch=now,
        expires_at_epoch=now + DEFAULT_VISUAL_SESSION_REGISTRY.ttl_seconds,
        is_synthetic_fixture=is_synthetic,
        inspected_url=effective_url,
        profile=profile,
        card=card,
        assets=assets,
        proposal=proposal,
    )
    DEFAULT_VISUAL_SESSION_REGISTRY.put(session)
    return session


def _render_dev_control_banner_html(
    *,
    csrf_token: str,
    session: VisualPreviewSession | None,
    ingested_media: dict[str, IngestedPreviewMedia],
) -> str:
    """Render the top workflow bar for Stage 1 (Inspect) and Stage 2 (Owner Attestation)."""
    _e = html.escape
    notice_html = (
        f'<div class="mf-dev-alert" role="alert" data-mf-dev-notice="true">{_e(session.last_notice)}</div>'
        if session and session.last_notice
        else ""
    )

    inspect_form_html = (
        '<div class="mf-dev-card">'
        '<span class="mf-dev-status-pill">STAGE 1: STATIC HTML &amp; BRAND ASSET DISCOVERY</span>'
        "<h2>Inspect Existing Website or Load Built-In Contractor Fixture</h2>"
        '<form method="post" action="/dev/visual-preview/inspect" data-mf-inspect-form="true">'
        f'<input type="hidden" name="csrf_token" value="{_e(csrf_token)}">'
        '<div class="mf-dev-field">'
        '<label for="mf-demo-mode">Demonstration Source</label>'
        '<select id="mf-demo-mode" name="mode">'
        '<option value="synthetic_fixture">Built-In Synthetic Contractor Fixture (Granite Peak Remodeling — Offline Safe)</option>'
        '<option value="live_url">Public http(s):// Business Website URL (SSRF-Pinned Static Scan)</option>'
        "</select>"
        "</div>"
        '<div class="mf-dev-field">'
        '<label for="mf-demo-url">Public Website URL (used when Public URL mode is selected)</label>'
        f'<input type="url" id="mf-demo-url" name="target_url" value="{_e(session.inspected_url if session else SYNTHETIC_FIXTURE_URL)}" placeholder="https://example.com">'
        "</div>"
        '<div class="mf-dev-field">'
        '<label for="mf-demo-name">Business Name</label>'
        f'<input type="text" id="mf-demo-name" name="business_name" value="{_e(session.profile.name if session else "Granite Peak Kitchen & Bath Remodeling")}">'
        "</div>"
        '<button type="submit" class="mf-dev-btn" data-mf-inspect-submit="true">'
        "Run Stage 1 Discovery (No Media Copied Yet)"
        "</button>"
        "</form>"
        "</div>"
    )

    if session is None:
        stage2_html = (
            '<div class="mf-dev-card">'
            '<span class="mf-dev-status-pill">STAGE 2: OWNER MEDIA ATTESTATION</span>'
            "<h2>Attest Rights to Render Brand Logo &amp; Hero Photography</h2>"
            "<p>Run Stage 1 first to discover candidate brand assets. MeasuredField never downloads or stores "
            "full third-party images until the business owner explicitly attests usage rights.</p>"
            "</div>"
        )
    else:
        safe_assets = [
            a
            for a in session.assets
            if a.verification_state in ("sniffed_safe_image", "discovered_html") and a.license_state != "rejected"
        ]
        logo_options = ['<option value="">-- None / Keep Text Header --</option>']
        hero_options = ['<option value="">-- None / Keep Text Hero --</option>']
        for asset in safe_assets:
            dims = (
                f" ({asset.dimensions.width}x{asset.dimensions.height}px)"
                if asset.dimensions
                else ""
            )
            label = f"{asset.id}: {asset.kind}{dims} — {asset.alt_text or asset.discovery_source}"
            sel_logo = (
                ' selected="selected"'
                if asset.id == session.proposal.selected_logo_asset_id
                else ""
            )
            sel_hero = (
                ' selected="selected"'
                if asset.id == session.proposal.selected_hero_asset_id
                else ""
            )
            logo_options.append(f'<option value="{_e(asset.id)}"{sel_logo}>{_e(label)}</option>')
            hero_options.append(f'<option value="{_e(asset.id)}"{sel_hero}>{_e(label)}</option>')

        ingested_summary = (
            f"{len(ingested_media)} attested raster asset(s) re-encoded &amp; active in After preview."
            if ingested_media
            else "0 assets ingested yet — candidate assets are shown as metadata-only until owner attestation."
        )
        stage2_html = (
            '<div class="mf-dev-card">'
            '<span class="mf-dev-status-pill">STAGE 2: OWNER MEDIA ATTESTATION &amp; SAFE RASTER INGESTION</span>'
            "<h2>Authorize Brand Media for Temporary After Preview</h2>"
            f'<p data-mf-ingestion-summary="true"><strong>Current Media State:</strong> {ingested_summary}</p>'
            '<form method="post" action="/dev/visual-preview/attest" data-mf-attest-form="true">'
            f'<input type="hidden" name="csrf_token" value="{_e(csrf_token)}">'
            f'<input type="hidden" name="session_id" value="{_e(session.session_id)}">'
            '<div class="mf-dev-field">'
            '<label for="mf-select-logo">Header Logo Candidate</label>'
            f'<select id="mf-select-logo" name="logo_asset_id">{"".join(logo_options)}</select>'
            "</div>"
            '<div class="mf-dev-field">'
            '<label for="mf-select-hero">Hero Photograph Candidate</label>'
            f'<select id="mf-select-hero" name="hero_asset_id">{"".join(hero_options)}</select>'
            "</div>"
            '<label class="mf-dev-checkbox-label" for="mf-owner-rights-checkbox">'
            '<input type="checkbox" id="mf-owner-rights-checkbox" name="owner_rights_confirmed" value="yes" required>'
            "<span>I am the business owner or authorized representative and confirm MeasuredField may "
            "download, strip metadata, and re-encode the selected logo/photo for this temporary local preview.</span>"
            "</label>"
            '<button type="submit" class="mf-dev-btn mf-dev-btn-secondary" data-mf-attest-submit="true">'
            "Confirm Owner Rights &amp; Render Real Brand Media in After Page"
            "</button>"
            "</form>"
            "</div>"
        )

    return (
        '<section class="mf-dev-control-banner" aria-label="Local visual preview demo controls">'
        '<div class="wrap">'
        "<h1>MeasuredField Development Visual Preview Studio (MF-016 Local Demo)</h1>"
        "<p>Development-only owner-branded Before/After demonstration. Zero live Gemini API calls, "
        "zero third-party image hotlinks, and zero persistence outside this temporary preview session.</p>"
        f"{notice_html}"
        '<div class="mf-dev-forms-grid">'
        f"{inspect_form_html}"
        f"{stage2_html}"
        "</div>"
        "</div>"
        "</section>"
    )


def _compose_session_page_bundle(
    session: VisualPreviewSession,
    *,
    csrf_token: str,
    media_store: PreviewMediaStore = DEFAULT_PREVIEW_MEDIA_STORE,
) -> dict[str, Any]:
    ingested_by_asset = media_store.list_session_media(session.session_id)
    bundle = compose_interactive_visual_preview(
        session.profile,
        session.card,
        session.assets,
        session.proposal,
        owner_authorized_asset_ids=set(ingested_by_asset.keys()),
        ingested_media_by_asset_id=ingested_by_asset,
        asset_base=f"/dev/visual-preview/assets/{session.session_id}/",
    )
    banner_html = _render_dev_control_banner_html(
        csrf_token=csrf_token,
        session=session,
        ingested_media=ingested_by_asset,
    )
    full_html = bundle["html"].replace(
        "<body>",
        "<body>" + banner_html,
        1,
    )
    full_css = bundle["css"] + "\n" + DEV_STUDIO_BAR_CSS + "\n"
    return {
        **bundle,
        "html": full_html,
        "css": full_css,
    }


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def visual_preview_home(
    request: Request,
    _: None = Depends(_require_visual_demo_allowed),
) -> HTMLResponse:
    """Render the local visual preview studio landing page with a ready-to-browse Stage 1 fixture."""
    cookie_sid = _get_or_create_cookie_sid(request)
    csrf_token = _compute_csrf_token(cookie_sid)
    session = _create_inspection_session(
        mode="synthetic_fixture",
        target_url=SYNTHETIC_FIXTURE_URL,
        business_name="Granite Peak Kitchen & Bath Remodeling",
        city="Prescott, AZ",
        industry="remodeling",
    )
    bundle = _compose_session_page_bundle(session, csrf_token=csrf_token)
    response = HTMLResponse(content=bundle["html"], status_code=200)
    response.headers["Content-Security-Policy"] = DEV_STUDIO_CSP
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        key=_COOKIE_NAME,
        value=cookie_sid,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/inspect")
def inspect_for_visual_preview(
    request: Request,
    csrf_token: str = Form(default=""),
    mode: str = Form(default="synthetic_fixture"),
    target_url: str = Form(default=SYNTHETIC_FIXTURE_URL),
    business_name: str = Form(default="Granite Peak Kitchen & Bath Remodeling"),
    city: str = Form(default="Prescott, AZ"),
    industry: str = Form(default="remodeling"),
    _: None = Depends(_require_visual_demo_allowed),
) -> Response:
    """Stage 1: Run SSRF-safe static HTML diagnostics & candidate asset discovery (zero media stored)."""
    _enforce_rate_limit(request)
    cookie_sid = _verify_csrf_token(request, csrf_token)
    if mode not in {"synthetic_fixture", "live_url"}:
        raise HTTPException(status_code=400, detail="Invalid inspection mode.")

    try:
        session = _create_inspection_session(
            mode=mode,
            target_url=target_url,
            business_name=business_name,
            city=city,
            industry=industry,
        )
    except (SSRFBlockedError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = RedirectResponse(
        url=f"/dev/visual-preview/session/{session.session_id}",
        status_code=303,
    )
    response.set_cookie(
        key=_COOKIE_NAME,
        value=cookie_sid,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/attest")
def attest_and_ingest_brand_media(
    request: Request,
    csrf_token: str = Form(default=""),
    session_id: str = Form(default=""),
    logo_asset_id: str = Form(default=""),
    hero_asset_id: str = Form(default=""),
    owner_rights_confirmed: str = Form(default=""),
    _: None = Depends(_require_visual_demo_allowed),
) -> Response:
    """Stage 2: Verify explicit owner attestation and safely download, strip metadata, and re-encode raster media."""
    _enforce_rate_limit(request)
    cookie_sid = _verify_csrf_token(request, csrf_token)
    session = DEFAULT_VISUAL_SESSION_REGISTRY.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Visual preview session not found or expired.")

    if owner_rights_confirmed.strip().lower() != "yes":
        raise HTTPException(
            status_code=400,
            detail="Explicit owner rights attestation (owner_rights_confirmed='yes') is required before copying or storing candidate brand media.",
        )

    safe_assets_by_id = {
        a.id: a
        for a in session.assets
        if a.verification_state in ("sniffed_safe_image", "discovered_html") and a.license_state != "rejected"
    }

    selections: list[tuple[str, str]] = []
    if logo_asset_id.strip():
        if logo_asset_id.strip() not in safe_assets_by_id:
            raise HTTPException(status_code=400, detail="Selected logo asset is not a verified safe candidate.")
        selections.append((logo_asset_id.strip(), "header_logo"))
    if hero_asset_id.strip():
        if hero_asset_id.strip() not in safe_assets_by_id:
            raise HTTPException(status_code=400, detail="Selected hero asset is not a verified safe candidate.")
        selections.append((hero_asset_id.strip(), "hero_visual"))

    if not selections:
        raise HTTPException(status_code=400, detail="Select at least one candidate asset to attest and ingest.")

    transport = _build_synthetic_fixture_transport() if session.is_synthetic_fixture else None
    dns_resolver = _synthetic_dns_resolver if session.is_synthetic_fixture else None

    ingested_count = 0
    for asset_id, slot_role in selections:
        candidate = safe_assets_by_id[asset_id]
        attestation = OwnerMediaAttestation(
            asset_id=candidate.id,
            source_page=candidate.source_page,
            source_url=candidate.source_url,
            slot_role=slot_role,  # type: ignore[arg-type]
            owner_attested_rights=True,
        )
        try:
            download_and_ingest_attested_media(
                candidate,
                attestation,
                session_id=session.session_id,
                store=DEFAULT_PREVIEW_MEDIA_STORE,
                resolver=dns_resolver,
                transport=transport,
            )
            session.assets = [
                c.model_copy(update={"license_state": "owner_attested"})
                if c.id == candidate.id
                else c
                for c in session.assets
            ]
            session.attestations.append(attestation)
            if slot_role == "header_logo":
                session.proposal = session.proposal.model_copy(
                    update={"selected_logo_asset_id": candidate.id}
                )
            elif slot_role == "hero_visual":
                session.proposal = session.proposal.model_copy(
                    update={"selected_hero_asset_id": candidate.id}
                )
            ingested_count += 1
        except (PreviewMediaError, SSRFBlockedError) as exc:
            session.last_notice = f"Media ingestion rejected for {candidate.id}: {exc}"
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.last_notice = (
        f"Owner attestation recorded: {ingested_count} brand asset(s) safely decoded, stripped of metadata, "
        f"and re-encoded into temporary same-origin storage."
    )
    response = RedirectResponse(
        url=f"/dev/visual-preview/session/{session.session_id}",
        status_code=303,
    )
    response.set_cookie(
        key=_COOKIE_NAME,
        value=cookie_sid,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/session/{session_id}", response_class=HTMLResponse)
def view_visual_preview_session(
    session_id: str,
    request: Request,
    _: None = Depends(_require_visual_demo_allowed),
) -> HTMLResponse:
    """Stage 3: Render the browsable Before/After visual preview studio for `session_id`."""
    session = DEFAULT_VISUAL_SESSION_REGISTRY.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Visual preview session not found or expired.")
    cookie_sid = _get_or_create_cookie_sid(request)
    csrf_token = _compute_csrf_token(cookie_sid)
    bundle = _compose_session_page_bundle(session, csrf_token=csrf_token)
    response = HTMLResponse(content=bundle["html"], status_code=200)
    response.headers["Content-Security-Policy"] = DEV_STUDIO_CSP
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        key=_COOKIE_NAME,
        value=cookie_sid,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/assets/{session_id}/style.css")
def serve_visual_preview_css(
    session_id: str,
    request: Request,
    _: None = Depends(_require_visual_demo_allowed),
) -> Response:
    """Serve the external stylesheet for `session_id` (enforcing zero inline styles)."""
    session = DEFAULT_VISUAL_SESSION_REGISTRY.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Visual preview session not found or expired.")
    cookie_sid = _get_or_create_cookie_sid(request)
    csrf_token = _compute_csrf_token(cookie_sid)
    bundle = _compose_session_page_bundle(session, csrf_token=csrf_token)
    return Response(
        content=bundle["css"],
        media_type="text/css; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/assets/{session_id}/site.js")
def serve_visual_preview_js(
    session_id: str,
    _: None = Depends(_require_visual_demo_allowed),
) -> Response:
    """Serve the deterministic local interaction script for `session_id`."""
    session = DEFAULT_VISUAL_SESSION_REGISTRY.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Visual preview session not found or expired.")
    cookie_sid = "0" * 32
    bundle = _compose_session_page_bundle(session, csrf_token=cookie_sid)
    return Response(
        content=bundle["js"],
        media_type="application/javascript; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/media/{session_id}/{media_token}")
def serve_temporary_preview_media(
    session_id: str,
    media_token: str,
    _: None = Depends(_require_visual_demo_allowed),
) -> Response:
    """Serve clean re-encoded raster bytes for an owner-attested temporary brand asset."""
    if not _SESSION_ID_RE.match(session_id or "") or not _TOKEN_RE.match(media_token or ""):
        raise HTTPException(status_code=404, detail="Media token not found.")
    session = DEFAULT_VISUAL_SESSION_REGISTRY.get(session_id)
    if session is None:
        DEFAULT_PREVIEW_MEDIA_STORE.purge_session(session_id)
        raise HTTPException(status_code=404, detail="Visual preview session expired.")

    record = DEFAULT_PREVIEW_MEDIA_STORE.get_media(session_id, media_token)
    if record is None:
        raise HTTPException(status_code=404, detail="Temporary preview media not found or expired.")

    meta, clean_bytes = record
    return Response(
        content=clean_bytes,
        media_type=meta.content_type,
        headers={
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
