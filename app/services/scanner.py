"""SSRF-hardened, IP-pinned static HTML diagnostic scanner.

Security architecture:
1. Pre-flight URL validation rejects non-HTTP(S) schemes, embedded credentials, non-standard
   ports, IP literals (IPv4/IPv6/numeric/octal/hex), single-label hosts, and reserved/internal
   domains before any DNS query occurs.
2. DNS resolution runs once per hop through ``validate_and_resolve_url``. Every returned
   address is validated against IPv4/IPv6 private, loopback, link-local, cloud metadata,
   CGNAT, NAT64, Teredo, 6to4, multicast, benchmarking, and reserved ranges. If any resolved
   record is non-global, the request fails closed with ``SSRFBlockedError``.
3. ``SSRFBlockedError`` inherits from ``ScannerError(Exception)``, NEVER ``ValueError``, so
   IP-parsing ``except ValueError`` clauses can never swallow security blocks.
4. Connection pinning eliminates DNS-rebinding TOCTOU: outbound requests dial the validated
   numeric ``pinned_ip`` directly while preserving the original ``Host`` header and passing
   ``extensions={"sni_hostname": hostname, "pinned_ip": pinned_ip}`` so TLS SNI and X.509
   certificate hostname verification validate the original domain name without a second DNS
   lookup.
5. Every HTTP redirect (up to ``MAX_REDIRECTS=3``) is independently re-parsed, re-resolved,
   and re-pinned before connecting.
"""
from __future__ import annotations

import inspect
import ipaddress
import re
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpcore
import httpx

from ..schemas.preview import (
    STATIC_SCAN_DISCLAIMER,
    DiagnosticFinding,
    DiagnosticObservationCard,
)

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {80, 443}
MAX_REDIRECTS = 3
MAX_HTML_BYTES = 262_144  # 256 KiB cap
SCANNER_USER_AGENT = "MeasuredFieldSiteInspector/1.0 (+https://measuredfield.com; static-html-no-js)"

_HOSTNAME_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ALPHA_TLD_RE = re.compile(r"^[a-z]{2,24}$")

_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
    "kubernetes.default",
    "kubernetes.default.svc",
}
_BLOCKED_DOMAIN_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".arpa",
    ".intranet",
    ".corp",
    ".lan",
    ".home",
    ".invalid",
    ".onion",
)

# Explicit defense-in-depth CIDR blocklists in addition to ipaddress.is_global checks
_BLOCKED_IPV4_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),      # CGNAT & Alibaba Cloud metadata (100.100.100.200)
    ipaddress.ip_network("127.0.0.0/8"),        # Loopback
    ipaddress.ip_network("169.254.0.0/16"),     # Link-local & AWS/GCP/Azure metadata (169.254.169.254)
    ipaddress.ip_network("172.16.0.0/12"),      # RFC 1918
    ipaddress.ip_network("192.0.0.0/24"),       # IETF protocol assignments
    ipaddress.ip_network("192.0.2.0/24"),       # TEST-NET-1
    ipaddress.ip_network("192.88.99.0/24"),     # 6to4 relay anycast
    ipaddress.ip_network("192.168.0.0/16"),     # RFC 1918
    ipaddress.ip_network("198.18.0.0/15"),      # Benchmarking
    ipaddress.ip_network("198.51.100.0/24"),    # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),     # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),        # Multicast
    ipaddress.ip_network("240.0.0.0/4"),        # Reserved / broadcast
)

_BLOCKED_IPV6_NETWORKS = (
    ipaddress.ip_network("::/128"),             # Unspecified
    ipaddress.ip_network("::1/128"),            # Loopback
    ipaddress.ip_network("::ffff:0:0/96"),      # IPv4-mapped IPv6 (also checked via ipv4_mapped)
    ipaddress.ip_network("64:ff9b::/96"),       # NAT64 well-known prefix
    ipaddress.ip_network("64:ff9b:1::/48"),     # NAT64 local-use prefix
    ipaddress.ip_network("100::/64"),           # Discard-only
    ipaddress.ip_network("2001::/32"),          # Teredo tunneling
    ipaddress.ip_network("2001:2::/48"),        # Benchmarking
    ipaddress.ip_network("2001:db8::/32"),      # Documentation
    ipaddress.ip_network("2002::/16"),          # 6to4 tunneling
    ipaddress.ip_network("fc00::/7"),           # Unique local (includes AWS fd00:ec2::254)
    ipaddress.ip_network("fe80::/10"),          # Link-local
    ipaddress.ip_network("ff00::/8"),           # Multicast
)

