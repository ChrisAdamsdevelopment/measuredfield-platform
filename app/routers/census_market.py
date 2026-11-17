"""MF-074: authenticated, explicit opt-in Census market snapshots for owners.

All external Census calls are read-only public aggregates and occur only after
a CSRF-protected POST. No key, request URL with key, or individual business data
is ever passed into the browser.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request

from ..db import TenantSession, get_db
from ..deps import Principal, member, member_post
from ..security import rate_limited
from ..services.census_market import (
    CensusMarketClient, CensusMarketError, CensusObservation, CensusQuery,
)
from .workspace import _page

router = APIRouter()

# These FIPS place codes were read from Census ACS5 2024 place:* data,
# and the selected ZIPs are separately labeled, not assumed to equal towns.
@dataclass(frozen=True)
class MarketArea:
    slug: str
    title: str
    place_fips: str
    business_zip: str
    county_fips: str = "025"
    state_fips: str = "04"
    county_name: str = "Yavapai County"


AREAS = {
    a.slug: a for a in (
        MarketArea("prescott-valley", "Prescott Valley", "57450", "86314"),
        MarketArea("prescott", "Prescott", "57380", "86301"),
        MarketArea("chino-valley", "Chino Valley", "12840", "86323"),
    )
}
# Public statistics may be cached between tenants. Nothing private is cached.
_CENSUS = CensusMarketClient(max_calls=500, ttl_seconds=3600)


def _read_requests(area: MarketArea) -> tuple[tuple[str, CensusQuery], ...]:
    return (
        ("town", CensusQuery(
            "acs_place", state=area.state_fips, place=area.place_fips,
        )),
        ("zcta", CensusQuery("acs_zcta", zip_code=area.business_zip)),
        ("county_employers", CensusQuery(
            "cbp_county", state=area.state_fips, county=area.county_fips,
        )),
        ("county_remodelers", CensusQuery(
            "cbp_county", state=area.state_fips, county=area.county_fips,
            naics="236118",
        )),
        ("zip_employers", CensusQuery("cbp_zip", zip_code=area.business_zip)),
        ("county_nonemployers", CensusQuery(
            "nonemp_county", state=area.state_fips, county=area.county_fips,
        )),
    )


def _metric(observation: CensusObservation | None, field: str) -> dict[str, Any]:
    if observation is None:
        return {"value": None, "availability": "not_loaded", "unit": "", "year": None}
    for metric in observation.metrics:
        if metric.field == field:
            return {
                "value": metric.value,
                "availability": metric.availability,
                "unit": metric.unit,
                "year": observation.reference_year,
            }
    return {"value": None, "availability": "not_available", "unit": "", "year": None}


def _snapshot(area: MarketArea) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Six bounded official Census requests, but preserve partial failures."""
    records: dict[str, CensusObservation | None] = {}
    missing: list[str] = []
    for label, query in _read_requests(area):
        try:
            result = _CENSUS.query(query)
            if len(result.observations) != 1:
                raise CensusMarketError("Census returned no matching geographic record")
            records[label] = result.observations[0]
        except CensusMarketError:
            records[label] = None
            missing.append(label)
    summary = {
        "area": area,
        "records": records,
        "cards": [
            {"label": "Town population", "detail": "ACS place · 2024 five-year",
             "stat": _metric(records["town"], "B01003_001E"), "geography": "Census place"},
            {"label": "Town households", "detail": "ACS place · 2024 five-year",
             "stat": _metric(records["town"], "B11001_001E"), "geography": "Census place"},
            {"label": "Construction employers", "detail": "County · CBP 2023",
             "stat": _metric(records["county_employers"], "ESTAB"), "geography": "County"},
            {"label": "Construction nonemployers", "detail": "County · NES 2024",
             "stat": _metric(records["county_nonemployers"], "NESTAB"), "geography": "County"},
        ],
        "details": [
            {"label": "Construction employer establishments in ZIP",
             "stat": _metric(records["zip_employers"], "ESTAB"),
             "scope": f"Business ZIP {area.business_zip}", "dataset": "CBP · 2023"},
            {"label": "Residential-remodeler employer establishments in county",
             "stat": _metric(records["county_remodelers"], "ESTAB"),
             "scope": area.county_name, "dataset": "CBP · 2023 · NAICS 236118"},
            {"label": "Population in the ZIP Code Tabulation Area",
             "stat": _metric(records["zcta"], "B01003_001E"),
             "scope": f"ACS ZCTA {area.business_zip}", "dataset": "ACS 5-year · 2024"},
            {"label": "Households in the ZIP Code Tabulation Area",
             "stat": _metric(records["zcta"], "B11001_001E"),
             "scope": f"ACS ZCTA {area.business_zip}", "dataset": "ACS 5-year · 2024"},
            {"label": "County employer payroll",
             "stat": _metric(records["county_employers"], "PAYANN"),
             "scope": area.county_name, "dataset": "CBP · 2023 · $1,000"},
            {"label": "County nonemployer gross receipts",
             "stat": _metric(records["county_nonemployers"], "NRCPTOT"),
             "scope": area.county_name, "dataset": "NES · 2024 · $1,000"},
        ],
        "sources": tuple(
            (label, record) for label, record in records.items() if record is not None
        ),
    }
    return summary, tuple(missing)


def _render(request: Request, db: TenantSession, p: Principal, *,
            chosen: str = "prescott-valley", snapshot: dict | None = None,
            error: str | None = None, unavailable: tuple[str, ...] = ()):
    return _page(request, db, p, "app/census_market.html", {
        "market_areas": AREAS,
        "market_selected": chosen,
        "market_snapshot": snapshot,
        "market_error": error,
        "market_unavailable": unavailable,
        "census_live_installed": True,
    })


@router.get("/app/market-intelligence")
def market_intelligence_page(
    request: Request,
    db: TenantSession = Depends(get_db),
    p: Principal = Depends(member()),
):
    """Page load is offline: no Census calls or owner's key exposure."""
    return _render(request, db, p)


@router.post("/app/market-intelligence/run")
def market_intelligence_run(
    request: Request,
    area: str = Form(""),
    confirm: str = Form(""),
    db: TenantSession = Depends(get_db),
    p: Principal = Depends(member_post("read")),
):
    if confirm != "LOAD PUBLIC CENSUS DATA":
        raise HTTPException(422, "Confirm before requesting a public Census snapshot")
    if area not in AREAS:
        raise HTTPException(422, "Choose a supported market area")
    # Signed-in personal + workspace-level limits across web instances (PostgreSQL).
    if rate_limited(db, "census-owner:" + str(p.user.id), limit=15, window_seconds=3600):
        return _render(request, db, p, chosen=area, error=(
            "Hourly Census refresh limit reached. Previously loaded data may still be cached."
        ))
    if rate_limited(db, "census-workspace:" + str(p.org.id), limit=40, window_seconds=3600):
        return _render(request, db, p, chosen=area, error=(
            "Workspace Census refresh limit reached. Try again later."
        ))
    summary, missing = _snapshot(AREAS[area])
    if len(missing) == len(_read_requests(AREAS[area])):
        return _render(request, db, p, chosen=area, error=(
            "Census data is unavailable or the server key is not configured. "
            "No business information was modified."
        ), unavailable=missing)
    return _render(request, db, p, chosen=area, snapshot=summary, unavailable=missing)
