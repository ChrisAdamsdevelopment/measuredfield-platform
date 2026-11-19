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

# These are the owner-supplied pilot question texts (seeded ONLY for the pilot org
# or direct no-config unit test calls; never offered to other business tenants).
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
DEFAULT_NEUTRAL_MARKETS = (
    "Primary Service Area",
    "Secondary Service Area",
    "Surrounding Metro Area",
)
MODES = ("as_written", "controlled_substitution", "persona_context")
BRAND_ALIASES = ("Prescott's Best Construction", "Prescotts Best Construction",
                 "Prescott Best Construction", "Prescott's Best Construction LLC")
_LEGAL_SUFFIX_RE = re.compile(
    r",?\s+\b(?:l\.?l\.?c\.?|inc\.?|incorporated|corp\.?|corporation|co\.?|company|ltd\.?|pllc|p\.?c\.?)\b\.?$",
    re.I,
)
_UNSAFE_TEXT_RE = re.compile(r"[\x00-\x1f\x7f<>]")
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


@dataclass(frozen=True)
class TenantLabConfig:
    """Per-tenant AI Visibility Lab configuration derived from the tenant profile."""
    business_name: str
    is_pilot: bool
    markets: tuple[str, ...]
    brand_aliases: tuple[str, ...]
    questions: tuple[str, ...]

    def serialize(self) -> dict:
        data = asdict(self)
        data["markets"] = list(self.markets)
        data["brand_aliases"] = list(self.brand_aliases)
        data["questions"] = [
            {"id": f"q-seed-{i}", "text": q}
            for i, q in enumerate(self.questions, 1)
        ]
        return data


def _normalize(query: str) -> str:
    return re.sub(r"\s+", " ", query.casefold()).strip()


def _clean_item(value: str, field: str, *, min_len: int = 2, max_len: int = 120) -> str:
    if not isinstance(value, str):
        raise LabInputError(f"Invalid {field}.")
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not (min_len <= len(cleaned) <= max_len) or _UNSAFE_TEXT_RE.search(cleaned):
        raise LabInputError(f"Each {field} must be {min_len} to {max_len} safe characters.")
    return cleaned


def _is_pilot_org(business_name: str, settings: dict | None = None) -> bool:
    cfg = (settings or {}).get("visibility_lab")
    if isinstance(cfg, dict) and isinstance(cfg.get("is_pilot"), bool):
        return cfg["is_pilot"]
    if isinstance(settings, dict) and settings.get("pilot_org") is True:
        return True
    norm = _normalize(business_name or "")
    pilot_names = {_normalize(a) for a in BRAND_ALIASES}
    return norm in pilot_names


def derive_brand_aliases(business_name: str, extra_aliases: Iterable[str] = ()) -> tuple[str, ...]:
    """Derive brand search aliases from the tenant's own business name."""
    candidates: list[str] = []
    raw_name = re.sub(r"\s+", " ", (business_name or "").strip())
    if raw_name:
        base_name = _LEGAL_SUFFIX_RE.sub("", raw_name).strip(" ,.-")
        roots = [raw_name]
        if base_name and base_name.casefold() != raw_name.casefold():
            roots.append(base_name)
        for root in roots:
            candidates.append(root)
            no_apostrophe = root.replace("'", "").replace("\u2019", "")
            if no_apostrophe != root:
                candidates.append(no_apostrophe)
            no_possessive = re.sub(r"['\u2019]s\b", "", root)
            if no_possessive != root:
                candidates.append(no_possessive)
            if "&" in root:
                candidates.append(re.sub(r"\s*&\s*", " and ", root))
    for extra in extra_aliases or ():
        if isinstance(extra, str) and extra.strip():
            candidates.append(re.sub(r"\s+", " ", extra.strip()))
    seen: set[str] = set()
    result: list[str] = []
    for item in candidates:
        norm = _normalize(item)
        if len(norm) >= 2 and norm not in seen:
            seen.add(norm)
            result.append(item[:120])
    return tuple(result)