_CONTACT_FIELD_HINT_RE = re.compile(
    r"(?:email|e-mail|mail|phone|tel|mobile|cell|contact|message|inquiry|project|scope|service|details|help|comment|question)",
    re.IGNORECASE,
)
_PRIMARY_CONTACT_HINT_RE = re.compile(
    r"(?:email|e-mail|phone|tel|mobile|message|inquiry|project|scope|details|help)",
    re.IGNORECASE,
)
_SEARCH_HINT_RE = re.compile(r"^(?:q|s|search|query|keyword|keywords|filter|find)$", re.IGNORECASE)
_CTA_TEXT_RE = re.compile(
    r"\b(?:request|quote|estimate|schedule|book|contact|call|inquir(?:e|y)|get\s+started|talk|consult(?:ation)?|send\s+message|reach\s+out)\b",
    re.IGNORECASE,
)


class ScannerError(Exception):
    """Base exception for scanner failures (intentionally NOT a ValueError subclass)."""


class SSRFBlockedError(ScannerError):
    """Raised when a URL, hostname, IP literal, DNS record, or redirect violates SSRF policy."""


@dataclass(frozen=True)
class PinnedTarget:
    """Validated, DNS-pinned HTTP/HTTPS target.

    Supports both attribute access (``target.pinned_ip``, ``target.normalized_url``) and
    2-tuple unpacking (``normalized_url, pinned_ip = validate_and_resolve_url(...)``).
    """

    normalized_url: str
    pinned_ip: str
    scheme: str
    hostname: str
    port: int
    path_and_query: str
    validated_ips: tuple[str, ...] = ()

    def __iter__(self):
        yield self.normalized_url
        yield self.pinned_ip

    def __getitem__(self, index: int) -> str:
        return (self.normalized_url, self.pinned_ip)[index]

    @property
    def default_port(self) -> int:
        return 443 if self.scheme == "https" else 80

    @property
    def host_header(self) -> str:
        if self.port == self.default_port:
            return self.hostname
        return f"{self.hostname}:{self.port}"

    @property
    def ip_host_literal(self) -> str:
        ip_obj = ipaddress.ip_address(self.pinned_ip)
        return f"[{ip_obj.compressed}]" if isinstance(ip_obj, ipaddress.IPv6Address) else ip_obj.compressed

    @property
    def pinned_url(self) -> str:
        port_suffix = "" if self.port == self.default_port else f":{self.port}"
        return f"{self.scheme}://{self.ip_host_literal}{port_suffix}{self.path_and_query}"


def _check_ipv4(ip: ipaddress.IPv4Address) -> None:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
        or not ip.is_global
    ):
        raise SSRFBlockedError(f"Non-global IPv4 address blocked: {ip}")
    for net in _BLOCKED_IPV4_NETWORKS:
        if ip in net:
            raise SSRFBlockedError(f"Blocked IPv4 network ({net}) for address: {ip}")


def validate_ip_address(ip_raw: str) -> str:
    """Validate that ``ip_raw`` is a globally routable public IPv4 or IPv6 address."""
    if not ip_raw or not isinstance(ip_raw, str):
        raise SSRFBlockedError("Empty or non-string IP address blocked.")
    candidate = ip_raw.strip()
    if "%" in candidate:
        raise SSRFBlockedError("IPv6 zone identifiers are forbidden.")
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]

    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError as exc:
        raise SSRFBlockedError(f"Invalid IP literal: {ip_raw!r}") from exc

    if isinstance(ip, ipaddress.IPv4Address):
        _check_ipv4(ip)
        return ip.compressed

    # IPv6 validation: inspect embedded/mapped IPv4 representations first
    if ip.ipv4_mapped is not None:
        _check_ipv4(ip.ipv4_mapped)
        raise SSRFBlockedError(f"IPv4-mapped IPv6 address blocked: {ip}")
    if ip.sixtofour is not None:
        _check_ipv4(ip.sixtofour)
        raise SSRFBlockedError(f"6to4 IPv6 address blocked: {ip}")
    if ip.teredo is not None:
        raise SSRFBlockedError(f"Teredo IPv6 address blocked: {ip}")

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
        or ip.is_site_local
        or not ip.is_global
    ):
        raise SSRFBlockedError(f"Non-global IPv6 address blocked: {ip}")

    for net in _BLOCKED_IPV6_NETWORKS:
        if ip in net:
            raise SSRFBlockedError(f"Blocked IPv6 network ({net}) for address: {ip}")

    return ip.compressed


def _looks_like_ip_literal(host: str) -> bool:
    """Detect standard, bracketed, hex, octal, or integer IP literals before DNS resolution."""
    stripped = host.strip().strip("[]")
    if not stripped:
        return True
    try:
        ipaddress.ip_address(stripped)
        return True
    except ValueError:
        pass
    if ":" in stripped:
        return True
    # Pure digits or hex/octal dot-separated segments (e.g., 127.1, 0177.0.0.1, 0x7f.0.0.1, 2130706433)
    parts = stripped.split(".")
    if all( re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)", p, re.IGNORECASE) for p in parts ):
        return True
    return False


