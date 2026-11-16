"""Self-service account-linked ChatGPT connection list and immediate disconnect.

These are USER-owned OAuth identity links, not business-wide computer grants.
No ChatGPT plugin can create links or bypass current workspace user auth.
"""
from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from ..db import TenantSession, get_db
from ..deps import Principal, back, get_principal, member, member_post, parse_uuid
from ..models import McpIdentityLink, now
from ..security import csrf_ok
from .workspace import _page

router = APIRouter()


def _own_links(db: TenantSession, p: Principal) -> list[McpIdentityLink]:
    return db.scalars(select(McpIdentityLink).where(
        McpIdentityLink.user_id == p.user.id).order_by(
            McpIdentityLink.linked_at.desc(), McpIdentityLink.id.desc()
        ).limit(100)).all()


def _owner_link(db: TenantSession, p: Principal, raw_id: str) -> McpIdentityLink:
    row = db.scalar(select(McpIdentityLink).where(
        McpIdentityLink.id == parse_uuid(raw_id),
        McpIdentityLink.user_id == p.user.id).with_for_update())
    if row is None:
        # Never reveal whether another user's connection exists.
        raise HTTPException(404, "Connection not found.")
    return row


def _public_link(row: McpIdentityLink) -> dict:
    provider_host = urlsplit(row.issuer).hostname or "Identity provider"
    return {
        "id": str(row.id),
        "provider": provider_host,
        "status": "disconnected" if row.revoked_at else "connected",
        "linked_at": row.linked_at.isoformat(),
        "disconnected_at": row.revoked_at.isoformat() if row.revoked_at else None,
    }


def _revoke(db: TenantSession, p: Principal, link_id: str) -> tuple[McpIdentityLink, bool]:
    link = _owner_link(db, p, link_id)
    if link.revoked_at is not None:
        return link, False
    # The active token is invalidated on its NEXT server call by the MF-037
    # identity resolver, which checks revoked_at for every use.
    link.revoked_at = now()
    db.flush()
    return link, True


@router.get("/app/connections")
def connections_page(request: Request, db: TenantSession = Depends(get_db),
                     p: Principal = Depends(member())):
    """Ordinary accessible owner page, scoped to the signed-in PERSON."""
    return _page(request, db, p, "app/mcp_connections.html", {
        "connections": [_public_link(row) for row in _own_links(db, p)],
    })


@router.post("/app/connections/{link_id}/disconnect")
def disconnect_page(link_id: str, confirm: str = Form(default=""),
                    db: TenantSession = Depends(get_db),
                    p: Principal = Depends(member_post("read"))):
    if confirm != "DISCONNECT":
        return back("/app/connections", err="Confirm the account disconnect first.")
    row, changed = _revoke(db, p, link_id)
    return back("/app/connections", ok=(
        "ChatGPT account disconnected. Existing tokens can no longer read your business."
        if changed else "This account is already disconnected."
    ))


def _personal_api(request: Request, p: Principal | None = Depends(get_principal),
                  x_csrf_token: str | None = Header(default=None)) -> Principal:
    if p is None:
        raise HTTPException(401, "Sign in required.")
    if request.method not in ("GET", "HEAD") and not csrf_ok(p.session, x_csrf_token):
        raise HTTPException(403, "A valid account security confirmation is required.")
    return p


class DisconnectRequest(BaseModel):
    confirm: Literal["DISCONNECT CHATGPT"]


@router.get("/api/v1/mcp/account-links")
def list_account_links(db: TenantSession = Depends(get_db),
                       p: Principal = Depends(_personal_api)):
    return {
        "connections": [_public_link(row) for row in _own_links(db, p)],
        "owner_managed": True,
        "live_plugin_installation_enabled": False,
    }


@router.post("/api/v1/mcp/account-links/{link_id}/disconnect")
def disconnect_account_link(link_id: str, body: DisconnectRequest,
                            db: TenantSession = Depends(get_db),
                            p: Principal = Depends(_personal_api)):
    row, changed = _revoke(db, p, link_id)
    return {
        "disconnected_now": changed,
        "connection": _public_link(row),
        "no_other_account_modified": True,
    }
