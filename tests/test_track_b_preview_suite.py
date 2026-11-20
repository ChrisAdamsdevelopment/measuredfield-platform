"""Comprehensive unit, security, accessibility, and regression suite for Track B (MF-014).

Covers all 8 checks from the GPT-6 executable audit (27_ARGON_TRACK_B_EXECUTABLE_AUDIT_20261114.md)
plus deep SSRF, DNS-rebinding, redirect re-pinning, XSS, CSP, widget accessibility, and
sitegen/assessment compatibility tests using synthetic in-memory fixtures only.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import pytest
import httpcore
import httpx
from pydantic import ValidationError

from app.schemas.preview import (
    DiagnosticObservationCard,
    PreviewConceptProfile,
    WidgetParameter,
    WidgetParameterOption,
    WidgetSpec,
    adapt_to_sitegen_profile,
    build_assessment_result_from_preview,
    improvements_from_preview_result,
)
from app.services import assessment, sitegen
from app.services.preview_renderer import (
    DEMO_PREVIEW_JS,
    PREVIEW_CSP,
    render_concept_preview,
    render_diagnostic_card_html,
)
from app.services.scanner import (
    PinnedPublicIPTransport,
    SSRFBlockedError,
    ScannerError,
    build_pinned_request,
    inspect_public_url,
    validate_and_resolve_url,
    validate_ip_address,
)
from app.services.widgets import default_widget_for_industry, render_widgets_html


def _public_resolver(ip: str = "93.184.216.34"):
    def _resolve(host: str, port: int = 443, *_args):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    return _resolve


# ---------------------------------------------------------------------------
# 1. GPT-6 Audit Check #1: Ordinary public HTML parsing
# ---------------------------------------------------------------------------


def test_audit_check_1_parse_ordinary_public_html_title_and_tel_link():
    sample_html = """
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Prescott Valley Remodeling | Rimstone Builders</title>
    </head>
    <body>
      <header>
        <a href="tel:+1-928-555-0142">Call (928) 555-0142</a>
        <a href="mailto:office@rimstone.example">Email Us</a>
      </header>
      <main>
        <h1>Careful Kitchen &amp; Bath Remodeling in Prescott Valley</h1>
        <a href="#contact" class="btn">Request an Estimate</a>
        <form action="/contact" method="post">
          <label for="n">Name</label><input id="n" name="name" type="text">
          <label for="e">Email</label><input id="e" name="email" type="email">
          <label for="m">Project details</label><textarea id="m" name="message"></textarea>
          <button type="submit">Send Request</button>
        </form>
      </main>
    </body>
    </html>
    """
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            html=sample_html,
        )

    card = inspect_public_url(
        "https://rimstone.example/",
        resolver=_public_resolver("93.184.216.34"),
        transport=httpx.MockTransport(handler),
    )
    assert card.scan_state == "completed"
    assert card.http_status == 200
    assert card.pinned_ip == "93.184.216.34"
    assert card.final_url == "https://rimstone.example/"
    assert card.title_status == "observed"
    assert card.observed_title == "Prescott Valley Remodeling | Rimstone Builders"
    assert card.h1_status == "observed"
    assert card.observed_h1 == "Careful Kitchen & Bath Remodeling in Prescott Valley"
    assert card.tap_to_call_status == "observed"
    assert card.observed_tel_links == ["+1-928-555-0142"]
    assert card.mailto_status == "observed"
    assert card.observed_mailto_links == ["office@rimstone.example"]
    assert card.viewport_meta_status == "observed"
    assert card.contact_form_status == "observed"
    assert "input:name" in card.observed_form_fields
    assert "input:email" in card.observed_form_fields
    assert "textarea:message" in card.observed_form_fields
    assert card.cta_status == "observed"
    assert len(seen_requests) == 1


# ---------------------------------------------------------------------------
# 2. GPT-6 Audit Check #2: Disallow unverified demo pricing and claims
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_copy",
    [
        "Typical remodel costs $12,000 to $25,000",
        "Flat rate 250 USD service call",
        "Estimated 16 hours of labor",
        "Guaranteed #1 ranking in local search",
        "Reach page 1 on Google in 7 days",
        "No permit required for this addition",
    ],
)
def test_audit_check_2_disallow_unverified_demo_pricing_and_claims(bad_copy: str):
    with pytest.raises(ValidationError):
        WidgetParameterOption(
            value="bad_opt",
            label="Option label",
            scope_note=bad_copy,
        )

    with pytest.raises(ValidationError):
        PreviewConceptProfile(
            name="Test Co",
            industry="construction",
            intro=bad_copy,
        )


def test_widget_spec_forbids_owner_verified_pricing_flag_in_track_b():
    base = default_widget_for_industry("construction").model_dump()
    base["owner_verified_pricing"] = True
    with pytest.raises(ValidationError):
        WidgetSpec.model_validate(base)


# ---------------------------------------------------------------------------
# 3. GPT-6 Audit Check #3: Pinned IP transport prevents DNS rebinding
# ---------------------------------------------------------------------------


def test_audit_check_3_pinned_ip_transport_prevents_dns_rebinding():
    """Verify DNS is resolved ONCE and outbound HTTPX request connects to the pinned IP, not hostname."""
    dns_calls: list[str] = []

    def rebinding_resolver(host: str, port: int = 443, *_args):
        dns_calls.append(host)
        if len(dns_calls) == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        # Second lookup simulates DNS rebinding to loopback!
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    captured_requests: list[httpx.Request] = []

    def transport_handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            html="<html><head><title>Pinned</title></head><body><h1>Pinned Host</h1></body></html>",
        )

    card = inspect_public_url(
        "https://rebind-victim.example/welcome?ref=1",
        resolver=rebinding_resolver,
        transport=httpx.MockTransport(transport_handler),
    )

    # DNS resolver must only be called once
    assert dns_calls == ["rebind-victim.example"]
    assert len(captured_requests) == 1
    req = captured_requests[0]

    # Outbound request URL host MUST be the pinned IP literal, NOT the domain name
    assert req.url.host == "93.184.216.34"
    assert req.url.path == "/welcome"
    assert req.url.query == b"ref=1"
    # Original Host header and TLS SNI hostname MUST be preserved
    assert req.headers["Host"] == "rebind-victim.example"
    assert req.extensions["sni_hostname"] == "rebind-victim.example"
    assert req.extensions["pinned_ip"] == "93.184.216.34"
    assert card.final_url == "https://rebind-victim.example/welcome?ref=1"


def test_pinned_public_ip_transport_and_network_backend_enforce_ip_and_sni():
    """Verify PinnedPublicIPTransport passes numeric IP to connect_tcp and domain to start_tls."""
    connect_calls: list[tuple[str, int]] = []
    tls_calls: list[str] = []

    class FakeStream(httpcore.NetworkStream):
        def __init__(self, peer_ip: str = "93.184.216.34") -> None:
            self._peer_ip = peer_ip
            self._responded = False

        def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
            if not self._responded:
                self._responded = True
                body = b"<html><head><title>OK</title></head></html>"
                return (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/html; charset=utf-8\r\n"
                    + f"Content-Length: {len(body)}\r\n".encode("ascii")
                    + b"Connection: close\r\n\r\n"
                    + body
                )
            return b""

        def write(self, buffer: bytes, timeout: float | None = None) -> None:
            return None

        def close(self) -> None:
            return None

        def start_tls(
            self,
            ssl_context,
            server_hostname: str | None = None,
            timeout: float | None = None,
        ) -> httpcore.NetworkStream:
            tls_calls.append(server_hostname or "")
            return self

        def get_extra_info(self, info: str):
            if info == "socket":
                class DummySock:
                    def __init__(self, ip: str) -> None:
                        self._ip = ip

                    def getpeername(self):
                        return (self._ip, 443)

                return DummySock(self._peer_ip)
            return None

    class FakeBackend(httpcore.NetworkBackend):
        def __init__(self, peer_ip: str = "93.184.216.34") -> None:
            self._peer_ip = peer_ip

        def connect_tcp(self, host: str, port: int, **_kwargs) -> httpcore.NetworkStream:
            connect_calls.append((host, port))
            return FakeStream(self._peer_ip)

    transport = PinnedPublicIPTransport(network_backend=FakeBackend("93.184.216.34"))
    try:
        card = inspect_public_url(
            "https://secure- contractor.example/".replace(" ", ""),
            resolver=_public_resolver("93.184.216.34"),
            transport=transport,
        )
        assert card.scan_state == "completed"
        assert card.observed_title == "OK"
        # connect_tcp received the pinned numeric IP, never the hostname
        assert connect_calls == [("93.184.216.34", 443)]
        # start_tls received the original domain for SNI and certificate verification
        assert tls_calls == ["secure-contractor.example"]
    finally:
        transport.close()

    # Now verify fail-closed if underlying socket peername mismatches or is private
    bad_peer_transport = PinnedPublicIPTransport(network_backend=FakeBackend("127.0.0.1"))
    try:
        with pytest.raises(SSRFBlockedError):
            inspect_public_url(
                "https://secure-contractor.example/",
                resolver=_public_resolver("93.184.216.34"),
                transport=bad_peer_transport,
            )
    finally:
        bad_peer_transport.close()


def test_unpinned_request_to_pinned_transport_fails_closed():
    transport = PinnedPublicIPTransport()
    try:
        # Request built with hostname instead of pinned IP must fail closed immediately
        unpinned_req = httpx.Request("GET", "https://example.com/")
        with pytest.raises(SSRFBlockedError):
            transport.handle_request(unpinned_req)
    finally:
        transport.close()


# ---------------------------------------------------------------------------
# 4. GPT-6 Audit Check #4: Private IP literal exception is NEVER swallowed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "literal_url",
    [
        "http://127.0.0.1/",
        "https://127.0.0.1/admin",
        "http://127.0.1.1/",
        "http://127.1/",
        "http://0177.0.0.1/",
        "http://0x7f.0.0.1/",
        "http://2130706433/",
        "http://0.0.0.0/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/",
        "http://100.100.100.200/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:169.254.169.254]/",
        "http://[fd00:ec2::254]/",
        "http://[fe80::1]/",
        "http://93.184.216.34/",  # Even public IP literals are rejected in Release 1
    ],
)
def test_audit_check_4_private_ip_literal_not_swallowed_even_with_public_mock_resolver(
    literal_url: str,
):
    """Even if a mocked resolver falsely returns a public IP, literal IPs MUST raise SSRFBlockedError."""
    assert not issubclass(SSRFBlockedError, ValueError)
    resolver_called = False

    def misleading_public_resolver(host: str, port: int = 80, *_args):
        nonlocal resolver_called
        resolver_called = True
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    transport_called = False

    def forbidden_transport(_request: httpx.Request) -> httpx.Response:
        nonlocal transport_called
        transport_called = True
        raise AssertionError("Transport must never be called for blocked URL")

    with pytest.raises(SSRFBlockedError):
        validate_and_resolve_url(literal_url, resolver=misleading_public_resolver)

    with pytest.raises(SSRFBlockedError):
        inspect_public_url(
            literal_url,
            resolver=misleading_public_resolver,
            transport=httpx.MockTransport(forbidden_transport),
        )

    assert resolver_called is False
    assert transport_called is False


# ---------------------------------------------------------------------------
# 5. GPT-6 Audit Check #5: Unreachable/unknown page produces NO 'not_observed'
# ---------------------------------------------------------------------------


def test_audit_check_5_unreachable_or_unknown_observation_never_produces_not_observed_improvements():
    def failing_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("Connection timed out", request=request)

    card = inspect_public_url(
        "https://unreachable-contractor.example/",
        resolver=_public_resolver("93.184.216.34"),
        transport=httpx.MockTransport(failing_transport),
    )
    assert card.scan_state == "unreachable"
    assert card.title_status == "unable_to_determine"
    assert card.h1_status == "unable_to_determine"
    assert card.tap_to_call_status == "unable_to_determine"
    assert card.mailto_status == "unable_to_determine"
    assert card.contact_form_status == "unable_to_determine"
    assert card.viewport_meta_status == "unable_to_determine"
    assert card.cta_status == "unable_to_determine"
    assert card.findings == []

    result = build_assessment_result_from_preview(
        card, entry_path="existing_site", business_name="Unreachable Co"
    )
    assert result["understood"] is False
    assert result["recommended_offer"] == "consult"
    assert result["obstacles"] == []
    assert assessment.improvements_from(result) == []
    assert improvements_from_preview_result(result) == []


# ---------------------------------------------------------------------------
# 6. GPT-6 Audit Check #6: Search box outside/inside form is NOT a contact form
# ---------------------------------------------------------------------------


def test_audit_check_6_search_box_outside_or_inside_form_not_counted_as_contact_form():
    html_with_search_only = """
    <!doctype html>
    <html>
    <head>
      <title>Rimstone Supply</title>
      <meta name="viewport" content="width=device-width, initial-scale=1">
    </head>
    <body>
      <h1>Building Materials in Prescott</h1>
      <!-- Loose inputs outside any <form> tag -->
      <div class="header-search">
        <input type="search" name="q" placeholder="Search catalog...">
        <input type="email" name="newsletter_email" placeholder=" Loose email input not in a form">
      </div>
      <!-- Search form inside <form> -->
      <form role="search" action="/search" method="get">
        <input type="search" name="q" placeholder="Find products">
        <button type="submit">Search</button>
      </form>
      <!-- Another generic search form without role="search" -->
      <form action="/find" method="get">
        <input type="text" name="search" placeholder="Keyword">
        <input type="submit" value="Go">
      </form>
    </body>
    </html>
    """
    card = inspect_public_url(
        "https://rimstonesupply.example/",
        resolver=_public_resolver("93.184.216.34"),
        transport=httpx.MockTransport(
            lambda _req: httpx.Response(200, headers={"Content-Type": "text/html"}, html=html_with_search_only)
        ),
    )
    assert card.scan_state == "completed"
    assert card.contact_form_status == "not_observed"
    assert card.observed_form_fields == []


# ---------------------------------------------------------------------------
# 7. GPT-6 Audit Check #7: Fully observed page does NOT invent fallback obstacle
# ---------------------------------------------------------------------------


def test_audit_check_7_fully_observed_page_produces_zero_fabricated_obstacles():
    complete_html = """
    <!doctype html>
    <html>
    <head>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Granite Peak Cleaning — Prescott</title>
    </head>
    <body>
      <h1>Dependable Home &amp; Office Cleaning in Prescott</h1>
      <p><a href="tel:+19285550199">Call (928) 555-0199</a></p>
      <form action="/inquire" method="post">
        <input type="text" name="name" required>
        <input type="tel" name="phone" required>
        <textarea name="message" required></textarea>
        <button type="submit">Request a Quote</button>
      </form>
    </body>
    </html>
    """
    card = inspect_public_url(
        "https://granitepeakcleaning.example/",
        resolver=_public_resolver("93.184.216.34"),
        transport=httpx.MockTransport(
            lambda _req: httpx.Response(200, headers={"Content-Type": "text/html"}, html=complete_html)
        ),
    )
    assert card.scan_state == "completed"
    assert all(f.status == "observed" for f in card.findings)

    result = build_assessment_result_from_preview(
        card, entry_path="existing_site", business_name="Granite Peak Cleaning"
    )
    assert result["understood"] is True
    assert result["obstacles"] == []
    assert result["recommended_offer"] == "consult"
    assert assessment.improvements_from(result) == []
    assert improvements_from_preview_result(result) == []


# ---------------------------------------------------------------------------
# 8. GPT-6 Audit Check #8: HTTP 503 error page is unable_to_determine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status_code", [403, 404, 429, 500, 502, 503])
def test_audit_check_8_http_error_page_yields_unable_to_determine_not_missing_features(
    status_code: int,
):
    error_html = """
    <!doctype html>
    <html>
    <head><title>503 Service Unavailable</title></head>
    <body><h1>Service Temporarily Unavailable</h1><p>Please try again later.</p></body>
    </html>
    """
    card = inspect_public_url(
        "https://maintenance.example/",
        resolver=_public_resolver("93.184.216.34"),
        transport=httpx.MockTransport(
            lambda _req: httpx.Response(
                status_code,
                headers={"Content-Type": "text/html; charset=utf-8"},
                html=error_html,
            )
        ),
    )
    assert card.scan_state == "http_error"
    assert card.http_status == status_code
    assert card.title_status == "unable_to_determine"
    assert card.h1_status == "unable_to_determine"
    assert card.tap_to_call_status == "unable_to_determine"
    assert card.contact_form_status == "unable_to_determine"
    assert card.viewport_meta_status == "unable_to_determine"
    assert card.cta_status == "unable_to_determine"
    assert card.findings == []

    result = build_assessment_result_from_preview(
        card, entry_path="existing_site", business_name="Maintenance Co"
    )
    assert result["obstacles"] == []
    assert result["understood"] is False


# ---------------------------------------------------------------------------
# 9. Additional SSRF, IPv6, Dual-Record DNS, and Redirect Re-pinning Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "blocked_ip",
    [
        "0.0.0.0",
        "10.255.255.1",
        "100.64.0.1",
        "100.100.100.200",
        "127.0.0.1",
        "169.254.169.254",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.0.1",
        "198.18.0.1",
        "224.0.0.1",
        "255.255.255.255",
        "::",
        "::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "::ffff:169.254.169.254",
        "64:ff9b::127.0.0.1",
        "2001::1",
        "2001:db8::1",
        "2002:7f00:1::1",
        "fc00::1",
        "fd00:ec2::254",
        "fe80::1",
        "fe80::1%eth0",
        "ff02::1",
    ],
)
def test_validate_ip_address_blocks_all_non_global_addresses(blocked_ip: str):
    with pytest.raises(SSRFBlockedError):
        validate_ip_address(blocked_ip)


@pytest.mark.parametrize(
    "bad_url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file.txt",
        "gopher://example.com:70/",
        "javascript:alert(1)",
        "https://user:secret@example.com/",
        "https://example.com:8443/",
        "http://example.com:8080/",
        "http://example.com:443/",
        "https://example.com:80/",
        "http://localhost/",
        "http://sub.localhost/",
        "http://printer.local/",
        "http://service.internal/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://intranet/",
    ],
)
def test_validate_and_resolve_url_blocks_unsafe_schemes_ports_and_hosts(bad_url: str):
    with pytest.raises(SSRFBlockedError):
        validate_and_resolve_url(bad_url, resolver=_public_resolver("93.184.216.34"))


def test_mixed_public_and_private_dns_records_fail_closed():
    """If DNS returns both a public IP and a private IP, validation must fail closed."""
    def mixed_resolver(host: str, port: int = 443, *_args):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.42", port)),
        ]

    with pytest.raises(SSRFBlockedError):
        validate_and_resolve_url("https://mixed.example/", resolver=mixed_resolver)


def test_redirect_to_private_ip_or_metadata_is_blocked_before_request():
    requests_sent: list[httpx.Request] = []

    def redirect_to_metadata(request: httpx.Request) -> httpx.Response:
        requests_sent.append(request)
        return httpx.Response(
            302,
            headers={"Location": "http://169.254.169.254/latest/meta-data/iam/"},
        )

    with pytest.raises(SSRFBlockedError):
        inspect_public_url(
            "https://public-start.example/",
            resolver=_public_resolver("93.184.216.34"),
            transport=httpx.MockTransport(redirect_to_metadata),
        )

    # Only the initial public hop was sent; the private redirect was blocked pre-flight
    assert len(requests_sent) == 1
    assert requests_sent[0].url.host == "93.184.216.34"


def test_valid_public_redirect_re_resolves_and_repins_each_hop():
    hop_ips = {
        "oldbrand.example": "93.184.216.34",
        "www.newbrand.example": "93.184.216.99",
    }

    def multi_host_resolver(host: str, port: int = 443, *_args):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (hop_ips[host], port))]

    seen_requests: list[httpx.Request] = []

    def redirect_handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        if len(seen_requests) == 1:
            return httpx.Response(
                301,
                headers={"Location": "https://www.newbrand.example/home"},
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            html="<html><head><title>New Brand</title></head><body><h1>Welcome</h1></body></html>",
        )

    card = inspect_public_url(
        "https://oldbrand.example/",
        resolver=multi_host_resolver,
        transport=httpx.MockTransport(redirect_handler),
    )
    assert card.scan_state == "completed"
    assert card.final_url == "https://www.newbrand.example/home"
    assert card.pinned_ip == "93.184.216.99"
    assert len(seen_requests) == 2
    assert seen_requests[0].url.host == "93.184.216.34"
    assert seen_requests[0].headers["Host"] == "oldbrand.example"
    assert seen_requests[1].url.host == "93.184.216.99"
    assert seen_requests[1].headers["Host"] == "www.newbrand.example"
    assert seen_requests[1].extensions["sni_hostname"] == "www.newbrand.example"


# ---------------------------------------------------------------------------
# 10. Widgets, Preview Renderer, CSP, XSS Escaping, and Sitegen/Assessment Adapters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_industry", "expected_industry"),
    [
        ("construction", "construction"),
        ("remodeling", "construction"),
        ("cleaning", "cleaning"),
        ("professional_services", "professional"),
        ("retail", "retail"),
        ("general", "other"),
    ],
)
def test_default_widget_for_industry_and_html_accessibility(
    raw_industry: str, expected_industry: str
):
    widget = default_widget_for_industry(raw_industry)
    assert widget.industry == expected_industry
    assert widget.owner_verified_pricing is False
    html_out = render_widgets_html([widget])
    assert 'data-mf-widget="scope-explorer"' in html_out
    assert 'role="region"' in html_out
    assert 'aria-live="polite"' in html_out
    assert "<fieldset" in html_out
    assert "<legend" in html_out
    assert "style=" not in html_out.lower()


def test_render_concept_preview_xss_escaping_no_network_and_csp_compliance():
    malicious_input = {
        "name": ' Rimstone <script>alert("xss")</script> ',
        "city": "",  # Unconfirmed city
        "industry": "remodeling",
        "color": "copper",
        "headline": 'Quality work <img src=x onerror=alert(1)>',
        "services": "Kitchen Remodels | Custom cabinets <script>evil()</script>",
    }
    card = inspect_public_url(
        "https://rimstone.example/",
        resolver=_public_resolver("93.184.216.34"),
        transport=httpx.MockTransport(
            lambda _req: httpx.Response(
                200,
                headers={"Content-Type": "text/html"},
                html='<html><head><title><script>alert("t")</script></title></head><body><h1>Heading</h1></body></html>',
            )
        ),
    )

    bundle = render_concept_preview(malicious_input, card=card, asset_base="/preview-assets/")
    html_doc = bundle["html"]
    css_doc = bundle["css"]
    js_doc = bundle["js"]

    # 1. All untrusted script tags escaped
    assert '<script>alert("xss")</script>' not in html_doc
    assert '<script>evil()</script>' not in html_doc
    assert '<script>alert("t")</script>' not in html_doc
    assert "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;" in html_doc

    # 2. Prominent concept preview banner & unconfirmed field disclosure
    assert "NOT LIVE — CONCEPT PREVIEW ONLY" in html_doc
    assert "city" in bundle["unconfirmed_fields"]
    assert "contact_method" in bundle["unconfirmed_fields"]

    # 3. Widget is visibly rendered inside the preview HTML
    assert 'data-mf-widget="scope-explorer"' in html_doc
    assert 'id="scope-explorer"' in html_doc

    # 4. No inline style attributes or <style> tags (strict APP_CSP / PREVIEW_CSP compliance)
    assert re.search(r"\bstyle\s*=", html_doc, re.IGNORECASE) is None
    assert "<style" not in html_doc.lower()
    assert "#a55c36" in css_doc  # copper palette applied in external CSS

    # 5. No /api/lead or /api/event form action or JS calls
    assert "api/lead" not in html_doc
    assert "api/event" not in html_doc
    assert 'data-mf-demo-form="true"' in html_doc
    for forbidden_token in ("fetch(", "XMLHttpRequest", "sendBeacon", "api/lead", "api/event"):
        assert forbidden_token not in js_doc
    assert js_doc == DEMO_PREVIEW_JS
    assert "form-action 'none'" in PREVIEW_CSP


def test_adapt_to_sitegen_profile_and_legacy_render_byte_compatibility():
    raw_owner = {
        "business_name": "Prescott Canyon Builders",
        "city": "Prescott, AZ",
        "industry": "remodeling",
        "color": "pine",
        "response_window": "one_business_day",
        "phone": "(928) 555-0188",
        "email": "",
        "services": [
            {"name": "Custom Decks", "description": "Composite and timber decks."},
            {"name": "Kitchen Remodels", "description": "Cabinetry, tile, and layout updates."},
        ],
        "structured_intake": True,
    }
    adapted = adapt_to_sitegen_profile(raw_owner)
    assert adapted["industry"] == "construction"
    assert adapted["color"] == "pine"
    assert adapted["response_window"] == "one_business_day"

    # Must pass sitegen.validate_profile and produce identical output to sitegen.render
    revalidated = sitegen.validate_profile(adapted)
    assert adapted == revalidated
    html1, css1 = sitegen.render(adapted)
    html2, css2 = sitegen.render(revalidated)
    assert (html1, css1) == (html2, css2)

    # Rejects hex color strings or missing contact details at claim boundary
    with pytest.raises(sitegen.ProfileError):
        adapt_to_sitegen_profile({**raw_owner, "color": "#286854"})

    with pytest.raises(sitegen.ProfileError):
        adapt_to_sitegen_profile({**raw_owner, "phone": "", "email": ""})


def test_provenance_preserved_in_assessment_and_improvements():
    partial_html = "<html><head><title>Only Title</title></head><body><p>Hello</p></body></html>"
    card = inspect_public_url(
        "https://partial-site.example/",
        resolver=_public_resolver("93.184.216.34"),
        transport=httpx.MockTransport(
            lambda _req: httpx.Response(200, headers={"Content-Type": "text/html"}, html=partial_html)
        ),
    )
    result = build_assessment_result_from_preview(
        card, entry_path="existing_site", business_name="Partial Site Co"
    )
    assert result["understood"] is True
    assert len(result["obstacles"]) > 0
    for ob in result["obstacles"]:
        assert ob["evidence_status"] == "system_recorded"
        assert ob["evidence_status"] != "owner_stated"

    # Verify compatibility with existing assessment.improvements_from() AND provenance helper
    legacy_items = assessment.improvements_from(result)
    assert len(legacy_items) == len(result["obstacles"])

    provenance_items = improvements_from_preview_result(result)
    assert len(provenance_items) == len(result["obstacles"])
    assert all(item["evidence_status"] == "system_recorded" for item in provenance_items)
