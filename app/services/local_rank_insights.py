"""Owner-imported local SERP comparison and deterministic next-step advice.

MF-049 is an OFFLINE, ephemeral preview. There is no provider client, claim of
verified rank, automated Google query, billing, database write or AI inference.
The same provenance standard applies to every displayed numeric comparison.
"""
from __future__ import annotations

import csv
import io
import math
from collections import defaultdict
from datetime import datetime
from typing import Any

REQUIRED = {
    "city", "keyword", "surface", "latitude", "longitude", "position",
    "competitor", "competitor_position", "checked_at", "provider",
}
SURFACES = {"organic", "local_pack"}
MAX_ROWS = 500
MAX_BYTES = 320_000


class LocalRankInputError(ValueError):
    """Source CSV was ambiguous or outside allowed bounds."""


def _text(raw: str | None, *, field: str, size: int = 120, required: bool = True) -> str:
    if raw is None:
        raise LocalRankInputError(f"Missing {field}.")
    value = raw.strip()
    if (required and not value) or len(value) > size or any(ord(c) < 32 for c in value):
        raise LocalRankInputError(f"Invalid {field}; use plain text under {size} characters.")
    return value


def _coordinate(raw: str, *, field: str, lo: float, hi: float) -> float:
    try:
        value = float(raw)
    except (ValueError, TypeError):
        raise LocalRankInputError(f"{field} must be a numeric coordinate.") from None
    if not math.isfinite(value) or value < lo or value > hi:
        raise LocalRankInputError(f"{field} must be a valid geographic coordinate.")
    return round(value, 5)


def _position(raw: str | None, *, field: str) -> int | None:
    value = _text(raw, field=field, size=3, required=False)
    if not value:
        return None  # Missing is UNKNOWN, not a claimed 101+ position.
    if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 100:
        raise LocalRankInputError(f"{field} must be an integer from 1 to 100, or blank.")
    return int(value)


def _timestamp(raw: str | None) -> str:
    value = _text(raw, field="checked_at", size=38)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LocalRankInputError("checked_at must be an ISO 8601 timestamp with timezone.") from None
    if parsed.utcoffset() is None:
        raise LocalRankInputError("checked_at requires a timezone offset.")
    return parsed.isoformat(timespec="seconds")


