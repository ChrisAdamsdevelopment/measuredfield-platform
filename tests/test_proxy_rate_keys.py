"""Regression tests: spoofed forwarding headers cannot alter app-layer IP keys."""
from starlette.requests import Request

from app.config import Settings
from app.deps import client_key
from app.routers.site_host import _client


def fake_request(socket_ip: str | None, forwarded: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "https",
        "path": "/",
        "query_string": b"",
        "headers": [(b"x-forwarded-for", forwarded.encode())],
        "server": ("example.test", 443),
        "client": (socket_ip, 12345) if socket_ip else None,
    }
    return Request(scope)


def test_forwarded_header_never_changes_same_peer_key():
    from_a = fake_request("203.0.113.5", "198.51.100.7")
    from_b = fake_request("203.0.113.5", "192.0.2.99, 198.51.100.8")
    for key_fn in (client_key, _client):
        assert key_fn(from_a) == key_fn(from_b)
        assert key_fn(from_a) != key_fn(fake_request("203.0.113.6", "198.51.100.7"))


def test_missing_socket_ip_uses_stable_unknown_key():
    for key_fn in (client_key, _client):
        assert key_fn(fake_request(None, "192.0.2.1")) == key_fn(
            fake_request(None, "198.51.100.20")
        )


def test_production_rejects_unverified_forwarded_header_trust():
    settings = Settings(env="production", trust_proxy_headers=True)
    assert any("MF_TRUST_PROXY_HEADERS" in issue for issue in settings.validate_for_production())
    settings = Settings(env="staging", trust_proxy_headers=True)
    assert any("MF_TRUST_PROXY_HEADERS" in issue for issue in settings.validate_for_production())


def test_production_has_no_proxy_error_when_untrusted_headers_disabled():
    settings = Settings(env="production", trust_proxy_headers=False)
    assert not any("MF_TRUST_PROXY_HEADERS" in issue for issue in settings.validate_for_production())
