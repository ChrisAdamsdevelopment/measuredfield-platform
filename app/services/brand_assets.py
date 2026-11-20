"""Brand asset candidate discovery, magic-byte sniffing, and evidence extraction (MF-015).

Security architecture:
1. ``extract_asset_candidates`` parses initial HTML and metadata without executing scripts,
   resolving relative URLs against the validated ``page_url`` and rejecting ``data:``,
   ``javascript:``, ``file:``, embedded credentials, non-standard ports, IP literals, and
   internal/reserved hostnames.
2. SVG candidates (``.svg``, ``.svgz``, ``image/svg+xml``) are marked
   ``verification_state='rejected_unsafe'`` because SVG documents can carry executable
   ``<script>``, ``onload``, and ``<foreignObject>`` payloads.
3. ``sniff_asset_candidate`` reuses MF-014's ``validate_and_resolve_url``,
   ``build_pinned_request``, and ``PinnedPublicIPTransport`` (never an unpinned client),
   reads at most 64 KiB in memory to verify raster magic bytes (PNG, JPEG, GIF, WebP, ICO)
   and dimensions, and never persists third-party media to permanent storage.
4. Every discovered asset carries ``license_state='unverified_candidate'`` until an
   authenticated owner explicitly attests rights to use it.
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
from html.parser import HTMLParser
from typing import Any, Callable, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from ..schemas.ai_visual import (
    AssetKind,
    BrandAssetCandidate,
    ConfidenceLevel,
    EvidenceItem,
    ImageDimensions,
)
from ..schemas.preview import DiagnosticObservationCard
from ..vendor.hub_privacy_filter import detect_prompt_injection
from .scanner import (
    ALLOWED_PORTS,
    ALLOWED_SCHEMES,
    PinnedPublicIPTransport,
    SSRFBlockedError,
    _BLOCKED_DOMAIN_SUFFIXES,
    _BLOCKED_HOSTNAMES,
    _HOSTNAME_LABEL_RE,
    _ALPHA_TLD_RE,
    _VerifiedTransportWrapper,
    _looks_like_ip_literal,
    build_pinned_request,
    validate_and_resolve_url,
)

MAX_ASSET_CANDIDATES = 16
MAX_SNIFF_BYTES = 65_536  # 64 KiB header/magic sniff cap
MAX_ASSET_REDIRECTS = 2

_CSS_URL_RE = re.compile(
    r"background(?:-image)?\s*:[^;{}]*?url\(\s*(['\"]?)([^'\")\s]+)\1\s*\)",
    re.IGNORECASE,
)
_LOGO_HINT_RE = re.compile(r"(?:logo|brand|wordmark|crest|emblem|header-mark)", re.IGNORECASE)
_HERO_HINT_RE = re.compile(r"(?:hero|banner|masthead|cover|feature|showcase|jumbotron)", re.IGNORECASE)


def _normalize_candidate_http_url(raw_ref: str, base_url: str) -> str | None:
    """Resolve ``raw_ref`` against ``base_url`` and enforce public HTTP/HTTPS syntax."""
    ref = (raw_ref or "").strip()
    if not ref or len(ref) > 450:
        return None
    if any(ord(c) < 33 or ord(c) > 126 for c in ref):
        return None
    if any(ch in ref for ch in ("<", ">", '"', "'", "\\")):
        return None

    lower_ref = ref.lower()
    if lower_ref.startswith(("data:", "javascript:", "vbscript:", "file:", "blob:", "about:", "mailto:", "tel:")):
        return None

    joined = urljoin(base_url, ref)
    parsed = urlsplit(joined)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        return None
    if parsed.username is not None or parsed.password is not None or "@" in (parsed.netloc or ""):
        return None

    raw_host = parsed.hostname
    if not raw_host:
        return None
    if "[" in (parsed.netloc or "") or "]" in (parsed.netloc or "") or _looks_like_ip_literal(raw_host):
        return None

    try:
        port = parsed.port
    except ValueError:
        return None
    effective_port = port if port is not None else (443 if scheme == "https" else 80)
    if effective_port not in ALLOWED_PORTS:
        return None
    if (scheme == "http" and effective_port == 443) or (scheme == "https" and effective_port == 80):
        return None

    host_clean = raw_host.strip().lower().rstrip(".")
    try:
        ascii_host = host_clean.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if not ascii_host or len(ascii_host) > 253 or _looks_like_ip_literal(ascii_host):
        return None
    if ascii_host in _BLOCKED_HOSTNAMES or ascii_host.endswith(_BLOCKED_DOMAIN_SUFFIXES):
        return None

    labels = ascii_host.split(".")
    if len(labels) < 2:
        return None
    if not all(_HOSTNAME_LABEL_RE.fullmatch(lbl) for lbl in labels):
        return None
    if not _ALPHA_TLD_RE.fullmatch(labels[-1]):
        return None

    path = parsed.path or "/"
    return urlunsplit((scheme, ascii_host, path, parsed.query, ""))


def _sanitize_alt_text(raw_alt: str) -> str:
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f<>]", " ", raw_alt or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()[:200]
    if not cleaned:
        return ""
    if detect_prompt_injection(cleaned):
        return ""
    if re.search(r"(?:ignore\s+previous|system\s+prompt|you\s+are\s+now|disregard\s+all)", cleaned, re.IGNORECASE):
        return ""
    return cleaned


def _parse_dim_attr(raw_val: str | None) -> int | None:
    if not raw_val:
        return None
    m = re.match(r"^\s*(\d{1,5})(?:px)?\s*$", raw_val.strip(), re.IGNORECASE)
    if not m:
        return None
    val = int(m.group(1))
    return val if 1 <= val <= 16384 else None


def _is_svg_url_or_mime(url: str, mime_hint: str = "") -> bool:
    path_lower = urlsplit(url).path.lower()
    mime_lower = (mime_hint or "").lower()
    return path_lower.endswith((".svg", ".svgz")) or "svg" in mime_lower


class _BrandAssetHTMLParser(HTMLParser):
    """Single-pass HTML/metadata parser for candidate logos, icons, and site imagery."""

    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self._in_header_or_nav = 0
        self._in_hero_container = 0
        self._in_style = False
        self._in_jsonld = False
        self._ignored_depth = 0
        self._style_chunks: list[str] = []
        self._jsonld_chunks: list[str] = []
        self.raw_items: list[dict[str, Any]] = []
        self._url_to_index: dict[str, int] = {}

    def _record(
        self,
        raw_url: str,
        *,
        kind: AssetKind,
        discovery_source: str,
        alt_text: str = "",
        mime_hint: str = "",
        width: int | None = None,
        height: int | None = None,
        confidence: ConfidenceLevel = "medium",
    ) -> None:
        norm_url = _normalize_candidate_http_url(raw_url, self.page_url)
        if not norm_url:
            return
        dims = ImageDimensions(width=width, height=height) if (width and height) else None
        clean_alt = _sanitize_alt_text(alt_text)
        if norm_url in self._url_to_index:
            existing = self.raw_items[self._url_to_index[norm_url]]
            if existing["dimensions"] is None and dims is not None:
                existing["dimensions"] = dims
            if not existing["alt_text"] and clean_alt:
                existing["alt_text"] = clean_alt
            if existing["kind"] == "content_image" and kind in ("logo", "hero_image"):
                existing["kind"] = kind
            if existing["confidence"] != "high" and confidence == "high":
                existing["confidence"] = "high"
            return
        if len(self.raw_items) >= MAX_ASSET_CANDIDATES:
            return
        self._url_to_index[norm_url] = len(self.raw_items)

        is_svg = _is_svg_url_or_mime(norm_url, mime_hint)
        self.raw_items.append(
            {
                "source_page": self.page_url,
                "source_url": norm_url,
                "kind": kind,
                "mime": "image/svg+xml" if is_svg else (mime_hint or "unknown"),
                "dimensions": dims,
                "alt_text": clean_alt,
                "confidence": confidence,
                "license_state": "unverified_candidate",
                "verification_state": "rejected_unsafe" if is_svg else "discovered_html",
                "discovery_source": discovery_source,
            }
        )

    def _extract_first_srcset_url(self, srcset: str) -> str:
        for part in (srcset or "").split(","):
            tokens = part.strip().split()
            if tokens and tokens[0]:
                return tokens[0]
        return ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        attr_map = {k.lower(): (v or "").strip() for k, v in attrs if k}

        if t in ("noscript", "template", "svg", "iframe"):
            self._ignored_depth += 1
            return
        if self._ignored_depth > 0:
            return

        if t == "script":
            script_type = attr_map.get("type", "").lower().split(";")[0].strip()
            if script_type == "application/ld+json":
                self._in_jsonld = True
                self._jsonld_chunks = []
            else:
                self._ignored_depth += 1
            return

        if t == "style":
            self._in_style = True
            self._style_chunks = []
            return

        if t in ("header", "nav"):
            self._in_header_or_nav += 1

        combined_cls_id = f"{attr_map.get('class', '')} {attr_map.get('id', '')}"
        if t in ("section", "div", "header") and _HERO_HINT_RE.search(combined_cls_id):
            self._in_hero_container += 1

        inline_style = attr_map.get("style", "")
        if inline_style:
            for match in _CSS_URL_RE.finditer(inline_style):
                self._record(
                    match.group(2),
                    kind="hero_image" if self._in_hero_container > 0 else "css_background",
                    discovery_source="style[background-image]",
                    confidence="medium" if self._in_hero_container > 0 else "low",
                )

        if t == "link":
            rel = attr_map.get("rel", "").lower()
            href = attr_map.get("href", "")
            mime_hint = attr_map.get("type", "").lower()
            if not href:
                return
            if "apple-touch-icon" in rel:
                self._record(
                    href,
                    kind="apple_touch_icon",
                    discovery_source="link[apple-touch-icon]",
                    mime_hint=mime_hint,
                    confidence="high",
                )
            elif "icon" in rel:
                self._record(
                    href,
                    kind="favicon",
                    discovery_source="link[icon]",
                    mime_hint=mime_hint,
                    confidence="high",
                )
        elif t == "meta":
            prop = (attr_map.get("property") or attr_map.get("name") or "").lower()
            content = attr_map.get("content", "")
            if prop in ("og:image", "og:image:url", "twitter:image", "twitter:image:src") and content:
                self._record(
                    content,
                    kind="og_image",
                    discovery_source=f"meta[{prop}]",
                    alt_text=attr_map.get("alt", ""),
                    confidence="high",
                )
        elif t == "source":
            srcset = attr_map.get("srcset", "")
            first_url = self._extract_first_srcset_url(srcset)
            if first_url:
                self._record(
                    first_url,
                    kind="hero_image" if self._in_hero_container > 0 else "gallery_image",
                    discovery_source="picture/source[srcset]",
                    mime_hint=attr_map.get("type", "").lower(),
                    confidence="medium",
                )
        elif t == "img":
            src = attr_map.get("src", "") or self._extract_first_srcset_url(attr_map.get("srcset", ""))
            if not src:
                return
            alt = attr_map.get("alt", "")
            signals = f"{src} {alt} {combined_cls_id}"
            w = _parse_dim_attr(attr_map.get("width"))
            h = _parse_dim_attr(attr_map.get("height"))

            if _LOGO_HINT_RE.search(signals) or self._in_header_or_nav > 0:
                kind: AssetKind = "logo"
                conf: ConfidenceLevel = "high" if _LOGO_HINT_RE.search(signals) else "medium"
            elif _HERO_HINT_RE.search(signals) or self._in_hero_container > 0:
                kind = "hero_image"
                conf = "high"
            else:
                kind = "gallery_image"
                conf = "medium"

            self._record(
                src,
                kind=kind,
                discovery_source="img[src]" if attr_map.get("src") else "img[srcset]",
                alt_text=alt,
                width=w,
                height=h,
                confidence=conf,
            )

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("noscript", "template", "svg", "iframe"):
            if self._ignored_depth > 0:
                self._ignored_depth -= 1
            return
        if t == "script":
            if self._in_jsonld:
                self._in_jsonld = False
                self._parse_jsonld("".join(self._jsonld_chunks)[:32_768])
                self._jsonld_chunks = []
            elif self._ignored_depth > 0:
                self._ignored_depth -= 1
            return
        if self._ignored_depth > 0:
            return
        if t == "style" and self._in_style:
            self._in_style = False
            style_text = "".join(self._style_chunks)[:32_768]
            for match in _CSS_URL_RE.finditer(style_text):
                self._record(
                    match.group(2),
                    kind="css_background",
                    discovery_source="style[background-image]",
                    confidence="low",
                )
            self._style_chunks = []
            return
        if t in ("header", "nav") and self._in_header_or_nav > 0:
            self._in_header_or_nav -= 1
        if t in ("section", "div", "header") and self._in_hero_container > 0:
            self._in_hero_container -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth > 0:
            return
        if self._in_jsonld and len(self._jsonld_chunks) < 50:
            self._jsonld_chunks.append(data)
        elif self._in_style and len(self._style_chunks) < 50:
            self._style_chunks.append(data)

    def _parse_jsonld(self, raw_json: str) -> None:
        try:
            payload = json.loads(raw_json)
        except (ValueError, TypeError):
            return
        self._walk_jsonld(payload, depth=0)

    def _walk_jsonld(self, node: Any, *, depth: int) -> None:
        if depth > 4:
            return
        if isinstance(node, list):
            for item in node[:10]:
                self._walk_jsonld(item, depth=depth + 1)
            return
        if not isinstance(node, dict):
            return
        for key, kind, src_label in (
            ("logo", "logo", "jsonld[logo]"),
            ("image", "hero_image", "jsonld[image]"),
        ):
            val = node.get(key)
            if isinstance(val, str):
                self._record(val, kind=kind, discovery_source=src_label, confidence="high")
            elif isinstance(val, dict) and isinstance(val.get("url"), str):
                self._record(
                    val["url"],
                    kind=kind,
                    discovery_source=src_label,
                    alt_text=str(val.get("caption") or ""),
                    confidence="high",
                )
            elif isinstance(val, list):
                for entry in val[:3]:
                    if isinstance(entry, str):
                        self._record(entry, kind=kind, discovery_source=src_label, confidence="medium")
                    elif isinstance(entry, dict) and isinstance(entry.get("url"), str):
                        self._record(entry["url"], kind=kind, discovery_source=src_label, confidence="medium")
        if "@graph" in node:
            self._walk_jsonld(node["@graph"], depth=depth + 1)


def extract_asset_candidates(html_text: str, page_url: str) -> list[BrandAssetCandidate]:
    """Extract candidate logos, icons, and imagery from static HTML with provenance."""
    normalized_page = _normalize_candidate_http_url(page_url, page_url)
    if not normalized_page:
        raise SSRFBlockedError(f"Unsafe or invalid page_url for asset extraction: {page_url!r}")

    parser = _BrandAssetHTMLParser(normalized_page)
    parser.feed((html_text or "")[:262_144])
    parser.close()

    candidates: list[BrandAssetCandidate] = []
    for idx, item in enumerate(parser.raw_items[:MAX_ASSET_CANDIDATES], start=1):
        candidates.append(
            BrandAssetCandidate(
                id=f"asset_{idx:02d}",
                **item,
            )
        )
    return candidates


def _sniff_jpeg_dimensions(data: bytes) -> ImageDimensions | None:
    idx = 2
    length = len(data)
    while idx + 9 < length:
        if data[idx] != 0xFF:
            idx += 1
            continue
        while idx < length and data[idx] == 0xFF:
            idx += 1
        if idx >= length:
            break
        marker = data[idx]
        idx += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if idx + 2 > length:
            break
        seg_len = struct.unpack(">H", data[idx : idx + 2])[0]
        if seg_len < 2:
            break
        if marker in (0xC0, 0xC1, 0xC2, 0xC3) and idx + 7 <= length:
            height, width = struct.unpack(">HH", data[idx + 3 : idx + 7])
            if 1 <= width <= 16384 and 1 <= height <= 16384:
                return ImageDimensions(width=width, height=height)
            return None
        idx += seg_len
    return None


def _sniff_image_magic_and_dimensions(data: bytes) -> tuple[str | None, ImageDimensions | None, bool]:
    """Inspect magic bytes to detect safe raster image MIME and dimensions.

    Returns ``(mime, dimensions, is_unsafe_markup)``.
    """
    if not data or len(data) < 6:
        return None, None, False

    head_strip = data[:512].lstrip().lower()
    if (
        head_strip.startswith((b"<svg", b"<?xml", b"<!doctype", b"<html", b"<script"))
        or b"<svg" in head_strip
        or b"<script" in head_strip
    ):
        return "image/svg+xml", None, True

    # PNG: 89 50 4E 47 0D 0A 1A 0A + IHDR at offset 16..24
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        dims = None
        if len(data) >= 24 and data[12:16] == b"IHDR":
            w, h = struct.unpack(">II", data[16:24])
            if 1 <= w <= 16384 and 1 <= h <= 16384:
                dims = ImageDimensions(width=w, height=h)
        return "image/png", dims, False

    # JPEG: FF D8 FF
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", _sniff_jpeg_dimensions(data), False

    # GIF: GIF87a / GIF89a
    if data.startswith((b"GIF87a", b"GIF89a")):
        dims = None
        if len(data) >= 10:
            w, h = struct.unpack("<HH", data[6:10])
            if 1 <= w <= 16384 and 1 <= h <= 16384:
                dims = ImageDimensions(width=w, height=h)
        return "image/gif", dims, False

    # WebP: RIFF....WEBP
    if len(data) >= 16 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        dims = None
        chunk_type = data[12:16]
        if chunk_type == b"VP8X" and len(data) >= 30:
            w = 1 + int.from_bytes(data[24:27], "little")
            h = 1 + int.from_bytes(data[27:30], "little")
            if 1 <= w <= 16384 and 1 <= h <= 16384:
                dims = ImageDimensions(width=w, height=h)
        elif chunk_type == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
            bits = int.from_bytes(data[21:25], "little")
            w = (bits & 0x3FFF) + 1
            h = ((bits >> 14) & 0x3FFF) + 1
            if 1 <= w <= 16384 and 1 <= h <= 16384:
                dims = ImageDimensions(width=w, height=h)
        return "image/webp", dims, False

    # ICO: 00 00 01 00
    if len(data) >= 8 and data.startswith(b"\x00\x00\x01\x00"):
        w = data[6] or 256
        h = data[7] or 256
        return "image/x-icon", ImageDimensions(width=w, height=h), False

    return None, None, False


def sniff_asset_candidate(
    candidate: BrandAssetCandidate,
    *,
    resolver: Callable[..., Any] | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 4.0,
    max_bytes: int = MAX_SNIFF_BYTES,
) -> BrandAssetCandidate:
    """Safely fetch up to ``max_bytes`` of a candidate asset over MF-014's pinned transport."""
    if candidate.verification_state == "rejected_unsafe" or _is_svg_url_or_mime(
        candidate.source_url, candidate.mime
    ):
        return candidate.model_copy(update={"verification_state": "rejected_unsafe"})

    current_target = validate_and_resolve_url(candidate.source_url, resolver=resolver)
    active_transport: httpx.BaseTransport = (
        _VerifiedTransportWrapper(transport)
        if transport is not None
        else PinnedPublicIPTransport()
    )
    timeout_cfg = httpx.Timeout(connect=min(timeout, 3.0), read=timeout, write=2.0, pool=2.0)

    try:
        with httpx.Client(
            transport=active_transport,
            trust_env=False,
            follow_redirects=False,
            timeout=timeout_cfg,
        ) as client:
            redirects = 0
            while True:
                request = build_pinned_request(current_target)
                request.headers["Accept"] = "image/webp,image/png,image/jpeg,image/gif,image/x-icon;q=0.9,*/*;q=0.1"
                try:
                    response = client.send(request, stream=True)
                except SSRFBlockedError:
                    raise
                except Exception:  # noqa: BLE001
                    return candidate.model_copy(update={"verification_state": "unreachable"})

                try:
                    status_code = response.status_code
                    if status_code in (301, 302, 303, 307, 308):
                        location = (response.headers.get("location") or "").strip()
                        if not location or redirects >= MAX_ASSET_REDIRECTS:
                            return candidate.model_copy(update={"verification_state": "unreachable"})
                        next_url = urljoin(current_target.normalized_url, location)
                        if _is_svg_url_or_mime(next_url):
                            return candidate.model_copy(
                                update={"verification_state": "rejected_unsafe", "mime": "image/svg+xml"}
                            )
                        current_target = validate_and_resolve_url(next_url, resolver=resolver)
                        redirects += 1
                        continue

                    if status_code != 200:
                        return candidate.model_copy(update={"verification_state": "unreachable"})

                    ct_header = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
                    if "svg" in ct_header or "html" in ct_header or "javascript" in ct_header or "xml" in ct_header:
                        return candidate.model_copy(
                            update={"verification_state": "rejected_unsafe", "mime": ct_header or "unknown"}
                        )

                    chunks: list[bytes] = []
                    total = 0
                    try:
                        for chunk in response.iter_bytes():
                            if not chunk:
                                continue
                            rem = max_bytes - total
                            if rem <= 0:
                                break
                            chunks.append(chunk[:rem])
                            total += len(chunks[-1])
                            if total >= max_bytes:
                                break
                    except SSRFBlockedError:
                        raise
                    except Exception:  # noqa: BLE001
                        return candidate.model_copy(update={"verification_state": "unreachable"})
                    raw_bytes = b"".join(chunks)
                finally:
                    response.close()
                break
    finally:
        if transport is None:
            active_transport.close()

    detected_mime, detected_dims, is_unsafe = _sniff_image_magic_and_dimensions(raw_bytes)
    if is_unsafe or detected_mime is None:
        return candidate.model_copy(
            update={
                "verification_state": "rejected_unsafe",
                "mime": detected_mime or "unknown",
            }
        )

    return candidate.model_copy(
        update={
            "source_url": current_target.normalized_url,
            "mime": detected_mime,
            "dimensions": detected_dims or candidate.dimensions,
            "verification_state": "sniffed_safe_image",
            "byte_size": len(raw_bytes),
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        }
    )


