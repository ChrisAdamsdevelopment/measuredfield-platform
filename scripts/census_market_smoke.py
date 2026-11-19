"""MF-073: explicit opt-in, read-only official Census public aggregate smoke.

Usage: python -B scripts/census_market_smoke.py --live
This DOES NOT save, print, upload, or pass Census API credentials to a model.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.census_market import (  # noqa: E402
    CensusMarketClient, CensusQuery, CensusMarketError,
)


CASES = (
    ("2023 Yavapai construction employers", CensusQuery(
        "cbp_county", state="04", county="025",
    )),
    ("2023 ZIP 86314 construction employers", CensusQuery(
        "cbp_zip", zip_code="86314",
    )),
    ("2024 Yavapai construction nonemployers", CensusQuery(
        "nonemp_county", state="04", county="025",
    )),
    ("2024 ACS Arizona places", CensusQuery(
        "acs_places", state="04",
    )),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safe Census-only public market data check")
    parser.add_argument("--live", action="store_true", help="Authorize four external Census GET requests")
    args = parser.parse_args(argv)
    if not args.live:
        print("CENSUS_SMOKE_NOT_RUN: no network without explicit --live")
        return 2
    service = CensusMarketClient(max_calls=4, ttl_seconds=3600)
    completed = 0
    try:
        for title, query in CASES:
            result = service.query(query)
            rows = result.observations
            if query.product == "acs_places":
                rows = tuple(r for r in rows if r.name == "Prescott Valley town, Arizona")
            if len(rows) != 1:
                print("CENSUS_SMOKE_FAIL", query.product, "expected_one_result", len(rows))
                return 1
            row = rows[0]
            print("SOURCE", title, "|", row.name, "|",
                  row.dataset, row.reference_year, "|",
                  row.geography_kind, row.geography_code)
            for metric in row.metrics:
                print("  PUBLIC_AGGREGATE", metric.field, metric.value,
                      metric.unit, metric.availability)
            if "key=" in row.source_url:
                print("CENSUS_SMOKE_FAIL leaked_source_key_parameter")
                return 1
            completed += 1
    except CensusMarketError as exc:
        print("CENSUS_SMOKE_FAIL", type(exc).__name__, str(exc))
        return 1
    print("CENSUS_MARKET_LIVE_SMOKE_PASS", "queries", completed,
          "key_never_logged", "true", "data_public_aggregates_only", "true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
