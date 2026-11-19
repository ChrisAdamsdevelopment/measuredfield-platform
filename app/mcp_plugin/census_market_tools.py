"""MF-075: tenant-authorized, read-only Census tools for OAuth MCP.

No AI calls and no owner/customer information goes to Census. A read-only
tool may increment only its own server-side rate-limit accounting rows.
"""
from __future__ import annotations

import uuid
from typing import Any

from app.db import new_session
from app.mcp_connector.gateway import (
    ConnectorAccessDenied, ReadOnlyBusinessGateway, VerifiedOAuthIdentity,
)
from app.routers import census_market as market
from app.security import rate_limited

MARKET_CAUTION = (
    "Official historical aggregate statistics, not current named competitors, "
    "Google rankings, licensed-contractor totals, or a prediction. Census places, "
    "postal ZIPs, business ZIPs, ZIP Code Tabulation Areas and counties differ."
)


def available_markets(
    *, identity: VerifiedOAuthIdentity, business_id: str,
    gateway: ReadOnlyBusinessGateway,
) -> dict[str, Any]:
    """List fixed choices after actual tenant membership is checked."""
    gateway.overview_for(identity, business_id)
    return {
        "business_id": business_id,
        "markets": [
            {"id": area.slug, "name": area.title,
             "town_geography": "Census place",
             "business_zip": area.business_zip,
             "acs_geography": "ZIP Code Tabulation Area; not postal ZIP",
             "county_name": area.county_name}
            for area in market.AREAS.values()
        ],
        "coverage": "Prescott-region pilot only",
        "note": MARKET_CAUTION,
    }


def _consume_owner_quota(
    *, identity: VerifiedOAuthIdentity, business_id: str,
    gateway: ReadOnlyBusinessGateway,
) -> None:
    """Server-side atomic PG quota, with tenant permission before accounting."""
    user_id = gateway._user(identity)
    try:
        org_id = uuid.UUID(str(business_id))
    except (ValueError, TypeError, AttributeError):
        raise ConnectorAccessDenied("Business is unavailable") from None

    db = new_session()
    try:
        db.set_context(user_id=user_id)
        gateway._active_user(db, user_id)
        gateway._enter_business(db, user_id, org_id)
        user_limited = rate_limited(
            db, "mcp-census-user:" + str(user_id), limit=15, window_seconds=3600,
        )
        org_limited = rate_limited(
            db, "mcp-census-org:" + str(org_id), limit=40, window_seconds=3600,
        )
        db.commit()
        if user_limited or org_limited:
            raise ConnectorAccessDenied("Hourly market refresh limit reached")
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _observation(label: str, record: Any) -> dict[str, Any]:
    """Return source-safe normalized evidence with per-metric availability."""
    if record is None:
        return {"source_id": label, "availability": "not_loaded",
                "metrics": [], "source_url": None}
    return {
        "source_id": label,
        "dataset": record.dataset,
        "reference_year": record.reference_year,
        "data_class": "official_public_aggregate",
        "geography_kind": record.geography_kind,
        "geography_name": record.name,
        "naics_code": record.naics_code,
        "naics_edition": record.naics_edition,
        "source_url": record.source_url,
        "retrieved_at_utc": record.fetched_at_utc,
        "caveats": list(record.caveats),
        "availability": "observed_or_partial",
        "metrics": [
            {
                "code": stat.field,
                "label": stat.description,
                "value": stat.value,
                "unit": stat.unit,
                "availability": stat.availability,
            }
            for stat in record.metrics
        ],
    }


def market_snapshot(
    *, identity: VerifiedOAuthIdentity, business_id: str, area_slug: str,
    gateway: ReadOnlyBusinessGateway,
) -> dict[str, Any]:
    """Fixed public Census queries after account link, membership and quota."""
    if area_slug not in market.AREAS:
        raise ValueError("Select one of the supported market areas")
    _consume_owner_quota(
        identity=identity, business_id=business_id, gateway=gateway,
    )
    area = market.AREAS[area_slug]
    summary, unavailable = market._snapshot(area)
    return {
        "business_id": str(uuid.UUID(str(business_id))),
        "selected_market": {"id": area.slug, "name": area.title,
                            "census_place_fips": area.place_fips,
                            "business_zip": area.business_zip,
                            "county": area.county_name},
        "evidence_type": "official_public_aggregate",
        "source_records": [
            _observation(label, obs) for label, obs in summary["records"].items()
        ],
        "unavailable_sources": list(unavailable),
        "partial_result": bool(unavailable),
        "note": MARKET_CAUTION,
    }