def _extract_configured_markets(settings: dict | None) -> tuple[str, ...]:
    if not isinstance(settings, dict):
        return ()
    lab_cfg = settings.get("visibility_lab")
    raw = None
    if isinstance(lab_cfg, dict) and lab_cfg.get("markets"):
        raw = lab_cfg.get("markets")
    elif settings.get("markets"):
        raw = settings.get("markets")
    elif settings.get("service_areas"):
        raw = settings.get("service_areas")
    elif isinstance(settings.get("service_area"), str) and settings["service_area"].strip():
        raw = [p.strip() for p in re.split(r"[;\n]+", settings["service_area"]) if p.strip()]
    if not isinstance(raw, (list, tuple)):
        return ()
    seen: set[str] = set()
    markets: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        cleaned = re.sub(r"\s+", " ", item).strip()
        key = cleaned.casefold()
        if 2 <= len(cleaned) <= 90 and not _UNSAFE_TEXT_RE.search(cleaned) and key not in seen:
            seen.add(key)
            markets.append(cleaned)
    return tuple(markets[:10])


def build_tenant_benchmark_questions(business_name: str, markets: tuple[str, ...]) -> tuple[str, ...]:
    """Generate a 22-question benchmark set scoped to the tenant's business and service areas."""
    brand = _LEGAL_SUFFIX_RE.sub("", (business_name or "our company").strip()).strip(" ,.-") or "our company"
    primary = (markets[0] if markets else "Primary Service Area").strip()
    secondary = (markets[1] if len(markets) > 1 else primary).strip()
    p_city = _market_city(primary)
    s_city = _market_city(secondary)
    return (
        f"Best place to get custom crown molding installed in {p_city}?",
        f"What are the latest trends in kitchen remodeling in {s_city}?",
        f"Where to find residential remodeling contractors in {p_city}?",
        f"Who does flooring installation in {primary}?",
        f"Can I hire {brand} for bathroom renovations in {p_city}?",
        f"What should I look for in a good finish carpenter in {p_city}?",
        f"Where can I get drywall services for home remodeling in {s_city}?",
        f"Who offers baseboard installation in {p_city}?",
        f"Where can I find trusted residential remodelers in {primary}?",
        f"Best company for complete home remodels in {secondary}?",
        f"Where can I get framing services for home projects in {p_city}?",
        f"Best remodeling services in the {s_city} area?",
        f"Does {brand} do kitchen remodels in {p_city}?",
        f"Where can I find kitchen remodeling services in {p_city}?",
        f"How much does a bathroom renovation cost in {p_city}?",
        f"Who offers personalized home remodeling services in {p_city}?",
        f"Who provides detailed carpentry work in {s_city}?",
        f"Where to book a home renovation expert in {p_city}?",
        f"What types of flooring are popular in {s_city} homes?",
        f"Who offers bathroom renovation contractors near {s_city}?",
        f"Where to hire a finish carpenter in the {s_city} area?",
        f"How do I contact {brand} for home remodeling in {s_city}?",
    )