def build_evidence_items_from_card(
    card: DiagnosticObservationCard,
    assets: Sequence[BrandAssetCandidate] = (),
) -> list[EvidenceItem]:
    """Build deterministic ``EvidenceItem`` records from a ``DiagnosticObservationCard`` and assets."""
    items: list[EvidenceItem] = [
        EvidenceItem(
            id="ev_title",
            source_field="title",
            status=card.title_status,
            summary=(
                f"Page title tag observed ({len(card.observed_title)} chars)."
                if card.title_status == "observed"
                else (
                    "No non-empty title tag observed in static HTML."
                    if card.title_status == "not_observed"
                    else "Unable to determine title tag due to incomplete scan."
                )
            ),
            observed_value=card.observed_title,
        ),
        EvidenceItem(
            id="ev_h1",
            source_field="h1",
            status=card.h1_status,
            summary=(
                f"Primary H1 heading observed ({len(card.observed_h1)} chars)."
                if card.h1_status == "observed"
                else (
                    "No non-empty H1 heading observed in static HTML."
                    if card.h1_status == "not_observed"
                    else "Unable to determine H1 heading due to incomplete scan."
                )
            ),
            observed_value=card.observed_h1,
        ),
        EvidenceItem(
            id="ev_tap_to_call",
            source_field="tap_to_call",
            status=card.tap_to_call_status,
            summary=(
                f"Observed {len(card.observed_tel_links)} tel: link(s) in static HTML."
                if card.tap_to_call_status == "observed"
                else (
                    "No tel: phone link observed in static HTML."
                    if card.tap_to_call_status == "not_observed"
                    else "Unable to determine tel: links due to incomplete scan."
                )
            ),
            observed_value=", ".join(card.observed_tel_links[:3]),
        ),
        EvidenceItem(
            id="ev_contact_form",
            source_field="contact_form",
            status=card.contact_form_status,
            summary=(
                f"Observed contact form with {len(card.observed_form_fields)} field(s)."
                if card.contact_form_status == "observed"
                else (
                    "No structured contact form observed in static HTML."
                    if card.contact_form_status == "not_observed"
                    else "Unable to determine contact form due to incomplete scan."
                )
            ),
            observed_value=", ".join(card.observed_form_fields[:5]),
        ),
        EvidenceItem(
            id="ev_viewport_meta",
            source_field="viewport_meta",
            status=card.viewport_meta_status,
            summary=(
                "Responsive mobile viewport meta tag observed."
                if card.viewport_meta_status == "observed"
                else (
                    "No responsive width=device-width viewport meta tag observed."
                    if card.viewport_meta_status == "not_observed"
                    else "Unable to determine viewport meta tag due to incomplete scan."
                )
            ),
            observed_value=card.observed_viewport_meta,
        ),
        EvidenceItem(
            id="ev_cta",
            source_field="cta",
            status=card.cta_status,
            summary=(
                f"Observed {len(card.observed_cta_texts)} call-to-action control(s)."
                if card.cta_status == "observed"
                else (
                    "No action-oriented CTA link or button text observed in static HTML."
                    if card.cta_status == "not_observed"
                    else "Unable to determine CTA controls due to incomplete scan."
                )
            ),
            observed_value=", ".join(card.observed_cta_texts[:4]),
        ),
    ]

    safe_assets = [a for a in assets if a.verification_state != "rejected_unsafe"]
    if card.scan_state != "completed":
        asset_status = "unable_to_determine"
        asset_summary = "Brand assets could not be determined because page inspection did not complete."
        asset_val = ""
    elif safe_assets:
        asset_status = "observed"
        asset_summary = f"Discovered {len(safe_assets)} candidate brand asset(s) requiring owner rights confirmation."
        asset_val = ", ".join(f"{a.id}:{a.kind}" for a in safe_assets[:6])
    else:
        asset_status = "not_observed"
        asset_summary = "No candidate raster logo or hero image assets were discovered in static HTML."
        asset_val = ""

    items.append(
        EvidenceItem(
            id="ev_brand_assets",
            source_field="asset_discovery",
            status=asset_status,
            summary=asset_summary,
            observed_value=asset_val,
        )
    )
    return items
