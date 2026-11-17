"""MF-073: strictly scoped, read-only Census market intelligence adapter.

This module returns dated PUBLIC aggregates, never named businesses.
API keys are used only in a direct TLS request to api.census.gov and are never
exposed in results, logs, exceptions or application templates.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Callable, Literal
from urllib.parse import urlencode
import http.client
import json
import os
import re
import ssl

HOST = "api.census.gov"
BASE_URL = "https://" + HOST
MAX_RESPONSE_BYTES = 512_000
MAX_RESPONSE_ROWS = 900
DEFAULT_TIMEOUT_SECONDS = 15
VALID_PRODUCTS = (
    "cbp_county", "cbp_zip", "nonemp_county",
    "acs_place", "acs_places", "acs_county", "acs_zcta",
)
Product = Literal[
    "cbp_county", "cbp_zip", "nonemp_county",
    "acs_place", "acs_places", "acs_county", "acs_zcta",
]
SUPPRESSION = {"", "N", "S", "D", "-", "X", "null", "None", "**", "***"}
BAD_NUMERIC_VALUES = {-666666666, -555555555, -888888888, -999999999}


class CensusMarketError(RuntimeError):
    """Safe, non-secret public error. Never includes an authenticated request URL."""


class CensusConfigurationError(CensusMarketError):
    pass


class CensusAccessError(CensusMarketError):
    pass


class CensusUnavailable(CensusMarketError):
    pass


@dataclass(frozen=True)
class CensusQuery:
    product: Product
    state: str | None = None
    county: str | None = None
    zip_code: str | None = None
    place: str | None = None
    naics: str = "23"

    def __post_init__(self) -> None:
        if self.product not in VALID_PRODUCTS:
            raise ValueError("Unsupported Census dataset")
        if self.product in ("cbp_county", "nonemp_county", "acs_county"):
            if not (_fips(self.state, 2) and _fips(self.county, 3)):
                raise ValueError("County queries require two-digit state and three-digit county FIPS")
            if self.zip_code is not None or self.place is not None:
                raise ValueError("Unexpected geography for county query")
        elif self.product in ("cbp_zip", "acs_zcta"):
            if not _fips(self.zip_code, 5):
                raise ValueError("ZIP or ZCTA must contain exactly five digits")
            if self.state is not None or self.county is not None or self.place is not None:
                raise ValueError("ZIP/ZCTA queries do not accept county or place filters")
        elif self.product in ("acs_place", "acs_places"):
            if not _fips(self.state, 2):
                raise ValueError("State FIPS must contain exactly two digits")
            if self.product == "acs_place" and not _fips(self.place, 5):
                raise ValueError("Place FIPS must contain exactly five digits")
            if self.product == "acs_places" and self.place is not None:
                raise ValueError("A statewide place listing must not specify a single place")
            if self.county is not None or self.zip_code is not None:
                raise ValueError("Place query cannot mix county or ZIP")
        if self.product in ("cbp_county", "cbp_zip", "nonemp_county"):
            if not isinstance(self.naics, str) or not re.fullmatch(r"[0-9]{2,6}", self.naics):
                raise ValueError("NAICS must be a two- through six-digit code")
        elif self.naics != "23":
            raise ValueError("ACS population does not accept an industry filter")


def _fips(value: object, width: int) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9]{" + str(width) + r"}", value))


@dataclass(frozen=True)
class CensusMetric:
    field: str
    description: str
    value: int | None
    unit: str
    availability: str  # observed | suppressed_or_not_available


@dataclass(frozen=True)
class CensusObservation:
    product: str
    reference_year: int
    dataset: str
    name: str
    geography_kind: str
    geography_code: str
    naics_code: str | None
    naics_edition: str | None
    metrics: tuple[CensusMetric, ...]
    source_url: str
    fetched_at_utc: str
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class CensusResult:
    product: str
    source_url: str
    observations: tuple[CensusObservation, ...]
    cache_hit: bool = False


def read_census_api_key(
    *,
    environ: dict[str, str] | None = None,
    local_key_file: Path | None = None,
) -> str:
    """Load an approved local file or deployed secret without any credential output.

    Local Windows pilot: D:\\CENSUS_API_KEY.env. Deployments should explicitly
    provide MF_CENSUS_API_KEY or MF_CENSUS_API_KEY_FILE from secure secret storage.
    """
    env = os.environ if environ is None else environ
    key = env.get("MF_CENSUS_API_KEY", "")
    if not key:
        path = local_key_file or Path(
            env.get("MF_CENSUS_API_KEY_FILE") or
            (r"D:\CENSUS_API_KEY.env" if os.name == "nt" else "")
        )
        if not str(path) or str(path) == ".":
            raise CensusConfigurationError("Census API credential has not been configured")
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError):
            raise CensusConfigurationError("Census credential file is unavailable") from None
        entries: list[str] = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            if "=" not in line:
                raise CensusConfigurationError("Census credential file has an unsupported format")
            field, secret = line.split("=", 1)
            if field.strip().upper() not in ("CENSUS_API_KEY", "CENSUS_KEY", "API_KEY"):
                raise CensusConfigurationError("Census credential file has an unsupported name")
            entries.append(secret.strip().strip("'\""))
        if len(entries) != 1:
            raise CensusConfigurationError("Census credential file must contain exactly one key")
        key = entries[0]
    if not re.fullmatch(r"[A-Za-z0-9]{40}", key):
        raise CensusConfigurationError("Census credential format is invalid")
    return key


def _request_parts(query: CensusQuery) -> tuple[str, dict[str, str], dict[str, tuple[str, str]]]:
    if query.product in ("cbp_county", "cbp_zip"):
        endpoint = "/data/2023/cbp"
        params = {
            "get": "NAME,ESTAB,EMP,PAYANN" if query.product == "cbp_county" else "NAME,ESTAB",
            "NAICS2017": query.naics,
            "LFO": "001",
            "EMPSZES": "001",
        }
        if query.product == "cbp_county":
            params.update({"for": f"county:{query.county}", "in": f"state:{query.state}"})
        else:
            params["for"] = f"zip code:{query.zip_code}"
        fields = {"ESTAB": ("Employer establishments", "establishments")}
        if query.product == "cbp_county":
            fields.update({"EMP": ("Paid employees", "persons"),
                           "PAYANN": ("Annual payroll", "thousand_usd")})
    elif query.product == "nonemp_county":
        endpoint = "/data/2024/nonemp"
        params = {
            "get": "NAME,NESTAB,NRCPTOT",
            "for": f"county:{query.county}",
            "in": f"state:{query.state}",
            "NAICS2022": query.naics,
            "LFO": "001",
            "RCPSZES": "001",
        }
        fields = {"NESTAB": ("Nonemployer establishments", "establishments"),
                  "NRCPTOT": ("Nonemployer receipts", "thousand_usd")}
    else:
        endpoint = "/data/2024/acs/acs5"
        params = {"get": "NAME,B01003_001E,B11001_001E"}
        if query.product == "acs_county":
            params.update({"for": f"county:{query.county}", "in": f"state:{query.state}"})
        elif query.product == "acs_place":
            params.update({"for": f"place:{query.place}", "in": f"state:{query.state}"})
        elif query.product == "acs_places":
            params.update({"for": "place:*", "in": f"state:{query.state}"})
        else:
            params["for"] = f"zip code tabulation area:{query.zip_code}"
        fields = {"B01003_001E": ("Estimated population", "persons"),
                  "B11001_001E": ("Estimated households", "households")}
    return endpoint, params, fields


def _numeric(raw: object) -> tuple[int | None, str]:
    if not isinstance(raw, str):
        return None, "suppressed_or_not_available"
    value = raw.strip()
    if value in SUPPRESSION:
        return None, "suppressed_or_not_available"
    try:
        n = int(value)
    except ValueError:
        return None, "suppressed_or_not_available"
    if n in BAD_NUMERIC_VALUES or n < 0:
        return None, "suppressed_or_not_available"
    return n, "observed"


def _geo_identity(query: CensusQuery, row: dict[str, str]) -> tuple[str, str]:
    if query.product in ("cbp_county", "nonemp_county", "acs_county"):
        if row.get("state") != query.state or row.get("county") != query.county:
            raise CensusUnavailable("Census response geography did not match request")
        return "county_fips", f"{row['state']}{row['county']}"
    if query.product == "cbp_zip":
        if row.get("zip code") != query.zip_code:
            raise CensusUnavailable("Census response geography did not match request")
        return "business_zip", row["zip code"]
    if query.product == "acs_zcta":
        if row.get("zip code tabulation area") != query.zip_code:
            raise CensusUnavailable("Census response geography did not match request")
        return "acs_zcta_not_postal_zip", row["zip code tabulation area"]
    if row.get("state") != query.state:
        raise CensusUnavailable("Census response geography did not match request")
    code = row.get("place", "")
    if not _fips(code, 5) or (query.product == "acs_place" and code != query.place):
        raise CensusUnavailable("Census response place did not match request")
    return "census_place", f"{row['state']}{code}"


def _caveats(product: str) -> tuple[str, ...]:
    if product in ("cbp_county", "cbp_zip"):
        return (
            "2023 reference-year employer establishments; not named firms or currently licensed competitors.",
            "Employer counts exclude most businesses without paid employees.",
            "ZIP Business Patterns ZIP areas are not equal to ACS ZCTAs or city boundaries.",
        ) if product == "cbp_zip" else (
            "2023 reference-year employer establishments; not named firms or currently licensed competitors.",
            "Employer counts exclude most businesses without paid employees.",
            "CBP payroll is published in thousands of U.S. dollars.",
        )
    if product == "nonemp_county":
        return (
            "2024 nonemployer establishments with no paid employees, not a live licensing list.",
            "2024 nonemployer data use NAICS 2022; 2023 employer CBP uses NAICS 2017.",
            "Gross receipts are published in thousands of U.S. dollars, not net profits.",
        )
    return (
        "2024 American Community Survey five-year estimates, reflecting a multiyear period rather than a 2024 population census.",
        "ACS ZIP Code Tabulation Areas are not USPS delivery ZIP boundaries." if product == "acs_zcta" else
        "Census place boundaries do not equal commercial service territories or ZIP areas.",
    )


def _decode_response(
    payload: bytes,
    query: CensusQuery,
    fields: dict[str, tuple[str, str]],
    source_url: str,
    clock: Callable[[], datetime],
) -> CensusResult:
    try:
        matrix = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        raise CensusUnavailable("Census did not return valid JSON data") from None
    if not isinstance(matrix, list) or not 2 <= len(matrix) <= MAX_RESPONSE_ROWS + 1:
        raise CensusUnavailable("Census response was empty or too large")
    headers = matrix[0]
    if (not isinstance(headers, list) or not all(isinstance(h, str) for h in headers)
            or len(headers) != len(set(headers))
            or not {"NAME", *fields}.issubset(headers)):
        raise CensusUnavailable("Census response lacked required fields")
    result: list[CensusObservation] = []
    year = 2023 if query.product.startswith("cbp") else 2024
    dataset = "County Business Patterns" if query.product.startswith("cbp") else (
        "Nonemployer Statistics" if query.product.startswith("nonemp") else
        "American Community Survey 5-year"
    )
    retrieved = clock().astimezone(timezone.utc).isoformat()
    for raw_row in matrix[1:]:
        if not isinstance(raw_row, list) or len(raw_row) != len(headers):
            raise CensusUnavailable("Census response had malformed table rows")
        if not all(isinstance(v, str) for v in raw_row):
            raise CensusUnavailable("Census response contained unexpected data types")
        row = dict(zip(headers, raw_row))
        geography_kind, geography_code = _geo_identity(query, row)
        measurements = tuple(
            CensusMetric(field, description, parsed[0], unit, parsed[1])
            for field, (description, unit) in fields.items()
            for parsed in (_numeric(row.get(field)),)
        )
        result.append(CensusObservation(
            product=query.product, reference_year=year, dataset=dataset,
            name=row["NAME"][:220], geography_kind=geography_kind,
            geography_code=geography_code,
            naics_code=query.naics if query.product.startswith(("cbp", "nonemp")) else None,
            naics_edition="2017" if query.product.startswith("cbp") else
                ("2022" if query.product.startswith("nonemp") else None),
            metrics=measurements, source_url=source_url,
            fetched_at_utc=retrieved, caveats=_caveats(query.product)
        ))
    return CensusResult(query.product, source_url, tuple(result))


def _safe_get(endpoint: str, parameters: dict[str, str], key: str) -> bytes:
    """One fixed-origin TLS request, no redirects, no arbitrary URLs or raw error logs."""
    if endpoint not in ("/data/2023/cbp", "/data/2024/nonemp", "/data/2024/acs/acs5"):
        raise CensusAccessError("Unsupported Census resource")
    # The key is present only in the TLS request target, never source_url in results.
    request_target = endpoint + "?" + urlencode({**parameters, "key": key})
    connection: http.client.HTTPSConnection | None = None
    try:
        connection = http.client.HTTPSConnection(
            HOST, timeout=DEFAULT_TIMEOUT_SECONDS, context=ssl.create_default_context()
        )
        connection.request("GET", request_target, headers={
            "Accept": "application/json",
            "User-Agent": "MeasuredField-Census-Market/0.1",
        })
        response = connection.getresponse()
        if response.status != 200:
            raise CensusUnavailable("Census endpoint returned a non-success status")
        if "json" not in (response.getheader("Content-Type") or "").lower():
            raise CensusUnavailable("Census endpoint did not return a JSON response")
        content_length = response.getheader("Content-Length")
        if content_length and int(content_length) > MAX_RESPONSE_BYTES:
            raise CensusUnavailable("Census response exceeded size limits")
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise CensusUnavailable("Census response exceeded size limits")
        return payload
    except CensusMarketError:
        raise
    except (OSError, http.client.HTTPException, ValueError):
        raise CensusUnavailable("Census public data service was unavailable") from None
    finally:
        if connection is not None:
            connection.close()


class CensusMarketClient:
    """Small owner-facing read client, with a per-process budget and TTL cache."""

    def __init__(
        self,
        *,
        transport: Callable[[str, dict[str, str], str], bytes] = _safe_get,
        key_loader: Callable[[], str] = read_census_api_key,
        max_calls: int = 60,
        ttl_seconds: int = 3600,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        tick: Callable[[], float] = monotonic,
    ):
        if type(max_calls) is not int or not 1 <= max_calls <= 500:
            raise ValueError("Census request budget must be bounded")
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 86_400:
            raise ValueError("Cache lifetime must be between 30 seconds and one day")
        self.transport = transport
        self.key_loader = key_loader
        self.max_calls = max_calls
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.tick = tick
        self._lock = Lock()
        self._fetches = 0
        self._cache: dict[tuple, tuple[float, CensusResult]] = {}

    def query(self, query: CensusQuery) -> CensusResult:
        if not isinstance(query, CensusQuery):
            raise CensusAccessError("Census query must use a validated product")
        endpoint, params, fields = _request_parts(query)
        cache_key = (endpoint, tuple(sorted(params.items())))
        now = self.tick()
        with self._lock:
            existing = self._cache.get(cache_key)
            if existing and now < existing[0]:
                return replace(existing[1], cache_hit=True)
            if self._fetches >= self.max_calls:
                raise CensusAccessError("Census request budget reached; try again later")
            self._fetches += 1
        key = self.key_loader()
        if not re.fullmatch(r"[A-Za-z0-9]{40}", key):
            raise CensusConfigurationError("Census credential format is invalid")
        source_url = BASE_URL + endpoint + "?" + urlencode(params)
        try:
            raw = self.transport(endpoint, params, key)
        except CensusMarketError:
            raise
        except Exception:
            raise CensusUnavailable("Census public data service was unavailable") from None
        if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
            raise CensusUnavailable("Census response had an invalid size or type")
        outcome = _decode_response(raw, query, fields, source_url, self.clock)
        with self._lock:
            if len(self._cache) >= 128:
                self._cache.clear()
            self._cache[cache_key] = (self.tick() + self.ttl_seconds, outcome)
        return outcome