def tenant_lab_config(
    org: object | None = None,
    *,
    business_name: str | None = None,
    settings: dict | None = None,
) -> TenantLabConfig:
    """Resolve per-tenant questions, service markets and brand aliases from the organization profile.

    The pilot set (BENCHMARK_22, MARKETS, BRAND_ALIASES) is seeded ONLY for the pilot org.
    Other tenants receive brand aliases derived from their business name, their own configured
    service areas (or neutral defaults), and benchmark questions scoped to their business.
    """
    name = business_name if business_name is not None else getattr(org, "name", "") or ""
    raw_settings = settings if settings is not None else getattr(org, "settings", None)
    org_settings = raw_settings if isinstance(raw_settings, dict) else {}
    lab_cfg = org_settings.get("visibility_lab")
    lab_dict = lab_cfg if isinstance(lab_cfg, dict) else {}

    pilot = _is_pilot_org(name, org_settings)
    configured_markets = _extract_configured_markets(org_settings)
    if configured_markets:
        markets = configured_markets
    elif pilot:
        markets = MARKETS
    else:
        markets = DEFAULT_NEUTRAL_MARKETS

    extra_aliases = lab_dict.get("brand_aliases") if isinstance(lab_dict.get("brand_aliases"), (list, tuple)) else ()
    if pilot and not extra_aliases:
        aliases = BRAND_ALIASES
    else:
        aliases = derive_brand_aliases(name, extra_aliases)

    raw_questions = lab_dict.get("questions")
    configured_questions: list[str] = []
    if isinstance(raw_questions, (list, tuple)):
        seen_q: set[str] = set()
        for q in raw_questions:
            if isinstance(q, str):
                cleaned_q = re.sub(r"\s+", " ", q).strip()
                q_key = cleaned_q.casefold()
                if 5 <= len(cleaned_q) <= 300 and not _UNSAFE_TEXT_RE.search(cleaned_q) and q_key not in seen_q:
                    seen_q.add(q_key)
                    configured_questions.append(cleaned_q)
    if configured_questions:
        questions = tuple(configured_questions[:MAX_QUESTIONS])
    elif pilot:
        questions = BENCHMARK_22
    else:
        questions = build_tenant_benchmark_questions(name, markets)

    return TenantLabConfig(
        business_name=name or "Workspace",
        is_pilot=pilot,
        markets=markets,
        brand_aliases=aliases,
        questions=questions,
    )


def validate_tenant_lab_settings(
    *,
    business_name: str,
    markets: Iterable[str],
    brand_aliases: Iterable[str] | None = None,
    questions: Iterable[str] | None = None,
) -> dict:
    """Validate owner-supplied per-tenant AI Visibility Lab configuration before persisting."""
    clean_markets: list[str] = []
    seen_markets: set[str] = set()
    for item in markets or ():
        m = _clean_item(item, "market", min_len=2, max_len=90)
        key = m.casefold()
        if key in seen_markets:
            raise LabInputError("Choose distinct supported markets.")
        seen_markets.add(key)
        clean_markets.append(m)
    if not (1 <= len(clean_markets) <= 10):
        raise LabInputError("Configure between 1 and 10 target service markets.")

    clean_aliases: list[str] = []
    if brand_aliases is not None:
        seen_aliases: set[str] = set()
        for item in brand_aliases:
            a = _clean_item(item, "brand alias", min_len=2, max_len=120)
            key = a.casefold()
            if key not in seen_aliases:
                seen_aliases.add(key)
                clean_aliases.append(a)
        if len(clean_aliases) > 20:
            raise LabInputError("Configure at most 20 brand aliases.")

    payload: dict = {"markets": clean_markets}
    if brand_aliases is not None:
        payload["brand_aliases"] = list(derive_brand_aliases(business_name, clean_aliases))
    if questions is not None:
        clean_questions: list[str] = []
        seen_q: set[str] = set()
        for item in questions:
            q = _clean_item(item, "question", min_len=5, max_len=300)
            key = q.casefold()
            if key in seen_q:
                raise LabInputError("Benchmark questions must be distinct.")
            seen_q.add(key)
            clean_questions.append(q)
        if not (1 <= len(clean_questions) <= MAX_QUESTIONS):
            raise LabInputError(f"Configure between 1 and {MAX_QUESTIONS} benchmark questions.")
        payload["questions"] = clean_questions
    return payload


def _market_city(market: str) -> str:
    if market.startswith("Greater Prescott"):
        return "greater Prescott area"
    return market.split(",")[0].strip()


