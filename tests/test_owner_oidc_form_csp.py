"""Regression: Auth0 form redirects must pass CSP without relaxing other pages."""
from types import SimpleNamespace

import pytest

from app.main import APP_CSP, _owner_link_form_csp
from app.services import mcp_oidc_enrollment as oidc


def _provider(monkeypatch, url: str):
    monkeypatch.setattr(
        oidc.OIDCOwnerSettings, "from_env",
        classmethod(lambda cls: SimpleNamespace(authorization_endpoint=url)),
    )


def test_owner_link_csp_allows_only_https_oidc_login_host(monkeypatch):
    _provider(monkeypatch, "https://login.example.org/authorize?x=y")
    result = _owner_link_form_csp()
    assert "form-action 'self' https://checkout.stripe.com https://login.example.org;" in result
    assert "script-src 'self'" in result
    assert "connect-src 'self'" in result
    assert APP_CSP == ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                       "connect-src 'self'; frame-src 'self'; form-action 'self' https://checkout.stripe.com; "
                       "base-uri 'none'; frame-ancestors 'self'; object-src 'none'")


@pytest.mark.parametrize("url", [
    "http://login.example.org/authorize",
    "https://malicious:password@login.example.org/authorize",
    "https://login.example.org:8443/authorize",
    "https://bad-host!@login.example.org/authorize",
])
def test_insecure_or_nonstandard_provider_has_no_csp_exception(monkeypatch, url):
    _provider(monkeypatch, url)
    assert _owner_link_form_csp() == APP_CSP


def test_unconfigured_provider_keeps_strict_csp(monkeypatch):
    def blocked(cls):
        raise oidc.OwnerEnrollmentUnavailable("not configured")
    monkeypatch.setattr(oidc.OIDCOwnerSettings, "from_env", classmethod(blocked))
    assert _owner_link_form_csp() == APP_CSP