def _recommendations(markets: list[dict], points: list[dict]) -> list[dict]:
    """Explainable rules, not assertions of why Google ranks a business."""
    actions: list[dict] = []
    for market in markets:
        city, n, known = market["city"], market["samples"], market["ranked_samples"]
        if n < 4 or known < n:
            actions.append({
                "priority": 1,
                "title": f"Complete a dependable baseline for {city}",
                "reason": (
                    f"{known} of {n} imported coordinate/query samples contain a rank. "
                    "Use at least four representative points and repeat the same phrases and search surface."
                ),
                "next_step": "Obtain another dated export for the same queries and coordinates before acting on apparent gaps.",
                "confidence": "needs_more_evidence",
            })
        if known >= 4 and market["top3_share"] is not None and market["top3_share"] < 0.25:
            actions.append({
                "priority": 2,
                "title": f"Review local relevance in {city}",
                "reason": (
                    f"Only {market['top3_count']} of {known} imported ranked samples "
                    "appear in the top three, on the source/surfaces specified by the export."
                ),
                "next_step": (
                    "Review business-category accuracy, city-specific service pages, "
                    "recent customer feedback and listing consistency; verify changes before publishing."
                ),
                "confidence": "owner_imported_only",
            })
        for rival in market["competitors"]:
            if rival["paired_samples"] >= 3 and rival["ahead_share"] >= 0.65:
                actions.append({
                    "priority": 2,
                    "title": f"Investigate the {rival['name']} gap in {city}",
                    "reason": (
                        f"The imported competitor is ahead at {rival['ahead_count']} of "
                        f"{rival['paired_samples']} directly comparable coordinate/query samples."
                    ),
                    "next_step": (
                        "Manually confirm the competitor's identity and inspect service relevance, "
                        "reviews and location-page coverage; do not copy unverified claims."
                    ),
                    "confidence": "owner_imported_only",
                })
    comparable: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for pt in points:
        if pt["position"] is not None:
            comparable[(pt["keyword"].casefold(), pt["surface"])][pt["city"]].append(pt["position"])
    if len(markets) >= 2:
        cities = sorted(m["city"] for m in markets)
        shared = [
            key for key, by_city in comparable.items()
            if all(by_city.get(c) for c in cities)
        ]
        if not shared:
            actions.append({
                "priority": 1,
                "title": "Make the city comparison apples-to-apples",
                "reason": "No keyword and search-surface combination has observed ranks in every selected city.",
                "next_step": "Check matching search phrases, device/source configuration and search surface in each city.",
                "confidence": "needs_more_evidence",
            })
        else:
            averages = {
                city: round(
                    sum(sum(comparable[key][city]) / len(comparable[key][city]) for key in shared)
                    / len(shared), 1
                )
                for city in cities
            }
            worst = max(averages, key=lambda c: averages[c])
            best = min(averages, key=lambda c: averages[c])
            if worst != best and averages[worst] - averages[best] >= 2:
                actions.append({
                    "priority": 3,
                    "title": f"Compare the {worst} experience with {best}",
                    "reason": (
                        f"Across {len(shared)} shared query/surface combinations, imported average "
                        f"rank is {averages[worst]} in {worst} versus {averages[best]} in {best}. "
                        "These are sample means, not Google Search Console position."
                    ),
                    "next_step": (
                        "Inspect whether services, landing pages and listing details are equally relevant "
                        "to both areas; repeat the scan before attributing a cause."
                    ),
                    "confidence": "owner_imported_only",
                })
    if not actions:
        actions.append({
            "priority": 3,
            "title": "Establish a repeatable trend",
            "reason": "This imported snapshot alone cannot establish whether an improvement caused a ranking change.",
            "next_step": "Repeat with the same coordinates, phrases, provider and search surfaces; compare dated snapshots.",
            "confidence": "owner_imported_only",
        })
    return sorted(actions, key=lambda r: (r["priority"], r["title"]))[:12]


