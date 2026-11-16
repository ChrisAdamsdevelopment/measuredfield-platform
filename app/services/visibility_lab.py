"""Pure local AI Visibility Lab core. No network, billing, desktop access or DB writes.

The live Gemini adapter, durable experiment queue and GSC OAuth will require separate
provider credentials/consent, persisted tenant-isolated schema and release review.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import asdict, dataclass
from typing import Iterable

MAX_GSC_BYTES = 320_000
MAX_GSC_ROWS = 3_000
MAX_QUESTIONS = 200
MAX_PLAN_CALLS = 500

# These are the owner-supplied question texts (not provider responses).
BENCHMARK_22 = (
    "Best place to get custom crown molding installed in Prescott Valley?",
    "What are the latest trends in kitchen remodeling in Prescott?",
    "Where to find residential remodeling contractors in Prescott Valley?",
    "Who does flooring installation in Prescott Valley, Arizona?",
    "Can I hire Prescotts Best Construction for bathroom renovations in Arizona?",
    "What should I look for in a good finish carpenter in Arizona?",
    "Where can I get drywall services for home remodeling in Prescott?",
    "Who offers baseboard installation in Prescott Valley?",
    "Where can I find trusted residential remodelers in Arizona?",
    "Best company for complete home remodels in Prescott, Arizona?",
    "Where can I get framing services for home projects in Prescott Valley?",
    "Best remodeling services in the greater Prescott area?",
    "Does Prescotts Best Construction do kitchen remodels in Prescott Valley?",
    "Where can I find kitchen remodeling services in Prescott Valley?",
    "How much does a bathroom renovation cost in Prescott Valley?",
    "Who offers personalized home remodeling services in Prescott Valley?",
    "Who provides detailed carpentry work in Prescott area?",
    "Where to book a home renovation expert in Prescott Valley?",
    "What types of flooring are popular in Prescott homes?",
    "Who offers bathroom renovation contractors near Prescott?",
    "Where to hire a finish carpenter in the greater Prescott area?",
    "How do I contact Prescotts Best Construction for home remodeling in Prescott?",
)
MARKETS = ("Prescott Valley, Arizona", "Prescott, Arizona",
           "Dewey-Humboldt, Arizona", "Chino Valley, Arizona",
           "Greater Prescott / Quad Cities")
MODES = ("as_written", "controlled_substitution", "persona_context")
BRAND_ALIASES = ("Prescott's Best Construction", "Prescotts Best Construction",
                 "Prescott Best Construction", "Prescott's Best Construction LLC")
EXCLUDED_QUERIES = re.compile(
    r"\b(recruit(er|ers|ing)|jobs?|career|superintendent|"
    r"plumbing|electrical|hvac|roofing|basement finishing)\b", re.I)
CATEGORIES = (
    ("Kitchen remodeling", re.compile(r"\b(kitchen|cabinet|island|pantry)\b", re.I)),
    ("Bathroom remodeling", re.compile(r"\b(bath|shower|tub|accessible|aging.in.place)\b", re.I)),
    ("Finish carpentry", re.compile(r"\b(carpent|trim|baseboard|molding|wainscot|millwork|mantel|bookshel)\w*", re.I)),
    ("Residential remodeling", re.compile(r"\b(remodel|renovat|construction|contractor|home|addition)\w*", re.I)),
)


class LabInputError(ValueError):
    pass


def _geo_for(query: str) -> str:
    q = query.lower()
    if "prescott valley" in q or "stoneridge" in q:
        return "Prescott Valley, Arizona"
    if "dewey" in q or "humboldt" in q:
        return "Dewey-Humboldt, Arizona"
    if "chino valley" in q:
        return "Chino Valley, Arizona"
    return "Prescott, Arizona"


def _category_for(query: str) -> str:
    for category, rx in CATEGORIES:
        if rx.search(query):
            return category
    return "Residential remodeling"


def _candidate_question(category: str, geography: str) -> str:
    city = geography.split(",")[0]
    if category == "Kitchen remodeling":
        return f"Who are reliable kitchen remodeling contractors serving {city}, Arizona?"
    if category == "Bathroom remodeling":
        return f"Which contractors provide quality bathroom remodeling in {city}, Arizona?"
    if category == "Finish carpentry":
        return f"Who offers professional finish carpentry and custom trim in {city}, Arizona?"
    return f"Which local residential remodeling contractors serve {city}, Arizona?"


def _normalize(query: str) -> str:
    return re.sub(r"\s+", " ", query.casefold()).strip()


def _number(value: str, field: str, *, integer: bool = False) -> float | int:
    try:
        n = float(str(value).strip().replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        raise LabInputError(f"Invalid {field} value.") from None
    if not (0 <= n <= 1_000_000) or n != n:
        raise LabInputError(f"Invalid {field} value.")
    if integer and not n.is_integer():
        raise LabInputError(f"{field} must be a whole number.")
    return int(n) if integer else n


def import_gsc_csv(text: str) -> dict:
    """Parse owner-supplied, date-unknown GSC query CSV without discarding rows."""
    if not isinstance(text, str) or not text.strip():
        raise LabInputError("Paste query, clicks, impressions and position columns.")
    if len(text.encode("utf-8")) > MAX_GSC_BYTES:
        raise LabInputError("CSV too large for a preview (320 KB max).")
    sample = text.lstrip("\ufeff").strip()
    first = sample.splitlines()[0].lower()
    delimiter = "\t" if first.count("\t") >= 3 else ","
    try:
        rows = list(csv.reader(io.StringIO(sample), delimiter=delimiter, strict=True))
    except csv.Error:
        raise LabInputError("Malformed CSV quoting.") from None
    if len(rows) > MAX_GSC_ROWS + 1:
        raise LabInputError("This preview handles at most 3,000 GSC rows.")
    if not rows:
        raise LabInputError("No data rows.")
    header = [c.strip().strip("\ufeff").casefold() for c in rows[0]]
    has_header = any("quer" in v or "impr" in v or "click" in v for v in header)
    if has_header:
        def column(keys: tuple[str, ...], fallback: int) -> int:
            return next((i for i, text in enumerate(header) if any(k in text for k in keys)), fallback)
        qi = column(("quer", "keyword"), 0)
        ci = column(("click",), 1)
        ii = column(("impr",), 2)
        pi = column(("pos",), 4 if any("ctr" in h for h in header) else 3)
        data = rows[1:]
    else:
        qi, ci, ii, pi = 0, 1, 2, 4 if len(rows[0]) >= 5 else 3
        data = rows
    parsed, errors, duplicates = [], [], set()
    for line, row in enumerate(data, 2 if has_header else 1):
        if not any(str(c).strip() for c in row):
            continue
        try:
            if len(row) <= max(qi, ci, ii, pi):
                raise LabInputError("Missing a required column.")
            query = row[qi].strip()
            if not 1 <= len(query) <= 250:
                raise LabInputError("Query is empty or too long.")
            key = _normalize(query)
            if key in duplicates:
                raise LabInputError("Duplicate query in this snapshot.")
            clicks = _number(row[ci], "clicks", integer=True)
            impressions = _number(row[ii], "impressions", integer=True)
            position = _number(row[pi], "average position")
            if clicks > impressions:
                raise LabInputError("Clicks cannot exceed impressions.")
            duplicates.add(key)
            parsed.append({
                "query": query, "clicks": clicks, "impressions": impressions,
                "avg_position": position, "ctr_percent": round(100 * clicks / impressions, 2) if impressions else 0.0,
                "geography": _geo_for(query), "category": _category_for(query),
                "source": "owner_supplied_gsc_csv",
                "status": "excluded_service_or_employment" if EXCLUDED_QUERIES.search(query) else (
                    "brand" if any(_normalize(alias) in key for alias in BRAND_ALIASES) else "eligible"),
            })
        except LabInputError as exc:
            errors.append({"row": line, "reason": str(exc)})
    return {
        "rows": parsed, "row_count": len(parsed), "invalid_count": len(errors),
        "errors": errors[:20], "clicks": sum(r["clicks"] for r in parsed),
        "impressions": sum(r["impressions"] for r in parsed),
        "date_range": None, "property_verified": False,
        "note": "Imported query rows are not a live GSC API connection; reporting period was not supplied.",
    }


def propose_questions(gsc_rows: Iterable[dict], max_proposals: int = 80) -> list[dict]:
    """Deduplicate proposed local-hiring questions, retaining original query provenance."""
    groups: dict[tuple[str, str], dict] = {}
    for row in gsc_rows:
        if row.get("status") != "eligible":
            continue
        category = row.get("category") or "Residential remodeling"
        geo = row.get("geography") or "Prescott, Arizona"
        key = (category, geo)
        if key not in groups:
            q = _candidate_question(category, geo)
            groups[key] = {
                "id": "gsc-" + hashlib.sha256((category + "|" + geo).encode()).hexdigest()[:12],
                "question": q, "category": category, "geography": geo,
                "status": "draft_needs_approval", "source_queries": [],
                "impressions": 0, "clicks": 0,
            }
        proposal = groups[key]
        proposal["source_queries"].append({"query": row["query"],
                                           "impressions": row["impressions"],
                                           "clicks": row["clicks"],
                                           "avg_position": row["avg_position"]})
        proposal["impressions"] += row["impressions"]
        proposal["clicks"] += row["clicks"]
    return sorted(groups.values(), key=lambda q: (-q["impressions"], q["category"], q["geography"]))[:max_proposals]


def _protected(text: str, aliases: tuple[str, ...]) -> tuple[str, dict[str, str]]:
    masked, replacements = text, {}
    for alias in sorted(set(aliases), key=len, reverse=True):
        pattern = re.compile(re.escape(alias), re.I)
        def replace(match: re.Match) -> str:
            token = f"__MFB_{len(replacements)}__"
            replacements[token] = match.group(0)
            return token
        masked = pattern.sub(replace, masked)
    return masked, replacements


def transform_question(question: str, market: str, mode: str, aliases: tuple[str, ...] = BRAND_ALIASES) -> dict:
    if mode not in MODES or market not in MARKETS:
        raise LabInputError("Unsupported search method or market.")
    if mode == "as_written":
        return {"actual_query_sent": question, "original_question": question,
                "method": mode, "target_market": market, "geography_warning": None}
    if mode == "persona_context":
        conflicts = bool(re.search(r"\bprescott\b", question, re.I)
                         and market not in ("Prescott, Arizona", "Greater Prescott / Quad Cities"))
        return {"actual_query_sent": f"Answer from the perspective of a homeowner in {market}. Question: {question}",
                "original_question": question, "method": mode, "target_market": market,
                "geography_warning": "Original question may mention another market." if conflicts else None}
    masked, protected = _protected(question, aliases)
    city = market.split(",")[0]
    city = "greater Prescott area" if market.startswith("Greater Prescott") else city
    # Replace geographic place names only AFTER protecting exact business names/aliases.
    patterns = (r"\bPrescott Valley(?:, Arizona)?\b",
                r"\bDewey[- ]Humboldt(?:, Arizona)?\b",
                r"\bChino Valley(?:, Arizona)?\b",
                r"\b(?:the )?greater Prescott area\b",
                r"\bPrescott(?:, Arizona)?\b")
    transformed = masked
    for rx in patterns:
        if re.search(rx, transformed, re.I):
            transformed = re.sub(rx, city, transformed, flags=re.I)
            break
    else:
        transformed = transformed.rstrip("?") + f" in {city}?"
    for token, original in protected.items():
        transformed = transformed.replace(token, original)
    return {"actual_query_sent": transformed, "original_question": question, "method": mode,
            "target_market": market, "geography_warning": None}


def plan_experiment(questions: list[dict], markets: list[str], modes: list[str],
                    repetitions: int = 1, chunk_size: int = 5, max_total: int = 100) -> dict:
    if not questions or len(questions) > MAX_QUESTIONS:
        raise LabInputError("Select between 1 and 200 questions.")
    if not modes or any(m not in MODES for m in modes) or len(set(modes)) != len(modes):
        raise LabInputError("Select one or more distinct supported modes.")
    if not markets or any(g not in MARKETS for g in markets) or len(set(markets)) != len(markets):
        raise LabInputError("Choose distinct supported markets.")
    if not isinstance(repetitions, int) or not 1 <= repetitions <= 10:
        raise LabInputError("Repetitions must be between 1 and 10.")
    if not isinstance(chunk_size, int) or not 1 <= chunk_size <= 10:
        raise LabInputError("Chunk size must be 1 through 10.")
    if not isinstance(max_total, int) or not 1 <= max_total <= MAX_PLAN_CALLS:
        raise LabInputError("Maximum permitted requests must be between 1 and 500.")
    for q in questions:
        if not isinstance(q, dict) or not 1 <= len(str(q.get("text", "")).strip()) <= 300:
            raise LabInputError("Each question requires text, maximum 300 characters.")
    total = len(questions) * len(markets) * len(modes) * repetitions
    if total > max_total:
        raise LabInputError(
            f"Requested {total} calls, but the explicit request budget allows {max_total}. "
            "Increase the approved limit or reduce scope. Nothing was silently removed.")
    tasks = []
    for market in markets:
        for mode in modes:
            for repeat in range(1, repetitions + 1):
                for q in questions:
                    query = transform_question(str(q["text"]), market, mode)
                    tasks.append({
                        "question_id": str(q.get("id") or ""), "market": market,
                        "method": mode, "repeat": repeat,
                        "prompt": query["actual_query_sent"],
                        "geography_warning": query["geography_warning"],
                    })
    return {
        "status": "planning_only", "provider_requests_executed": 0,
        "question_count": len(questions), "market_count": len(markets),
        "mode_count": len(modes), "repetitions": repetitions, "request_count": total,
        "chunk_size": chunk_size, "chunk_count": (total + chunk_size - 1) // chunk_size,
        "remaining": total, "warnings": sorted(set(t["geography_warning"] for t in tasks if t["geography_warning"])),
        "preview": tasks[:5],
        "note": "Preview only: no Gemini calls sent, no credentials configured, no runs recorded.",
    }


def provenance_counts(runs: Iterable[dict]) -> dict:
    """Never trust the string 'live_grounded' without authentic provider evidence."""
    groups = {"historical": 0, "quarantined": 0, "synthetic": 0,
              "captured_unverified": 0, "verified_provider": 0}
    for run in runs:
        rid = str(run.get("id", ""))
        cohort = str(run.get("cohortId", ""))
        mode = str(run.get("executionMode", ""))
        if mode == "imported_baseline" or cohort == "historical_import_22":
            groups["historical"] += 1
        elif "quarant" in cohort or mode == "quarantined_fixture":
            groups["quarantined"] += 1
        elif rid.startswith("run-fresh78-") or cohort == "fresh_executed_78" and rid.startswith("run-fresh78"):
            groups["synthetic"] += 1
        elif (run.get("provider_receipt_verified") is True and
              run.get("raw_provider_response") and
              run.get("groundingStatus") == "grounded_verified"):
            groups["verified_provider"] += 1
        else:
            groups["captured_unverified"] += 1
    return groups
