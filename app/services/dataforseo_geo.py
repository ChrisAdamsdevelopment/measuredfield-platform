"""MF-052: deterministic DataForSEO geography request/response *offline* boundary.

No network client, credential lookup, spend dispatch, DB write, or provider
authentication lives here. Even schema-valid response JSON is unverified input.
The trusted server-side HTTP + approved budget adapter is a separate release gate.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import urlsplit

from app.schemas.local_discovery import MatrixCell, SearchSource, normalize_website_domain
from app.services.search_matrix import canonical_json

Surface = Literal["organic", "local_pack", "google_maps", "local_finder"]
SURFACE_API: dict[str, str] = {
    "organic": "organic",
    "local_pack": "organic",
    "google_maps": "maps",
    "local_finder": "local_finder",
}
RESULT_TYPE = {
    "organic": ("organic", "organic"),
    "local_pack": ("organic", "local_pack"),
    "google_maps": ("maps", "maps_search"),
    "local_finder": ("local_finder", "local_pack"),
}
API_ORIGIN = "https://api.dataforseo.com"
MAX_JSON_BYTES = 2_000_000
MAX_ITEMS = 100
MICRO_USD = Decimal("1000000")


class GeoVendorError(ValueError):
    """Unsafe, ambiguous or incomplete vendor response/request evidence."""


@dataclass(frozen=True, slots=True)
class GeoVendorRequest:
    """Non-billable request plan. Not authorization to run a paid request."""

    endpoint: str
    surface: Surface
    cell_id: str
    plan_signature: str
    provider_id: str
    request_signature: str
    body_json: str
    latitude: float
    longitude: float
    requested_radius_m: int | None
    zoom: int | None
    execution_performed: Literal[False] = False

    def payload(self) -> dict[str, Any]:
        return json.loads(self.body_json)[0]


@dataclass(frozen=True, slots=True)
class TrackedBusiness:
    """Stable matching key. Business names alone cannot prove identity."""

    domain: str | None = None
    cid: str | None = None
    place_id: str | None = None

    def __post_init__(self) -> None:
        if self.domain is not None:
            try:
                normalized = normalize_website_domain(self.domain)
            except ValueError as exc:
                raise GeoVendorError("Owner domain identity is invalid") from exc
            if not normalized:
                raise GeoVendorError("Owner domain identity cannot be blank")
            object.__setattr__(self, "domain", normalized)
        for key in ("cid", "place_id"):
            value = getattr(self, key)
            if value is not None:
                if (not isinstance(value, str) or not 1 <= len(value) <= 200
                        or any(ord(ch) < 33 or ord(ch) > 126 for ch in value)):
                    raise GeoVendorError(f"Owner {key} identity is invalid")
                if key == "cid" and (not value.isascii() or not value.isdecimal()):
                    raise GeoVendorError("Owner CID identity must contain decimal digits")
        if not any((self.domain, self.cid, self.place_id)):
            raise GeoVendorError("Use a domain, provider CID or place ID; name-only matching is unsafe")


def _coordinate(value: Any, *, name: str, low: float, high: float) -> str:
    try:
        num = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise GeoVendorError(f"Invalid {name} coordinate") from None
    if not num.is_finite() or not low <= num <= high:
        raise GeoVendorError(f"Invalid {name} coordinate")
    # Provider coordinate API allows at most seven decimal places.
    if num != num.quantize(Decimal("0.0000001")):
        raise GeoVendorError(f"{name} coordinate needs at most seven decimal places")
    return format(num.normalize(), "f")


def _strict_integer(value: Any, *, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise GeoVendorError(f"Invalid {name}")
    return value


def prepare_dataforseo_request(
    cell: MatrixCell, *, surface: Surface,
    depth: int = 20, zoom: int = 14,
) -> GeoVendorRequest:
    """Translate an existing MF-021 coordinate cell into a single vendor task.

    Organic and actual Google Local Pack both use the *organic* endpoint but
    different filtered item types. Google Maps and Local Finder are separate
    vendor surfaces, never renamed as a local-pack rank.
    """
    if not isinstance(cell, MatrixCell):
        raise GeoVendorError("An MF-021 validated matrix cell is required")
    if surface not in SURFACE_API:
        raise GeoVendorError("Unsupported search surface")
    expected_source = (SearchSource.ORGANIC_SERP if surface == "organic"
                       else SearchSource.LOCAL_PACK_SERP)
    if cell.source != expected_source:
        raise GeoVendorError("The planned source does not match the requested SERP surface")
    if not cell.coordinate_parameter_applied or not cell.provider_id.startswith("dataforseo-"):
        raise GeoVendorError("A coordinate-capable DataForSEO provider profile is required")
    try:
        parameters = json.loads(cell.request_json)
    except (ValueError, TypeError) as exc:
        raise GeoVendorError("Invalid signed MF-021 request parameters") from exc
    if (not isinstance(parameters, dict)
            or parameters.get("provider_id") != cell.provider_id
            or parameters.get("source") != cell.source.value
            or parameters.get("query") != cell.phrase
            or any(key in parameters for key in ("market", "location_name", "location_code"))):
        raise GeoVendorError("MF-021 planned request parameters do not match the cell")
    fingerprint = hashlib.sha256(canonical_json(parameters).encode("utf-8")).hexdigest()
    if fingerprint != cell.request_signature:
        raise GeoVendorError("MF-021 request signature does not match the parameters")
    latitude = _coordinate(parameters.get("latitude"), name="latitude", low=-90, high=90)
    longitude = _coordinate(parameters.get("longitude"), name="longitude", low=-180, high=180)
    radius = _strict_integer(parameters.get("radius_m"), name="radius_m", low=199, high=199999)
    depth = _strict_integer(depth, name="depth", low=1, high=100)
    zoom = _strict_integer(zoom, name="zoom", low=4, high=18)
    device = parameters.get("device", "desktop")
    if device not in ("desktop", "mobile") or (bool(parameters.get("device")) != cell.device_parameter_applied):
        raise GeoVendorError("Device settings are unsupported or not represented in the plan")
    locale = parameters.get("locale", None)
    if locale not in (None, "en", "en-US"):
        raise GeoVendorError("This provider pilot supports only English, not silent locale conversion")
    if bool(locale) != cell.locale_parameter_applied:
        raise GeoVendorError("Locale support does not match the planned request")
    api = SURFACE_API[surface]
    location = f"{latitude},{longitude}"
    if api == "organic":
        location += f",{radius}"
    else:
        location += f",{zoom}z"
    payload = {
        "keyword": cell.phrase,
        "location_coordinate": location,
        "language_code": "en",
        "device": device,
        "depth": depth,
        "tag": f"mf052-{cell.id}",
    }
    endpoint = f"{API_ORIGIN}/v3/serp/google/{api}/live/advanced"
    # DataForSEO expects an array, not an envelope; preserve deterministic JSON.
    body_json = json.dumps([payload], separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    request_signature = hashlib.sha256((endpoint + "\n" + body_json).encode("utf-8")).hexdigest()
    return GeoVendorRequest(
        endpoint=endpoint, surface=surface, cell_id=cell.id,
        plan_signature=cell.request_signature, provider_id=cell.provider_id,
        request_signature=request_signature, body_json=body_json,
        latitude=float(latitude), longitude=float(longitude),
        requested_radius_m=radius if api == "organic" else None,
        zoom=zoom if api != "organic" else None,
    )


def _money_micro(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise GeoVendorError("Vendor cost must be a nonnegative decimal amount")
    try:
        amount = Decimal(str(value))
    except (ValueError, InvalidOperation):
        raise GeoVendorError("Vendor cost must be a nonnegative decimal amount") from None
    if not amount.is_finite() or amount < 0 or amount > Decimal("1000"):
        raise GeoVendorError("Vendor cost is out of the supported reporting range")
    micros = amount * MICRO_USD
    if micros != micros.to_integral_value():
        raise GeoVendorError("Vendor cost has sub-microUSD precision; no rounding is allowed")
    return int(micros)


def _provider_time(value: Any) -> str:
    if not isinstance(value, str) or not 19 <= len(value) <= 45:
        raise GeoVendorError("Vendor observation time is missing or malformed")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise GeoVendorError("Vendor observation time is not ISO-like") from None
    if parsed.utcoffset() is None:
        raise GeoVendorError("Vendor observation time must include a timezone")
    return parsed.isoformat(timespec="seconds")


def _public_domain(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 400:
        raise GeoVendorError("Vendor listing domain is malformed")
    try:
        return normalize_website_domain(value)
    except ValueError as exc:
        raise GeoVendorError("Vendor listing domain is invalid") from exc


def _identity(value: Any, key: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise GeoVendorError(f"Vendor {key} identity is invalid")
    text = str(value)
    if not 1 <= len(text) <= 200 or any(ord(ch) < 33 or ord(ch) > 126 for ch in text):
        raise GeoVendorError(f"Vendor {key} identity is invalid")
    if key == "cid" and (not text.isascii() or not text.isdecimal()):
        raise GeoVendorError("Vendor CID identity is invalid")
    return text


def _title(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 180:
        raise GeoVendorError("Ranked listing needs a readable provider title")
    if any(ord(char) < 32 for char in value):
        raise GeoVendorError("Ranked listing title has control characters")
    return value.strip()


def _matches_owner(listing: dict[str, Any], identity: TrackedBusiness, surface: Surface) -> bool:
    if surface == "organic":
        domain = listing["domain"]
        owner_domain = identity.domain
        return bool(domain and owner_domain and (
            domain == owner_domain or domain.endswith("." + owner_domain)
        ))
    cid, place = listing["cid"], listing["place_id"]
    return bool((identity.cid and cid == identity.cid) or (
        surface == "google_maps" and identity.place_id and place == identity.place_id
    ))


def inspect_dataforseo_response(
    request: GeoVendorRequest,
    response: dict[str, Any],
    *, owner: TrackedBusiness,
) -> dict[str, Any]:
    """Read *untrusted* vendor-shaped JSON for deterministic inspection ONLY.

    An owner-pasted JSON document and an actual API response may have identical
    shape. Neither may be promoted to VERIFIED_PROVIDER by this function.
    A trusted authenticated transport + charged-run receipt will be a separate
    guarded path; do not accept a caller-provided 'verified' flag.
    """
    if not isinstance(request, GeoVendorRequest) or not isinstance(owner, TrackedBusiness):
        raise GeoVendorError("A planned request and stable owner identity are required")
    if not isinstance(response, dict):
        raise GeoVendorError("Vendor response must be an object")
    try:
        raw = canonical_json(response)
    except (TypeError, ValueError) as exc:
        raise GeoVendorError("Vendor response is not safe JSON") from exc
    if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
        raise GeoVendorError("Vendor response exceeds the offline inspection limit")
    if (response.get("status_code") != 20000 or response.get("tasks_error") != 0
            or response.get("tasks_count") != 1):
        raise GeoVendorError("Vendor response did not complete successfully")
    tasks = response.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 1:
        raise GeoVendorError("Vendor response must contain exactly one completed task")
    task = tasks[0]
    if not isinstance(task, dict) or task.get("status_code") != 20000 or task.get("result_count") != 1:
        raise GeoVendorError("Vendor task did not complete successfully")
    task_id = task.get("id")
    if not isinstance(task_id, str) or not 8 <= len(task_id) <= 100:
        raise GeoVendorError("Vendor task receipt ID is invalid")
    expected_path = urlsplit(request.endpoint).path.strip("/").split("/")
    task_path = task.get("path")
    if not isinstance(task_path, list) or task_path[:4] != expected_path[:4] or (
        len(task_path) != 6 or set(task_path[4:]) != {"live", "advanced"}
    ):
        raise GeoVendorError("Vendor task path does not match the requested endpoint")
    data = task.get("data")
    if not isinstance(data, dict):
        raise GeoVendorError("Vendor task parameters are missing")
    requested = request.payload()
    for key, expected in requested.items():
        if data.get(key) != expected:
            raise GeoVendorError(f"Vendor request echo mismatch: {key}")
    if data.get("se") != "google" or data.get("se_type") != SURFACE_API[request.surface]:
        raise GeoVendorError("Vendor response search engine family mismatch")
    results = task.get("result")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise GeoVendorError("Vendor did not provide a complete single-result task")
    result = results[0]
    expected_result_type, listing_type = RESULT_TYPE[request.surface]
    if (result.get("type") != expected_result_type
            or result.get("keyword") != requested["keyword"]
            or result.get("language_code") != "en"):
        raise GeoVendorError("Vendor result metadata mismatch")
    time = _provider_time(result.get("datetime"))
    items = result.get("items")
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise GeoVendorError("Vendor ranked items are incomplete or oversized")
    if (result.get("items_count") is not None and
            result.get("items_count") != len(items)):
        raise GeoVendorError("Vendor items_count is inconsistent")
    task_cost_micro = _money_micro(task.get("cost"))
    if _money_micro(response.get("cost")) != task_cost_micro:
        raise GeoVendorError("Vendor billed totals disagree with the task cost")
    listings = []
    seen_ranks = set()
    seen_identities = {}
    for item in items:
        if not isinstance(item, dict):
            raise GeoVendorError("Vendor result contains a malformed item")
        if item.get("type") != listing_type:
            continue
        # Ads are a distinct paid placement surface, never counted as organic rank.
        if item.get("is_paid") is True:
            continue
        if item.get("is_paid") not in (None, False):
            raise GeoVendorError("Vendor paid placement classification is ambiguous")
        rank = _strict_integer(item.get("rank_group"), name="group rank", low=1, high=100)
        if rank in seen_ranks:
            raise GeoVendorError("Duplicate rank_group on the same search surface")
        seen_ranks.add(rank)
        title = _title(item.get("title"))
        domain = _public_domain(item.get("domain"))
        cid = _identity(item.get("cid"), "cid")
        place_id = _identity(item.get("place_id"), "place_id")
        # Matching a name alone never proves customer/business identity.
        listing = {
            "rank": rank, "title": title, "domain": domain, "cid": cid,
            "place_id": place_id, "is_owner": False,
        }
        listing["is_owner"] = _matches_owner(listing, owner, request.surface)
        key = (domain if request.surface == "organic" else (cid or place_id))
        if key is not None and key in seen_identities and seen_identities[key] != rank:
            # Organic may legitimately show distinct pages from same domain:
            # report the best first occurrence but leave both source rows in evidence.
            if request.surface != "organic":
                raise GeoVendorError("Same local business appears at conflicting positions")
        if key is not None:
            seen_identities[key] = rank
        listings.append(listing)
    positions = [listing["rank"] for listing in listings if listing["is_owner"]]
    # Missing from response depth is never proof of absent listing in wider Google search.
    return {
        "surface": request.surface,
        "status": "offline_source_shaped_not_provider_authenticated",
        "evidence_class": "owner_imported_unverified",
        "provider": "dataforseo",
        "provider_task_id": task_id,
        "response_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "request_signature": request.request_signature,
        "plan_signature": request.plan_signature,
        "cell_id": request.cell_id,
        "search_phrase": requested["keyword"],
        "requested_latitude": request.latitude,
        "requested_longitude": request.longitude,
        "observed_at": time,
        "cost_micro_usd_reported": task_cost_micro,
        "owner_position": min(positions) if positions else None,
        "owner_position_status": "observed_in_returned_items" if positions else "unknown_not_in_returned_items",
        "returned_ranked_items": len(listings),
        "ranked_competitors": [item for item in listings if not item["is_owner"]][:30],
        "owner_matches": [item for item in listings if item["is_owner"]][:30],
        "provider_calls_made": 0,
        "charge_authorized_or_executed": False,
        "independently_verified": False,
        "limitations": (
            "Only a later authenticated provider transport may verify this receipt.",
            "Coordinates are requested provider parameters; this is not proof of the physical user's GPS.",
            "Maps, Local Finder, organic and local pack rankings cannot be silently substituted.",
            "A missing owner in the returned depth remains unknown, not proof of Google absence.",
        ),
    }