def analyze_local_rank_csv(csv_text: str) -> dict[str, Any]:
    """Analyze a user-supplied CSV WITHOUT authenticating its source or fetching URLs."""
    if not isinstance(csv_text, str) or not 1 <= len(csv_text.encode("utf-8")) <= MAX_BYTES:
        raise LocalRankInputError("CSV must be 1 to 320,000 bytes.")
    if "\x00" in csv_text:
        raise LocalRankInputError("Binary data is not accepted.")
    try:
        stream = io.StringIO(csv_text, newline="")
        reader = csv.DictReader(stream, strict=True)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not REQUIRED.issubset(set(fields)):
            raise LocalRankInputError("CSV must include: " + ", ".join(sorted(REQUIRED)))
        if len(fields) > 20:
            raise LocalRankInputError("Too many CSV columns.")
        points: dict[tuple, dict] = {}
        count = 0
        for row in reader:
            count += 1
            if count > MAX_ROWS:
                raise LocalRankInputError("Maximum 500 rows per preview. Export smaller segments.")
            if None in row:
                raise LocalRankInputError("A CSV row has extra columns.")
            city = _text(row["city"], field="city", size=80)
            keyword = _text(row["keyword"], field="keyword", size=160)
            surface = _text(row["surface"], field="surface", size=20)
            if surface not in SURFACES:
                raise LocalRankInputError("Only organic or local_pack ranked SERP sources are supported.")
            lat = _coordinate(row["latitude"], field="latitude", lo=-90, hi=90)
            lon = _coordinate(row["longitude"], field="longitude", lo=-180, hi=180)
            position = _position(row["position"], field="position")
            competitor = _text(row["competitor"], field="competitor", size=100, required=False)
            competitor_pos = _position(row["competitor_position"], field="competitor_position")
            if competitor_pos is not None and not competitor:
                raise LocalRankInputError("A competitor position needs a competitor name.")
            timestamp = _timestamp(row["checked_at"])
            provider = _text(row["provider"], field="provider", size=60)
            key = (city.casefold(), keyword.casefold(), surface, lat, lon, provider.casefold(), timestamp)
            if key not in points:
                points[key] = {
                    "city": city, "keyword": keyword, "surface": surface,
                    "latitude": lat, "longitude": lon, "position": position,
                    "checked_at": timestamp, "provider": provider, "competitors": {},
                }
            pt = points[key]
            if pt["position"] != position:
                raise LocalRankInputError("Conflicting positions at an identical source point.")
            if competitor:
                name = competitor.casefold()
                if name in pt["competitors"] and pt["competitors"][name]["position"] != competitor_pos:
                    raise LocalRankInputError("Conflicting competitor positions at one source point.")
                pt["competitors"][name] = {"name": competitor, "position": competitor_pos}
    except (csv.Error, UnicodeError) as exc:
        raise LocalRankInputError("Invalid CSV encoding or quoting.") from exc
    if count == 0 or not points:
        raise LocalRankInputError("CSV has no observations.")
    if len(points) > 300:
        raise LocalRankInputError("Maximum 300 unique geographic observations per preview.")
    cities = {pt["city"].casefold() for pt in points.values()}
    if len(cities) > 8:
        raise LocalRankInputError("Compare at most eight cities in one preview.")
    normalized_points = sorted(points.values(), key=lambda p: (
        p["city"].casefold(), p["keyword"].casefold(), p["surface"], p["latitude"], p["longitude"],
    ))
    market_groups: dict[str, list[dict]] = defaultdict(list)
    for pt in normalized_points:
        pt["competitors"] = sorted(pt["competitors"].values(), key=lambda a: a["name"].casefold())
        market_groups[pt["city"]].append(pt)
    markets = []
    for city, items in sorted(market_groups.items()):
        ranked = [pt["position"] for pt in items if pt["position"] is not None]
        rivals = defaultdict(lambda: {"name": "", "paired_samples": 0, "ahead_count": 0})
        for pt in items:
            for competitor in pt["competitors"]:
                if competitor["position"] is None or pt["position"] is None:
                    continue
                entry = rivals[competitor["name"].casefold()]
                entry["name"] = competitor["name"]
                entry["paired_samples"] += 1
                if competitor["position"] < pt["position"]:
                    entry["ahead_count"] += 1
        competitors = [
            {**v, "ahead_share": round(v["ahead_count"] / v["paired_samples"], 3)}
            for v in rivals.values() if v["paired_samples"]
        ]
        competitors.sort(key=lambda r: (-r["ahead_count"], r["name"]))
        markets.append({
            "city": city, "samples": len(items), "ranked_samples": len(ranked),
            "unknown_samples": len(items) - len(ranked),
            "average_position": round(sum(ranked) / len(ranked), 1) if ranked else None,
            "top3_count": sum(v <= 3 for v in ranked),
            "top3_share": round(sum(v <= 3 for v in ranked) / len(ranked), 3) if ranked else None,
            "top10_share": round(sum(v <= 10 for v in ranked) / len(ranked), 3) if ranked else None,
            "competitors": competitors[:8],
            "points": [
                {
                    "keyword": p["keyword"], "surface": p["surface"], "lat": p["latitude"],
                    "lon": p["longitude"], "position": p["position"],
                    "checked_at": p["checked_at"], "provider": p["provider"],
                }
                for p in items[:100]
            ],
        })
    return {
        "status": "owner_imported_preview_not_verified",
        "provenance": "owner_imported_not_provider_authenticated",
        "provider_calls_made": 0,
        "google_queries_made": 0,
        "records_imported": count,
        "unique_samples": len(points),
        "markets": markets,
        "recommendations": _recommendations(markets, normalized_points),
        "limitations": [
            "All positions are as reported by an owner-uploaded file, not independently verified.",
            "A missing position means unknown, not absent from search.",
            "Compare the same keyword, SERP surface, device and collection parameters.",
            "Google Places relevance order and Search Console average position are not map-pack or organic SERP rank.",
            "This preview does not store the import or send data to a search provider.",
        ],
    }