def _geo_for(query: str, markets: tuple[str, ...] = MARKETS) -> str:
    q = query.lower()
    active_markets = tuple(markets) if markets else MARKETS
    if active_markets == MARKETS:
        if "prescott valley" in q or "stoneridge" in q:
            return "Prescott Valley, Arizona"
        if "dewey" in q or "humboldt" in q:
            return "Dewey-Humboldt, Arizona"
        if "chino valley" in q:
            return "Chino Valley, Arizona"
        return "Prescott, Arizona"
    # Match longest city/market name first so "Prescott Valley" matches before "Prescott".
    ordered = sorted(active_markets, key=lambda m: len(_market_city(m)), reverse=True)
    for m in ordered:
        city = _market_city(m).lower()
        if city and city in q:
            return m
    return active_markets[0]


def _category_for(query: str) -> str:
    for category, rx in CATEGORIES:
        if rx.search(query):
            return category
    return "Residential remodeling"


def _candidate_question(category: str, geography: str) -> str:
    if "," in geography:
        location = geography.strip()
    elif geography in ("Prescott", "Prescott Valley", "Dewey-Humboldt", "Chino Valley"):
        location = f"{geography}, Arizona"
    else:
        location = geography.strip()
    if category == "Kitchen remodeling":
        return f"Who are reliable kitchen remodeling contractors serving {location}?"
    if category == "Bathroom remodeling":
        return f"Which contractors provide quality bathroom remodeling in {location}?"
    if category == "Finish carpentry":
        return f"Who offers professional finish carpentry and custom trim in {location}?"
    return f"Which local residential remodeling contractors serve {location}?"


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


def import_gsc_csv(
    text: str,
    *,
    brand_aliases: Iterable[str] | None = None,
    markets: Iterable[str] | None = None,
    config: TenantLabConfig | None = None,
) -> dict:
    """Parse owner-supplied, date-unknown GSC query CSV without discarding rows."""
    if not isinstance(text, str) or not text.strip():
        raise LabInputError("Paste query, clicks, impressions and position columns.")
    if len(text.encode("utf-8")) > MAX_GSC_BYTES:
        raise LabInputError("CSV too large for a preview (320 KB max).")
    active_aliases = tuple(
        config.brand_aliases if config is not None
        else (brand_aliases if brand_aliases is not None else BRAND_ALIASES)
    )
    active_markets = tuple(
        config.markets if config is not None
        else (markets if markets is not None else MARKETS)
    )
    normalized_aliases = tuple(_normalize(a) for a in active_aliases if _normalize(a))
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
                "geography": _geo_for(query, active_markets), "category": _category_for(query),
                "source": "owner_supplied_gsc_csv",
                "status": "excluded_service_or_employment" if EXCLUDED_QUERIES.search(query) else (
                    "brand" if any(alias in key for alias in normalized_aliases) else "eligible"),
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


def propose_questions(
    gsc_rows: Iterable[dict],
    max_proposals: int = 80,
    *,
    default_market: str | None = None,
    config: TenantLabConfig | None = None,
) -> list[dict]:
    """Deduplicate proposed local-hiring questions, retaining original query provenance."""
    fallback_geo = (
        default_market
        or (config.markets[0] if config and config.markets else "Prescott, Arizona")
    )
    groups: dict[tuple[str, str], dict] = {}
    for row in gsc_rows:
        if row.get("status") != "eligible":
            continue
        category = row.get("category") or "Residential remodeling"
        geo = row.get("geography") or fallback_geo
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


