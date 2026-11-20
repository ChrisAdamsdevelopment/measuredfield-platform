"""MF-062 offline tests. Provider responses are synthetic; no network or tokens."""
import json
from scripts import mcp_private_connect_smoke as smoke


def _responses(*, http=401, metadata=True, issuer="https://example.auth0.com/",
               resource=None, scopes=None, challenge=True):
    if resource is None:
        resource = smoke.PRODUCTION_ORIGIN + "/mcp"
    if scopes is None:
        scopes = ["mf:read"]
    doc = {"resource": resource, "authorization_servers": [issuer],
           "scopes_supported": scopes}
    return {
        "/healthz": smoke.PublicResult(200, {}, b'{"status":"ok"}'),
        "/readyz": smoke.PublicResult(200, {}, b'{"status":"ready"}'),
        "/mcp": smoke.PublicResult(http, {
            "www-authenticate": ('Bearer resource_metadata="https://spectracleanse.com/.well-known/oauth-protected-resource/mcp"'
                                 if challenge else "Basic")
        }),
        "/.well-known/oauth-protected-resource/mcp": smoke.PublicResult(
            200 if metadata else 404, {},
            json.dumps(doc).encode("utf-8") if metadata else b""),
    }


def test_valid_resource_proves_boundary_not_account_connection():
    result = smoke.analyze(_responses())
    assert result["server_ready_for_chatgpt_authorization"] is True
    assert result["mcp_denies_anonymous"] is True
    assert result["chatgpt_plugin_connected"] is False
    assert result["user_authentication_verified"] is False
    assert result["revocation_verified"] is False


def test_any_404_mcp_host_is_not_ready():
    result = smoke.analyze(_responses(http=404, metadata=False))
    assert result["server_ready_for_chatgpt_authorization"] is False
    assert result["mcp_http"] == 404
    assert result["connection_status"] == "server_boundary_incomplete"


def test_anonymous_mcp_200_is_always_refused():
    result = smoke.analyze(_responses(http=200))
    assert result["mcp_denies_anonymous"] is False
    assert result["server_ready_for_chatgpt_authorization"] is False


def test_no_bearer_resource_challenge_fails_even_if_401():
    assert not smoke.analyze(_responses(challenge=False))[
        "server_ready_for_chatgpt_authorization"]


def test_untrusted_resource_and_scope_are_not_reported_as_ready():
    assert not smoke.analyze(_responses(resource="https://other.example.com/mcp"))[
        "oauth_resource_metadata_valid"]
    assert not smoke.analyze(_responses(scopes=["openid"]))[
        "oauth_resource_metadata_valid"]


def test_invalid_or_empty_issuer_fails_closed():
    for issuer in ("http://example.org", "https://localhost",
                   "https://example.org/?key=SECRET", "https://example.com@localhost",
                   ""):
        assert not smoke.analyze(_responses(issuer=issuer))[
            "oauth_resource_metadata_valid"]


def test_bad_metadata_is_never_reflected_into_report():
    values = _responses()
    values["/.well-known/oauth-protected-resource/mcp"] = smoke.PublicResult(
        200, {}, b'{"secret":"VERY_PRIVATE","resource":999}')
    response = json.dumps(smoke.analyze(values))
    assert "VERY_PRIVATE" not in response
    assert "secret" not in response
    assert '"oauth_resource_metadata_valid": false' in response


def test_transport_errors_and_redirects_are_not_ready():
    for status in (None, 302, 503):
        values = _responses()
        values["/healthz"] = smoke.PublicResult(status, {}, issue="transport_error")
        assert not smoke.analyze(values)["app_healthy"]


def test_fetch_has_literal_path_allowlist(monkeypatch):
    def trap():
        raise AssertionError("should not open network")
    monkeypatch.setattr(smoke, "build_opener", trap)
    try:
        smoke.fetch_public("/admin/secret")
    except ValueError:
        pass
    else:
        raise AssertionError("unexpected path accepted")


def test_issuer_url_validation_rejects_ip_and_invalid_ports():
    # The provider URL grammar is strict; private IP hosts remain prohibited
    # by the pinned end-to-end provider config before release.
    assert not smoke._valid_issuer_url("https://127.0.0.1/")
    assert not smoke._valid_issuer_url("https://[::1]/")
    assert not smoke._valid_issuer_url("https://issuer.example.org:8443/")
    assert not smoke._valid_issuer_url("https://issuer.example.org/?x=1")
    assert smoke._valid_issuer_url("https://example.auth0.com/")


def test_challenge_must_point_to_exact_pinned_production_resource():
    values = _responses()
    values["/mcp"] = smoke.PublicResult(401, {
        "www-authenticate": (
            'Bearer resource_metadata="https://lookalike.example/'
            '.well-known/oauth-protected-resource/mcp"'
        ),
    })
    result = smoke.analyze(values)
    assert result["mcp_denies_anonymous"] is True
    assert result["mcp_bearer_challenge"] is False
    assert result["server_ready_for_chatgpt_authorization"] is False
