"""MF-077: opt-in MCP resource host integration, no network or real identity."""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.testclient import TestClient

from app import main as entry
from app.mcp_plugin import config as mcp_config
from app.mcp_plugin import server as mcp_server


@asynccontextmanager
async def _noop_lifespan(_app):
    yield


def _surface(label):
    web = FastAPI(lifespan=_noop_lifespan)

    @web.get("/{path:path}")
    async def page(path: str):
        return JSONResponse({"surface": label, "path": path})

    return web


def _mcp_mock():
    web = FastAPI(lifespan=_noop_lifespan)

    @web.get("/mcp")
    async def noauth():
        return PlainTextResponse("Bearer required", status_code=401)

    @web.get("/.well-known/oauth-protected-resource/mcp")
    async def resource():
        return JSONResponse({"resource": "https://app.example.net/mcp"})

    return web


@pytest.fixture
def surfaces(monkeypatch):
    settings = SimpleNamespace(
        app_host_set={"app.example.net"}, is_production=False,
    )
    monkeypatch.setattr(entry, "get_settings", lambda: settings)
    return _surface("main"), _surface("site"), _mcp_mock()


def test_opt_out_keeps_website_and_customer_sites_unmodified(surfaces):
    main, sites, _ = surfaces
    router = entry.HostRouter(main, sites)
    with TestClient(router, base_url="https://app.example.net") as client:
        assert client.get("/app", headers={"Host": "app.example.net"}).json()["surface"] == "main"
        assert client.get("/mcp", headers={"Host": "app.example.net"}).json()["surface"] == "main"
        assert client.get("/mcp", headers={"Host": "tenant.example.net"}).json()["surface"] == "site"


def test_enabled_boundary_is_host_pinned_and_not_a_customer_route(surfaces):
    main, sites, mcp = surfaces
    router = entry.HostRouter(main, sites, mcp_app=mcp, mcp_host="app.example.net")
    with TestClient(router, base_url="https://app.example.net") as client:
        assert client.get("/mcp", headers={"Host": "app.example.net"}).status_code == 401
        discovery = client.get("/.well-known/oauth-protected-resource/mcp",
                               headers={"Host": "app.example.net"})
        assert discovery.status_code == 200
        assert discovery.json()["resource"] == "https://app.example.net/mcp"
        for forbidden in ("tenant.example.net", "app.example.net.attacker.invalid"):
            assert client.get("/mcp", headers={"Host": forbidden}).status_code == 404
            assert client.get("/.well-known/oauth-protected-resource/mcp",
                              headers={"Host": forbidden}).status_code == 404
        assert client.get("/app", headers={"Host": "app.example.net"}).json()["surface"] == "main"
        assert client.get("/app", headers={"Host": "tenant.example.net"}).json()["surface"] == "site"


def test_malformed_or_duplicate_host_fails_without_mcp(surfaces):
    main, sites, mcp = surfaces
    router = entry.HostRouter(main, sites, mcp_app=mcp, mcp_host="app.example.net")
    with TestClient(router, base_url="https://app.example.net") as client:
        for bad in ("evil.example:bad", "app.example.net:443:443",
                    "app.example.net%0aother"):
            assert client.get("/mcp", headers={"Host": bad}).status_code == 400
        assert client.get("/mcp", headers=[
            ("Host", "app.example.net"), ("Host", "app.example.net"),
        ]).status_code == 400
        assert client.get("/mcp", headers={"Host": "app.example.net",
                                           "X-Forwarded-Host": "evil.example.net"}).status_code == 401


def test_flag_disabled_does_not_construct_resource(monkeypatch):
    monkeypatch.delenv("MF_ENABLE_REMOTE_MCP", raising=False)
    monkeypatch.setattr(mcp_server, "create_app",
                        lambda: pytest.fail("disabled flag built MCP"))
    assert entry._configured_mcp_resource(
        SimpleNamespace(app_host_set={"app.example.net"})) == (None, None)


@pytest.mark.parametrize("flag", ["1", "TRUE", "yes", "on", ""])
def test_ambiguous_enable_flags_are_rejected(monkeypatch, flag):
    monkeypatch.setenv("MF_ENABLE_REMOTE_MCP", flag)
    with pytest.raises(RuntimeError, match="MF_ENABLE_REMOTE_MCP"):
        entry._configured_mcp_resource(
            SimpleNamespace(app_host_set={"app.example.net"}))


def test_enabled_without_real_oauth_fails_closed(monkeypatch):
    monkeypatch.setenv("MF_ENABLE_REMOTE_MCP", "true")
    for key in ("MF_MCP_OAUTH_ISSUER", "MF_MCP_OAUTH_JWKS_URL", "MF_MCP_RESOURCE_URL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(mcp_config.McpPluginConfigurationError):
        entry._configured_mcp_resource(
            SimpleNamespace(app_host_set={"app.example.net"}))


def test_cannot_bind_mcp_to_unknown_customer_hostname(monkeypatch):
    monkeypatch.setenv("MF_ENABLE_REMOTE_MCP", "true")
    monkeypatch.setattr(mcp_config.McpPluginSettings, "from_env",
                        lambda: SimpleNamespace(resource_url="https://customer.example.net/mcp"))
    monkeypatch.setattr(mcp_server, "create_app",
                        lambda: pytest.fail("misrouted host constructed MCP"))
    with pytest.raises(RuntimeError, match="approved application host"):
        entry._configured_mcp_resource(
            SimpleNamespace(app_host_set={"app.example.net"}))


def test_complete_configuration_constructs_one_resource_app(monkeypatch):
    monkeypatch.setenv("MF_ENABLE_REMOTE_MCP", "true")
    monkeypatch.setattr(mcp_config.McpPluginSettings, "from_env",
                        lambda: SimpleNamespace(resource_url="https://app.example.net/mcp"))
    constructed = []
    monkeypatch.setattr(mcp_server, "create_app",
                        lambda: constructed.append(_mcp_mock()) or constructed[-1])
    resource, hostname = entry._configured_mcp_resource(
        SimpleNamespace(app_host_set={"app.example.net"}))
    assert resource is constructed[0]
    assert hostname == "app.example.net"
    assert len(constructed) == 1


def test_child_mcp_lifespan_is_entered_and_exited(monkeypatch):
    events = []
    @asynccontextmanager
    async def child_lifespan(_app):
        events.append("start")
        yield
        events.append("stop")

    mcp = FastAPI(lifespan=child_lifespan)
    main = FastAPI(lifespan=entry.lifespan)
    main.state.mcp_resource_app = mcp
    monkeypatch.setattr(entry, "get_settings", lambda: SimpleNamespace(
        database_url="", is_production=False, log_level="WARNING",
        validate_for_production=lambda: [], inline_jobs=False))
    with TestClient(main):
        assert events == ["start"]
    assert events == ["start", "stop"]
