"""Private, read-only Lead Rescue insights; never sends messages or makes claims of sales."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select

from ..config import get_settings
from ..db import TenantSession, get_db
from ..deps import Principal, member, render
from ..models import Lead
from ..services.lead_rescue import OPEN_STATUSES, priority_case, summarize
from .auth import list_orgs

router = APIRouter()
MAX_LEADS_REVIEWED = 500


def _business_zone(settings) -> tuple[str, ZoneInfo]:
    name = settings.get("timezone", "UTC") if isinstance(settings, dict) else "UTC"
    if not isinstance(name, str) or len(name) > 80:
        name = "UTC"
    try:
        return name, ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC", ZoneInfo("UTC")


@router.get("/app/lead-rescue")
def lead_rescue_page(
    request: Request, db: TenantSession = Depends(get_db),
    p: Principal = Depends(member()),
):
    # A paid/entitled public release requires a separate authorization gate.
    if get_settings().is_production:
        raise HTTPException(404, "Lead Rescue preview not enabled.")
    zone_name, zone = _business_zone(p.org.settings)
    now = datetime.now(timezone.utc).astimezone(zone)
    today = now.date()
    conditions = (
        Lead.org_id == p.org.id,
        Lead.status.in_(tuple(OPEN_STATUSES)),
    )
    total_open = db.scalar(
        select(func.count()).select_from(Lead).where(*conditions)
    ) or 0
    # SQL urgency ordering ensures the top 500 prioritizes overdue records,
    # not merely recent inquiries. Older nonurgent records may be omitted.
    leads = db.scalars(
        select(Lead).where(*conditions)
        .order_by(
            priority_case(today, now - timedelta(hours=24)),
            Lead.created_at.asc(),
        ).limit(MAX_LEADS_REVIEWED)
    ).all()
    report = summarize(leads, as_of=now, total_open=total_open, maximum=MAX_LEADS_REVIEWED)
    return render(request, "app/lead_rescue.html", {
        "org": p.org, "role": p.role, "orgs": list_orgs(db, p.user),
        "report": report, "timezone_name": zone_name,
        "can": p.can,
    })