def _invoke_resolver(resolver: Callable[..., Any], hostname: str, port: int) -> list[str]:
    """Call ``resolver`` (default ``socket.getaddrinfo`` or test stub) and extract IP strings."""
    try:
        sig = inspect.signature(resolver)
        params = [
            p
            for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values())
        arity = 5 if has_varargs else len(params)
    except (ValueError, TypeError):
        arity = 5

    try:
        if arity >= 5:
            raw_records = resolver(hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        elif arity >= 2:
            raw_records = resolver(hostname, port)
        else:
            raw_records = resolver(hostname)
    except SSRFBlockedError:
        raise
    except OSError as exc:
        raise ScannerError(f"DNS resolution failed for {hostname}: {exc}") from exc

    if not raw_records:
        raise SSRFBlockedError(f"DNS resolution returned no addresses for {hostname}.")

    if isinstance(raw_records, str):
        return [raw_records]

    extracted: list[str] = []
    for item in raw_records:
        if isinstance(item, str):
            extracted.append(item)
        elif isinstance(item, tuple) and len(item) >= 5 and isinstance(item[4], tuple) and len(item[4]) >= 1:
            extracted.append(str(item[4][0]))
        elif isinstance(item, tuple) and len(item) >= 1 and isinstance(item[0], str):
            extracted.append(item[0])
        else:
            raise SSRFBlockedError(f"Unrecognized DNS record format for {hostname}.")

    if not extracted:
        raise SSRFBlockedError(f"DNS resolution produced no usable IP addresses for {hostname}.")
    return extracted


def validate_and_resolve_url(
    raw_url: str,
    *,
    resolver: Callable[..., Any] | None = None,
) -> PinnedTarget:
    """Validate URL syntax, reject IP literals/internal hosts, resolve DNS once, and pin a global IP."""
    if not raw_url or not isinstance(raw_url, str):
        raise SSRFBlockedError("URL is required.")
    candidate = raw_url.strip()
    if not candidate or len(candidate) > 500 or any(ord(c) < 33 or ord(c) > 126 for c in candidate):
        raise SSRFBlockedError("Malformed or control-character URL rejected.")

    parsed = urlsplit(candidate)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise SSRFBlockedError(f"Unsupported URL scheme {scheme!r}; only http and https are permitted.")

    if parsed.username is not None or parsed.password is not None or "@" in (parsed.netloc or ""):
        raise SSRFBlockedError("Embedded credentials in URL are forbidden.")

    raw_host = parsed.hostname
    if not raw_host:
        raise SSRFBlockedError("URL is missing a hostname.")

    # Check raw netloc for IPv6 brackets or IP literal forms BEFORE any ValueError try-block
    if "[" in (parsed.netloc or "") or "]" in (parsed.netloc or "") or _looks_like_ip_literal(raw_host):
        raise SSRFBlockedError("Direct IP literal URLs are forbidden; provide a public domain name.")

    try:
        port = parsed.port
    except ValueError as exc:
        raise SSRFBlockedError("Invalid port in URL.") from exc

    effective_port = port if port is not None else (443 if scheme == "https" else 80)
    if effective_port not in ALLOWED_PORTS:
        raise SSRFBlockedError(f"Port {effective_port} is not permitted; only standard ports 80 and 443 are allowed.")

    if scheme == "http" and effective_port == 443:
        raise SSRFBlockedError("Port 443 requires https scheme.")
    if scheme == "https" and effective_port == 80:
        raise SSRFBlockedError("Port 80 requires http scheme.")

    host_clean = raw_host.strip().lower().rstrip(".")
    try:
        ascii_host = host_clean.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise SSRFBlockedError("Invalid internationalized domain name.") from exc

    if not ascii_host or len(ascii_host) > 253:
        raise SSRFBlockedError("Hostname length is invalid.")

    if _looks_like_ip_literal(ascii_host):
        raise SSRFBlockedError("Direct IP literal hostnames are forbidden.")

    if ascii_host in _BLOCKED_HOSTNAMES or ascii_host.endswith(_BLOCKED_DOMAIN_SUFFIXES):
        raise SSRFBlockedError(f"Internal or reserved hostname blocked: {ascii_host}")

    labels = ascii_host.split(".")
    if len(labels) < 2:
        raise SSRFBlockedError("Single-label or intranet hostnames are forbidden.")
    if not all(_HOSTNAME_LABEL_RE.fullmatch(label) for label in labels):
        raise SSRFBlockedError("Hostname contains invalid DNS characters.")
    if not _ALPHA_TLD_RE.fullmatch(labels[-1]):
        raise SSRFBlockedError("Top-level domain must be alphabetic.")

    dns_resolver = resolver if resolver is not None else socket.getaddrinfo
    resolved_ips = _invoke_resolver(dns_resolver, ascii_host, effective_port)

    # Validate EVERY returned address; fail closed if any address is private/non-global
    validated_ips: list[str] = []
    for ip_str in resolved_ips:
        clean_ip = validate_ip_address(ip_str)
        if clean_ip not in validated_ips:
            validated_ips.append(clean_ip)

    if not validated_ips:
        raise SSRFBlockedError("No globally routable IP addresses resolved.")

    path = parsed.path or "/"
    path_and_query = path + (f"?{parsed.query}" if parsed.query else "")
    normalized_url = urlunsplit((scheme, ascii_host, path, parsed.query, ""))

    return PinnedTarget(
        normalized_url=normalized_url,
        pinned_ip=validated_ips[0],
        scheme=scheme,
        hostname=ascii_host,
        port=effective_port,
        path_and_query=path_and_query,
        validated_ips=tuple(validated_ips),
    )


class PinnedSyncBackend(httpcore.NetworkBackend):
    """NetworkBackend that refuses non-numeric or non-global hosts and verifies socket peername."""

    def __init__(self, inner: httpcore.NetworkBackend | None = None) -> None:
        self._inner = inner or httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        # Fail closed if httpcore ever attempts to connect using an unpinned domain name
        pinned_ip = validate_ip_address(host)
        if port not in ALLOWED_PORTS:
            raise SSRFBlockedError(f"Outbound TCP connection to port {port} blocked.")

        stream = self._inner.connect_tcp(
            host=pinned_ip,
            port=port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )
        # Verify underlying socket peername prior to TLS handshake or HTTP write
        raw_sock = stream.get_extra_info("socket")
        if raw_sock is not None:
            try:
                peer = raw_sock.getpeername()
                if isinstance(peer, tuple) and len(peer) >= 1:
                    peer_ip = validate_ip_address(str(peer[0]))
                    if ipaddress.ip_address(peer_ip) != ipaddress.ip_address(pinned_ip):
                        stream.close()
                        raise SSRFBlockedError(
                            f"Socket peer IP {peer_ip} did not match pinned IP {pinned_ip}."
                        )
            except SSRFBlockedError:
                stream.close()
                raise
            except OSError as exc:
                stream.close()
                raise ScannerError(f"Unable to verify socket peername: {exc}") from exc
        return stream

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        raise SSRFBlockedError("Unix domain socket connections are forbidden.")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


def _verify_pinned_httpx_request(request: httpx.Request) -> tuple[str, str]:
    """Verify that an ``httpx.Request`` is bound to a validated pinned IP and SNI hostname."""
    pinned_ip = request.extensions.get("pinned_ip")
    sni_hostname = request.extensions.get("sni_hostname")
    if not pinned_ip or not isinstance(pinned_ip, str):
        raise SSRFBlockedError("Outbound request missing required pinned_ip extension (fail-closed).")
    if not sni_hostname or not isinstance(sni_hostname, str):
        raise SSRFBlockedError("Outbound request missing required sni_hostname extension (fail-closed).")

    validated_ip = validate_ip_address(pinned_ip)
    req_host = (request.url.host or "").strip("[]")
    try:
        req_ip = ipaddress.ip_address(req_host)
    except ValueError as exc:
        raise SSRFBlockedError(
            f"Outbound request URL host must be the pinned IP literal, got {req_host!r}."
        ) from exc

    if req_ip != ipaddress.ip_address(validated_ip):
        raise SSRFBlockedError(
            f"Outbound request URL IP {req_ip} does not match pinned_ip {validated_ip}."
        )

    host_header = request.headers.get("Host", "").strip().lower()
    expected_host = sni_hostname.lower()
    if not host_header or host_header.split(":")[0] != expected_host:
        raise SSRFBlockedError(
            f"Outbound request Host header {host_header!r} does not match validated hostname {expected_host!r}."
        )
    return validated_ip, expected_host


class _PoolByteStream(httpx.SyncByteStream):
    def __init__(self, stream: Iterable[bytes]) -> None:
        self._stream = stream

    def __iter__(self):
        for chunk in self._stream:
            yield chunk

    def close(self) -> None:
        if hasattr(self._stream, "close"):
            self._stream.close()


class PinnedPublicIPTransport(httpx.BaseTransport):
    """HTTPX transport that dials only the pre-validated pinned public IP with TLS SNI."""

    def __init__(
        self,
        *,
        network_backend: httpcore.NetworkBackend | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._ssl_context = ssl_context or ssl.create_default_context()
        self._backend = PinnedSyncBackend(network_backend)
        self._pool = httpcore.ConnectionPool(
            ssl_context=self._ssl_context,
            network_backend=self._backend,
            max_connections=4,
            max_keepalive_connections=0,
            retries=0,
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        validated_ip, sni_hostname = _verify_pinned_httpx_request(request)
        ip_obj = ipaddress.ip_address(validated_ip)
        raw_ip_host = (
            f"[{ip_obj.compressed}]".encode("ascii")
            if isinstance(ip_obj, ipaddress.IPv6Address)
            else ip_obj.compressed.encode("ascii")
        )
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = sni_hostname

        core_req = httpcore.Request(
            method=request.method.encode("ascii"),
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=raw_ip_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=extensions,
        )
        try:
            core_resp = self._pool.handle_request(core_req)
        except SSRFBlockedError:
            raise
        except Exception as exc:
            raise ScannerError(f"Transport request failed: {type(exc).__name__}: {exc}") from exc

        return httpx.Response(
            status_code=core_resp.status,
            headers=core_resp.headers,
            stream=_PoolByteStream(core_resp.stream),
            extensions=core_resp.extensions,
        )

    def close(self) -> None:
        self._pool.close()


class _VerifiedTransportWrapper(httpx.BaseTransport):
    """Wraps an injected test transport so all pinning invariants are still enforced before dispatch."""

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        _verify_pinned_httpx_request(request)
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


def build_pinned_request(target: PinnedTarget) -> httpx.Request:
    """Create a GET request targeting ``target.pinned_url`` while preserving ``Host`` and ``sni_hostname``."""
    headers = {
        "Host": target.host_header,
        "User-Agent": SCANNER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        "Accept-Language": "en-US,en;q=0.8",
        "Cache-Control": "no-cache",
        "Connection": "close",
    }
    extensions = {
        "pinned_ip": target.pinned_ip,
        "sni_hostname": target.hostname,
        "original_url": target.normalized_url,
        "pinned_target": target,
    }
    return httpx.Request("GET", target.pinned_url, headers=headers, extensions=extensions)


@dataclass
class _FormCandidate:
    role: str
    action: str
    field_descriptors: list[str]
    has_primary_contact_field: bool = False
    has_textarea: bool = False
    is_search_only: bool = False


class _StaticHTMLDiagnosticParser(HTMLParser):
    """Deterministic single-pass HTML parser that never executes scripts or fetches subresources."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._in_title = False
        self._in_h1 = False
        self._h1_captured = False
        self._active_interactive_tag: str | None = None
        self._active_interactive_chunks: list[str] = []

        self.title_chunks: list[str] = []
        self.h1_chunks: list[str] = []
        self.tel_links: list[str] = []
        self.mailto_links: list[str] = []
        self.viewport_meta: str = ""
        self.cta_texts: list[str] = []
        self.contact_form_fields: list[str] = []

        self._form_stack: list[_FormCandidate] = []

    @staticmethod
    def _clean(text: str, limit: int = 160) -> str:
        collapsed = re.sub(r"\s+", " ", text or "").strip()
        return collapsed[:limit]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript", "template", "svg"):
            self._ignored_depth += 1
            return
        if self._ignored_depth > 0:
            return

        attr_map = {k.lower(): (v or "").strip() for k, v in attrs if k}

        if t == "title":
            self._in_title = True
        elif t == "h1" and not self._h1_captured:
            self._in_h1 = True
        elif t == "meta":
            if attr_map.get("name", "").lower() == "viewport" and not self.viewport_meta:
                self.viewport_meta = self._clean(attr_map.get("content", ""), 200)
        elif t == "a":
            href = attr_map.get("href", "")
            href_lower = href.lower()
            if href_lower.startswith("tel:"):
                raw_tel = href[4:].split("?")[0].strip()
                digits = re.sub(r"\D", "", raw_tel)
                if len(digits) >= 7 and raw_tel not in self.tel_links and len(self.tel_links) < 5:
                    self.tel_links.append(self._clean(raw_tel, 40))
            elif href_lower.startswith("mailto:"):
                raw_mail = href[7:].split("?")[0].strip()
                if "@" in raw_mail and raw_mail not in self.mailto_links and len(self.mailto_links) < 5:
                    self.mailto_links.append(self._clean(raw_mail, 120))
            if self._active_interactive_tag is None:
                self._active_interactive_tag = "a"
                self._active_interactive_chunks = []
        elif t == "button":
            if self._active_interactive_tag is None:
                self._active_interactive_tag = "button"
                self._active_interactive_chunks = []
        elif t == "form":
            role = attr_map.get("role", "").lower()
            action = attr_map.get("action", "").lower()
            is_search = role == "search" or "/search" in action or action.endswith("search")
            self._form_stack.append(
                _FormCandidate(role=role, action=action, field_descriptors=[], is_search_only=is_search)
            )
        elif t in ("input", "textarea", "select"):
            self._inspect_form_control(t, attr_map)

    def _inspect_form_control(self, tag: str, attr_map: dict[str, str]) -> None:
        if tag == "input":
            input_type = (attr_map.get("type") or "text").lower()
            if input_type in ("submit", "button"):
                val = self._clean(attr_map.get("value", ""), 80)
                if val and _CTA_TEXT_RE.search(val) and val not in self.cta_texts and len(self.cta_texts) < 6:
                    self.cta_texts.append(val)
                return
            if input_type in ("hidden", "reset", "image"):
                return
        else:
            input_type = tag

        # CRITICAL: Inputs outside a <form> element are NEVER counted toward contact forms
        if not self._form_stack:
            return

        current_form = self._form_stack[-1]
        if current_form.is_search_only:
            return

        name_or_id = (
            attr_map.get("name")
            or attr_map.get("id")
            or attr_map.get("autocomplete")
            or attr_map.get("aria-label")
            or attr_map.get("placeholder")
            or input_type
        ).strip()

        if input_type == "search" or _SEARCH_HINT_RE.fullmatch(name_or_id):
            return

        descriptor = self._clean(f"{tag}:{name_or_id}", 60)
        if len(current_form.field_descriptors) < 12:
            current_form.field_descriptors.append(descriptor)

        combined_signals = " ".join(
            [
                input_type,
                attr_map.get("name", ""),
                attr_map.get("id", ""),
                attr_map.get("autocomplete", ""),
                attr_map.get("placeholder", ""),
                attr_map.get("aria-label", ""),
            ]
        )
        if tag == "textarea":
            current_form.has_textarea = True
            current_form.has_primary_contact_field = True
        elif input_type in ("email", "tel") or _PRIMARY_CONTACT_HINT_RE.search(combined_signals):
            current_form.has_primary_contact_field = True

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript", "template", "svg"):
            if self._ignored_depth > 0:
                self._ignored_depth -= 1
            return
        if self._ignored_depth > 0:
            return

        if t == "title":
            self._in_title = False
        elif t == "h1" and self._in_h1:
            self._in_h1 = False
            self._h1_captured = True
        elif t in ("a", "button") and self._active_interactive_tag == t:
            label = self._clean(" ".join(self._active_interactive_chunks), 80)
            if label and _CTA_TEXT_RE.search(label) and label not in self.cta_texts and len(self.cta_texts) < 6:
                self.cta_texts.append(label)
            self._active_interactive_tag = None
            self._active_interactive_chunks = []
        elif t == "form" and self._form_stack:
            completed = self._form_stack.pop()
            # A genuine contact/inquiry form requires a real <form> with a contact/message field
            # and at least 2 user-editable fields (or an explicit contact field + textarea)
            if (
                not completed.is_search_only
                and completed.has_primary_contact_field
                and (len(completed.field_descriptors) >= 2 or completed.has_textarea)
                and not self.contact_form_fields
            ):
                self.contact_form_fields = list(completed.field_descriptors[:8])

    def handle_data(self, data: str) -> None:
        if self._ignored_depth > 0:
            return
        if self._in_title and len(self.title_chunks) < 20:
            self.title_chunks.append(data)
        if self._in_h1 and len(self.h1_chunks) < 20:
            self.h1_chunks.append(data)
        if self._active_interactive_tag is not None and len(self._active_interactive_chunks) < 12:
            self._active_interactive_chunks.append(data)


def _build_completed_findings(parser: _StaticHTMLDiagnosticParser) -> tuple[
    str, str, list[DiagnosticFinding]
]:
    title_text = parser._clean(" ".join(parser.title_chunks), 200)
    h1_text = parser._clean(" ".join(parser.h1_chunks), 200)
    viewport_ok = "width=device-width" in parser.viewport_meta.lower()
    has_tel = bool(parser.tel_links)
    has_form = bool(parser.contact_form_fields)

    findings: list[DiagnosticFinding] = [
        DiagnosticFinding(
            code="tap_to_call",
            title="Tap-to-call phone link (tel:) in static HTML",
            status="observed" if has_tel else "not_observed",
            evidence_status="system_recorded",
            observed_detail=(
                f"Observed tel: link ({parser.tel_links[0]}) in static HTML."
                if has_tel
                else "No <a href='tel:...'> link was observed in the initial static HTML response."
            ),
            capability="website",
            what_we_do="Place a direct tap-to-call phone link in the header and contact section after you confirm your business number.",
            first_step="Confirm the phone number customers should call and enable the header call link.",
            evidence_needed="Whether mobile visitors tap the call link or submit an inquiry once published.",
            improvement_metric="Completed website inquiries and recorded first-response times",
        ),
        DiagnosticFinding(
            code="service_city_heading",
            title="Primary page heading (<h1>) in static HTML",
            status="observed" if h1_text else "not_observed",
            evidence_status="system_recorded",
            observed_detail=(
                f"Observed <h1> heading: '{h1_text}'."
                if h1_text
                else "No non-empty <h1> heading was observed in the initial static HTML response."
            ),
            capability="visibility",
            what_we_do="State clearly what your business does and which service area you cover in the primary heading.",
            first_step="Confirm your primary service and city for the homepage hero heading.",
            evidence_needed="Actual search impressions and visitor inquiries after publishing.",
            improvement_metric="Search Console impressions and clicks for authorized properties (requires your authorization)",
        ),
        DiagnosticFinding(
            code="structured_contact_form",
            title="Structured contact or inquiry <form> in static HTML",
            status="observed" if has_form else "not_observed",
            evidence_status="system_recorded",
            observed_detail=(
                f"Observed contact form with fields: {', '.join(parser.contact_form_fields[:5])}."
                if has_form
                else "No <form> containing contact/inquiry fields (email, phone, or project details) was observed in static HTML."
            ),
            capability="inquiry_desk",
            what_we_do="Provide a structured inquiry form and scope explorer that routes requests into your private inquiry desk.",
            first_step="Choose the 3 to 5 scope questions that help you qualify a new request quickly.",
            evidence_needed="How many visitors start and complete the inquiry form once live.",
            improvement_metric="Page views, contact-form starts and completed inquiries on the published site",
        ),
        DiagnosticFinding(
            code="mobile_viewport",
            title="Responsive mobile viewport meta tag in static HTML",
            status="observed" if viewport_ok else "not_observed",
            evidence_status="system_recorded",
            observed_detail=(
                f"Observed viewport meta tag: '{parser.viewport_meta}'."
                if viewport_ok
                else "No <meta name='viewport' content='...width=device-width...'> tag was observed in static HTML."
            ),
            capability="website",
            what_we_do="Serve responsive mobile-first layouts with a standard viewport configuration.",
            first_step="Preview the draft layout at mobile and desktop widths before approving publication.",
            evidence_needed="Owner visual review across mobile and desktop screens.",
            improvement_metric="Page views, contact-form starts and completed inquiries on the published site",
        ),
    ]
    return title_text, h1_text, findings


def inspect_public_url(
    raw_url: str,
    *,
    resolver: Callable[..., Any] | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 5.0,
    max_redirects: int = MAX_REDIRECTS,
    max_bytes: int = MAX_HTML_BYTES,
) -> DiagnosticObservationCard:
    """Inspect a public HTTP/HTTPS URL with pinned-IP SSRF protection and tri-state diagnostics.

    Raises ``SSRFBlockedError`` immediately if the initial URL or any redirect target
    violates SSRF policy (private/loopback/metadata/non-global IP, IP literal, unsafe
    scheme, or unpinned transport).

    For operational HTTP errors (e.g. 503 Service Unavailable), non-HTML responses, or
    network timeouts, returns a ``DiagnosticObservationCard`` with all check statuses
    set to ``unable_to_determine`` and zero ``not_observed`` findings.
    """
    current_target = validate_and_resolve_url(raw_url, resolver=resolver)
    initial_normalized_url = current_target.normalized_url

    active_transport: httpx.BaseTransport = (
        _VerifiedTransportWrapper(transport)
        if transport is not None
        else PinnedPublicIPTransport()
    )
    timeout_cfg = httpx.Timeout(connect=min(timeout, 3.5), read=timeout, write=3.0, pool=3.0)

    try:
        with httpx.Client(
            transport=active_transport,
            trust_env=False,
            follow_redirects=False,
            timeout=timeout_cfg,
        ) as client:
            redirects_followed = 0
            while True:
                request = build_pinned_request(current_target)
                try:
                    response = client.send(request, stream=True)
                except SSRFBlockedError:
                    raise
                except Exception as exc:
                    return DiagnosticObservationCard(
                        input_url=initial_normalized_url,
                        final_url=current_target.normalized_url,
                        pinned_ip=current_target.pinned_ip,
                        scan_state="unreachable",
                        failure_reason=f"Network connection failed: {type(exc).__name__}",
                    )

                try:
                    status_code = response.status_code
                    if status_code in (301, 302, 303, 307, 308):
                        location = (response.headers.get("location") or "").strip()
                        if not location:
                            return DiagnosticObservationCard(
                                input_url=initial_normalized_url,
                                final_url=current_target.normalized_url,
                                pinned_ip=current_target.pinned_ip,
                                scan_state="unreachable",
                                http_status=status_code,
                                failure_reason=f"HTTP {status_code} redirect without a Location header.",
                            )
                        if redirects_followed >= max_redirects:
                            return DiagnosticObservationCard(
                                input_url=initial_normalized_url,
                                final_url=current_target.normalized_url,
                                pinned_ip=current_target.pinned_ip,
                                scan_state="unreachable",
                                http_status=status_code,
                                failure_reason=f"Exceeded maximum redirects ({max_redirects}).",
                            )
                        next_url = urljoin(current_target.normalized_url, location)
                        # Independently validate and pin the redirect destination BEFORE connecting
                        current_target = validate_and_resolve_url(next_url, resolver=resolver)
                        redirects_followed += 1
                        continue

                    raw_ct = (response.headers.get("content-type") or "").strip()
                    media_type = raw_ct.split(";")[0].strip().lower()

                    if status_code < 200 or status_code >= 400:
                        return DiagnosticObservationCard(
                            input_url=initial_normalized_url,
                            final_url=current_target.normalized_url,
                            pinned_ip=current_target.pinned_ip,
                            scan_state="http_error",
                            http_status=status_code,
                            content_type=media_type or None,
                            failure_reason=(
                                f"Target server returned HTTP {status_code}; page features cannot be determined from an error response."
                            ),
                        )

                    if media_type and media_type not in ("text/html", "application/xhtml+xml"):
                        return DiagnosticObservationCard(
                            input_url=initial_normalized_url,
                            final_url=current_target.normalized_url,
                            pinned_ip=current_target.pinned_ip,
                            scan_state="non_html",
                            http_status=status_code,
                            content_type=media_type,
                            failure_reason=f"Response Content-Type '{media_type}' is not HTML.",
                        )

                    chunks: list[bytes] = []
                    total_read = 0
                    try:
                        for chunk in response.iter_bytes():
                            if not chunk:
                                continue
                            remaining = max_bytes - total_read
                            if remaining <= 0:
                                break
                            chunks.append(chunk[:remaining])
                            total_read += len(chunks[-1])
                            if total_read >= max_bytes:
                                break
                    except SSRFBlockedError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        return DiagnosticObservationCard(
                            input_url=initial_normalized_url,
                            final_url=current_target.normalized_url,
                            pinned_ip=current_target.pinned_ip,
                            scan_state="unreachable",
                            http_status=status_code,
                            content_type=media_type or None,
                            failure_reason=f"Stream read failed: {type(exc).__name__}",
                        )
                    raw_bytes = b"".join(chunks)
                finally:
                    response.close()
                break
    finally:
        if transport is None:
            active_transport.close()

    html_text = raw_bytes.decode("utf-8", errors="replace")
    parser = _StaticHTMLDiagnosticParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception as exc:  # noqa: BLE001
        return DiagnosticObservationCard(
            input_url=initial_normalized_url,
            final_url=current_target.normalized_url,
            pinned_ip=current_target.pinned_ip,
            scan_state="non_html",
            http_status=status_code,
            content_type=media_type or "text/html",
            failure_reason=f"Unable to parse HTML document: {type(exc).__name__}",
        )

    title_text, h1_text, findings = _build_completed_findings(parser)
    viewport_ok = "width=device-width" in parser.viewport_meta.lower()

    return DiagnosticObservationCard(
        input_url=initial_normalized_url,
        final_url=current_target.normalized_url,
        pinned_ip=current_target.pinned_ip,
        scanned_at=datetime.now(timezone.utc),
        scan_state="completed",
        http_status=status_code,
        content_type=media_type or "text/html",
        title_status="observed" if title_text else "not_observed",
        observed_title=title_text,
        h1_status="observed" if h1_text else "not_observed",
        observed_h1=h1_text,
        tap_to_call_status="observed" if parser.tel_links else "not_observed",
        observed_tel_links=parser.tel_links,
        mailto_status="observed" if parser.mailto_links else "not_observed",
        observed_mailto_links=parser.mailto_links,
        contact_form_status="observed" if parser.contact_form_fields else "not_observed",
        observed_form_fields=parser.contact_form_fields,
        viewport_meta_status="observed" if viewport_ok else "not_observed",
        observed_viewport_meta=parser.viewport_meta,
        cta_status="observed" if parser.cta_texts else "not_observed",
        observed_cta_texts=parser.cta_texts,
        findings=findings,
        disclaimer=STATIC_SCAN_DISCLAIMER,
    )
