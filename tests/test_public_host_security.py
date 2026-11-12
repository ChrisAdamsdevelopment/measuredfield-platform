"""Adversarial HTTP authority tests for the multi-host dispatch boundary."""
import asyncio

import pytest
from starlette.responses import PlainTextResponse

from app.main import HostRouter, canonical_host


@pytest.mark.parametrize("value, expected", [
    (b"Business.Example:443", "business.example"),
    (b"business.example.", "business.example"),
    (b"localhost:8000", "localhost"),
    (b"127.0.0.1:8000", "127.0.0.1"),
    (b"[::1]:8000", "::1"),
    (b"XN--example.example:443", "xn--example.example"),
    (b"example.com:bad", None),
    (b"example.com:0", None),
    (b"example.com:65536", None),
    (b"example.com:80:123", None),
    (b"example.com..", None),
    (b"example..com", None),
    (b"example.com,", None),
    (b"example.com,evil.invalid", None),
    (b"example.com\n", None),
    (b" example.com", None),
    (b"user@example.com", None),
    (b"foo/bar", None),
    (b"example.com%00", None),
    (b"evil.invalid@business.example", None),
    ("tést.example".encode("utf-8"), None),
    (b"[::1]evil", None),
])
def test_host_canonicalization(value, expected):
    assert canonical_host(value) == expected


def make_router():
    async def platform(scope, receive, send):
        return await PlainTextResponse("platform")(scope, receive, send)

    async def site(scope, receive, send):
        return await PlainTextResponse(
            "site: " + str(scope.get("mf.canonical_host", "MISSING"))
        )(scope, receive, send)

    return HostRouter(platform, site)


def invoke(headers, path="/"):
    router = make_router()
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "method": "GET", "path": path,
        "scheme": "http", "query_string": b"", "headers": headers,
        "client": ("127.0.0.1", 12345), "server": ("localhost", 8000),
        "root_path": "", "app": router,
    }
    asyncio.run(router(scope, receive, send))
    status = next(msg["status"] for msg in sent if msg["type"] == "http.response.start")
    body = b"".join(msg.get("body", b"") for msg in sent
                    if msg["type"] == "http.response.body").decode("utf-8")
    return status, body, sent


def test_route_uses_validated_host_not_untrusted_port():
    status, body, _ = invoke([(b"host", b"Business.Example:443")])
    assert status == 200
    assert body == "site: business.example"


@pytest.mark.parametrize("headers", [
    [],
    [(b"host", b"")],
    [(b"host", b"business.example:evil")],
    [(b"host", b"business.example"), (b"host", b"evil.example")],
    [(b"Host", b"business.example"), (b"host", b"business.example")],
])
def test_malformed_or_duplicated_hosts_rejected_before_dispatch(headers):
    status, body, _ = invoke(headers)
    assert status == 400
    assert "Invalid Host" in body


def test_invalid_host_also_rejected_on_health_endpoint():
    status, _, _ = invoke([(b"host", b"foo.com:malformed")], "/healthz")
    assert status == 400


def test_health_still_works_on_any_valid_host_without_database():
    status, body, _ = invoke([(b"host", b"synthetic.site.example")], "/healthz")
    assert status == 200
    assert '"status":"ok"' in body


def test_invalid_authority_response_has_security_headers():
    status, _, sent = invoke([(b"host", b"site.example:bad")])
    assert status == 400
    headers = dict(next(msg["headers"] for msg in sent
                        if msg["type"] == "http.response.start"))
    assert headers[b"x-content-type-options"] == b"nosniff"