def _substitution_patterns(valid_markets: tuple[str, ...]) -> tuple[str, ...]:
    patterns: list[str] = []
    ordered = sorted(valid_markets, key=lambda m: len(_market_city(m)), reverse=True)
    for m in ordered:
        if m.startswith("Greater Prescott"):
            patterns.append(r"\b(?:the )?greater Prescott area\b")
            continue
        parts = [p.strip() for p in m.split(",", 1)]
        city = parts[0]
        if not city:
            continue
        city_rx = re.escape(city).replace(r"\-", "[- ]")
        if len(parts) > 1 and parts[1]:
            region_rx = re.escape(parts[1])
            patterns.append(rf"\b{city_rx}(?:,\s*{region_rx})?\b")
        else:
            patterns.append(rf"\b{city_rx}\b")
    if set(valid_markets) == set(MARKETS):
        for fallback in (
            r"\bPrescott Valley(?:, Arizona)?\b",
            r"\bDewey[- ]Humboldt(?:, Arizona)?\b",
            r"\bChino Valley(?:, Arizona)?\b",
            r"\b(?:the )?greater Prescott area\b",
            r"\bPrescott(?:, Arizona)?\b",
        ):
            if fallback not in patterns:
                patterns.append(fallback)
    return tuple(patterns)


def transform_question(
    question: str,
    market: str,
    mode: str,
    aliases: tuple[str, ...] = BRAND_ALIASES,
    *,
    allowed_markets: Iterable[str] | None = None,
    config: TenantLabConfig | None = None,
) -> dict:
    valid_markets = tuple(
        config.markets if config is not None
        else (allowed_markets if allowed_markets is not None else MARKETS)
    )
    active_aliases = tuple(
        config.brand_aliases if config is not None
        else (aliases if aliases is not None else BRAND_ALIASES)
    )
    if mode not in MODES or market not in valid_markets:
        raise LabInputError("Unsupported search method or market.")
    if mode == "as_written":
        return {"actual_query_sent": question, "original_question": question,
                "method": mode, "target_market": market, "geography_warning": None}
    if mode == "persona_context":
        target_city = _market_city(market)
        other_cities = [
            _market_city(m) for m in valid_markets
            if m != market and _market_city(m).casefold() != target_city.casefold()
        ]
        prescott_conflict = bool(
            "Prescott, Arizona" in valid_markets
            and re.search(r"\bprescott\b", question, re.I)
            and market not in ("Prescott, Arizona", "Greater Prescott / Quad Cities")
        )
        other_conflict = any(
            re.search(rf"\b{re.escape(c)}\b", question, re.I)
            for c in other_cities if c and c.casefold() not in target_city.casefold()
        )
        conflicts = prescott_conflict or other_conflict
        return {"actual_query_sent": f"Answer from the perspective of a homeowner in {market}. Question: {question}",
                "original_question": question, "method": mode, "target_market": market,
                "geography_warning": "Original question may mention another market." if conflicts else None}
    masked, protected = _protected(question, active_aliases)
    city = _market_city(market)
    # Replace geographic place names only AFTER protecting exact business names/aliases.
    patterns = _substitution_patterns(valid_markets)
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


def plan_experiment(
    questions: list[dict],
    markets: list[str],
    modes: list[str],
    repetitions: int = 1,
    chunk_size: int = 5,
    max_total: int = 100,
    *,
    allowed_markets: Iterable[str] | None = None,
    brand_aliases: Iterable[str] | None = None,
    config: TenantLabConfig | None = None,
) -> dict:
    valid_markets = tuple(
        config.markets if config is not None
        else (allowed_markets if allowed_markets is not None else MARKETS)
    )
    active_aliases = tuple(
        config.brand_aliases if config is not None
        else (brand_aliases if brand_aliases is not None else BRAND_ALIASES)
    )
    if not questions or len(questions) > MAX_QUESTIONS:
        raise LabInputError("Select between 1 and 200 questions.")
    if not modes or any(m not in MODES for m in modes) or len(set(modes)) != len(modes):
        raise LabInputError("Select one or more distinct supported modes.")
    if not markets or any(g not in valid_markets for g in markets) or len(set(markets)) != len(markets):
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
                    query = transform_question(
                        str(q["text"]),
                        market,
                        mode,
                        aliases=active_aliases,
                        allowed_markets=valid_markets,
                    )
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
