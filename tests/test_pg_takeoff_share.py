"""Real PostgreSQL: browser consent -> tenant document -> linked MCP gateway -> revoke."""
import pytest
from app.mcp_connector.gateway import ConnectorAccessDenied
from app.services import takeoff_share as share
from .conftest import client_for, csrf_of, onboard_business
from .test_pg_mcp_plugin import _gateway, _identity, _link
from .test_takeoff_share import packet

PATH = "/api/takeoff/shared-reviews"


def owner_id(org):
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import Membership
    with session_scope(org_id=org) as db:
        return db.scalar(select(Membership.user_id).where(Membership.org_id == org, Membership.role == "owner"))


def test_browser_to_linked_mcp_review_and_revocation(platform, monkeypatch):
    a, org_a, _ = onboard_business(platform, business="Fictional Takeoff A", owner_email="takeoff-share-a@fictional.test")
    b, org_b, _ = onboard_business(platform, business="Fictional Takeoff B", owner_email="takeoff-share-b@fictional.test")
    user_a, user_b = owner_id(org_a), owner_id(org_b)
    _link(user_a, "oidc|takeoff-share-a")
    _link(user_b, "oidc|takeoff-share-b")
    ida, idb = _identity("oidc|takeoff-share-a"), _identity("oidc|takeoff-share-b")
    gateway = _gateway()
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ENABLED", "true")
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ORG_IDS", f"{org_a},{org_b}")
    h = {"X-CSRF-Token": csrf_of(a)}
    hb = {"X-CSRF-Token": csrf_of(b)}
    body = {"consent": True, "packet": packet()}
    assert a.get("/api/capabilities").json()["chatgpt_snapshot_share"] is True
    assert client_for(platform).post(PATH, json=body).status_code == 401
    assert a.post(PATH, json=body).status_code == 403
    assert a.post(PATH, json={**body, "consent": False}, headers=h).status_code == 400
    assert a.post(PATH, json={**body, "packet": {**packet(), "pdf": "private"}}, headers=h).status_code == 400
    assert a.post(PATH, content=b"x"*(share.MAX_BODY+1), headers={**h,"Content-Type":"application/json"}).status_code == 413
    assert gateway.takeoff_reviews_for(ida, str(org_a)) == []
    response = a.post(PATH, json=body, headers=h)
    assert response.status_code == 200, response.text
    receipt = response.json()
    sid = receipt["snapshot_id"]
    assert a.post(PATH, json=body, headers=h).json() == receipt
    assert a.get(PATH).json()["snapshots"] == [receipt]
    assert gateway.takeoff_reviews_for(ida, str(org_a)) == [receipt]
    assert gateway.takeoff_reviews_for(idb, str(org_b)) == []
    _sdk_transport_check(org_a, user_a, sid)

    summary = gateway.takeoff_review_for(ida, str(org_a), sid, "summary", 0)
    assert summary["skill_readiness"] == "blocked"
    assert summary["section_counts"]["measurements"] == 1
    assert gateway.takeoff_review_for(ida, str(org_a), sid, "measurements", 0)["items"][0]["measured_quantity"] == 64
    assert gateway.takeoff_review_for(ida, str(org_a), sid, "findings", 0)["items"][0]["code"] == "PRICE_NOT_CONFIRMED"
    with pytest.raises(ConnectorAccessDenied):
        gateway.takeoff_review_for(idb, str(org_a), sid, "summary", 0)
    with pytest.raises(ConnectorAccessDenied):
        gateway.takeoff_review_for(idb, str(org_b), sid, "summary", 0)
    with pytest.raises(ConnectorAccessDenied):
        gateway.takeoff_reviews_for(_identity("unlinked"), str(org_a))
    assert b.delete(PATH+"/"+sid, headers=hb).status_code == 404
    assert a.delete(PATH+"/"+sid).status_code == 403
    assert a.delete(PATH+"/"+sid, headers=h).json() == {"revoked": True}
    with pytest.raises(ConnectorAccessDenied):
        gateway.takeoff_review_for(ida, str(org_a), sid, "summary", 0)
    assert a.get(PATH).json()["snapshots"] == []
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ORG_IDS", str(org_b))
    assert a.post(PATH, json=body, headers=h).status_code == 404
    with pytest.raises(ConnectorAccessDenied):
        gateway.takeoff_reviews_for(ida, str(org_a))


def _sdk_transport_check(org, user, sid):
    """Real signed OAuth/HTTP calls with SDK 2.3.0; run under production SDK venv."""
    from importlib.metadata import version
    if not version("mcp").startswith("2."):
        return
    import time
    from types import SimpleNamespace
    from urllib.parse import urlsplit
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.testclient import TestClient
    from app.mcp_plugin.config import McpPluginSettings
    from app.mcp_plugin.tokens import OAuthJWTVerifier
    from app.mcp_plugin.links import linked_subject_user_id
    from app.mcp_plugin.server import create_server
    from .test_pg_mcp_plugin import ISSUER, RESOURCE
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = McpPluginSettings(issuer=ISSUER, jwks_url=ISSUER+"/.well-known/jwks.json", resource_url=RESOURCE)
    jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key()))
    verifier = OAuthJWTVerifier(settings, jwks_client=jwks, resolve_subject=linked_subject_user_id)
    now = int(time.time())
    token = jwt.encode({"iss": ISSUER, "sub": "oidc|takeoff-share-a", "aud": RESOURCE,
        "resource": RESOURCE, "iat": now, "exp": now+600, "scope": "mf:read",
        "client_id": "https://chatgpt.com/oauth/client.json"}, key, algorithm="RS256", headers={"kid":"fixture"})
    host = urlsplit(RESOURCE).hostname
    origin = "https://"+host
    server = create_server(settings, token_verifier=verifier)
    app = server.streamable_http_app(json_response=True, host=host,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=[host],allowed_origins=[origin]))
    headers = {"Accept":"application/json, text/event-stream","Authorization":"Bearer "+token}
    init = {"jsonrpc":"2.0","id":1,"method":"initialize","params":{
        "protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"takeoff-fixture","version":"1"}}}
    with TestClient(app,base_url=origin) as client:
        r = client.post("/mcp",json=init,headers={"Accept":headers["Accept"]})
        assert r.status_code == 401
        r = client.post("/mcp",json=init,headers=headers)
        assert r.status_code == 200, r.text[:300]
        if r.headers.get("mcp-session-id"):
            headers["Mcp-Session-Id"] = r.headers["mcp-session-id"]
        listing = client.post("/mcp",json={"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}},headers=headers)
        assert listing.status_code==200
        assert "list_takeoff_reviews" in listing.text and "get_takeoff_review" in listing.text
        for num, name, args, marker in [
            (3,"list_takeoff_reviews",{"business_id":str(org)},sid),
            (4,"get_takeoff_review",{"business_id":str(org),"snapshot_id":sid,"section":"measurements"},"measured_quantity"),
        ]:
            r = client.post("/mcp",json={"jsonrpc":"2.0","id":num,"method":"tools/call","params":{"name":name,"arguments":args}},headers=headers)
            assert r.status_code==200, r.text[:300]
            result=r.json()["result"]
            assert not result.get("isError"), str(result)[:300]
            assert marker in str(result)
        print("MCP_2_3_0_SIGNED_HTTP_TAKEOFF_PASS")


def test_real_chrome_share_receipt_and_revoke(platform, monkeypatch, tmp_path):
    """Optional local Chrome acceptance: real browser + actual session + real PG.
    Normal CI exercises the same HTTP endpoints in the preceding test; Chrome
    is opt-in because it is not installed on the CI runner.
    """
    import os
    if os.getenv("MF_TAKEOFF_SHARE_BROWSER_SMOKE") != "1":
        return
    import subprocess
    import threading
    import time
    import urllib.request
    from pathlib import Path
    import uvicorn
    from app.main import create_main_app
    from scripts.takeoff_browser_smoke import free_port
    a, org, _ = onboard_business(platform, business="Synthetic Contractor Business",
        owner_email="takeoff-chrome@fictional.test", owner_name="Andrew Test")
    user = owner_id(org)
    _link(user, "oidc|takeoff-chrome")
    gateway = _gateway()
    ident = _identity("oidc|takeoff-chrome")
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ENABLED", "true")
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ORG_IDS", str(org))
    monkeypatch.delenv("MF_TAKEOFF_ASSISTANT_ENABLED", raising=False)
    monkeypatch.delenv("MF_TAKEOFF_SYNTHETIC_DEMO_ENABLED", raising=False)
    root = Path(__file__).resolve().parents[1]
    source = (root/"scripts/takeoff_browser_smoke.cjs").read_text(encoding="utf-8")
    source = source.replace('const response=await page.goto(url,', 'await page.setCookie({name:"mf_session",value:process.env.MF_SYNTHETIC_BROWSER_SESSION,url});\n    const response=await page.goto(url,', 1)
    end = '    // Wait for actual IndexedDB completion, not elapsed download time.'
    tail = source[source.rfind('  }finally{\n    await browser.close();'):]
    source = source.split(end, 1)[0] + """
    await page.waitForSelector("#chatgpt-share-panel:not([hidden])");
    await page.click("#share-review-packet");
    assert.match(await page.$eval("#chatgpt-share-status",e=>e.textContent),/Confirm sharing consent/);
    await page.click("#chatgpt-share-consent");
    await page.click("#share-review-packet");
    await page.waitForFunction(()=>document.querySelector("#chatgpt-share-status").textContent.startsWith("Shared immutable snapshot"),{timeout:20000});
    const list=await page.evaluate(async()=>{const r=await fetch("/api/takeoff/shared-reviews");return r.json()});
    assert.equal(list.snapshots.length,1);
    console.log("BROWSER_SHARED_SNAPSHOT_ID",list.snapshots[0].snapshot_id);
    assert.deepEqual(errors,[]);assert.deepEqual(failed,[]);
    console.log("REAL_CHROME_BROWSER_SNAPSHOT_SHARE_PASS");
""" + tail
    js = tmp_path/"share-smoke.cjs"
    js.write_text(source,encoding="utf-8")
    port=free_port()
    server=uvicorn.Server(uvicorn.Config(create_main_app(),host="127.0.0.1",port=port,log_level="error",lifespan="off"))
    worker=threading.Thread(target=server.run,daemon=True)
    worker.start()
    try:
        # The direct main-app fixture does not mount the composite /readyz route.
        # Verify the actual authenticated HTML instead of waiting on a 404.
        cookie = a.cookies.get("mf_session")
        last_error = None
        for _ in range(100):
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/takeoff",
                                             headers={"Cookie":"mf_session="+cookie})
                with urllib.request.urlopen(req,timeout=1) as response:
                    if b"chatgpt-share-panel" in response.read():
                        break
            except Exception as exc:
                last_error = type(exc).__name__
            time.sleep(.1)
        else:
            raise RuntimeError("Local authenticated browser server not ready: "+str(last_error))
        env={**os.environ,"MF_TAKEOFF_BROWSER_URL":f"http://127.0.0.1:{port}/takeoff",
             "MF_SYNTHETIC_BROWSER_SESSION":a.cookies.get("mf_session")}
        result=subprocess.run(["node",str(js)],cwd=root,env=env,capture_output=True,text=True,timeout=90)
        print(result.stdout)
        assert result.returncode==0, result.stderr
        receipts=gateway.takeoff_reviews_for(ident,str(org))
        assert len(receipts)==1
        sid=receipts[0]["snapshot_id"]
        evidence=gateway.takeoff_review_for(ident,str(org),sid,"field_entered_quantities",0)
        assert evidence["items"][0]["quantity"]==15
        summary=gateway.takeoff_review_for(ident,str(org),sid,"summary",0)
        assert summary["section_counts"]["scope_items"]>3
        assert summary["section_counts"]["material_lines"]>0
        assert a.delete(PATH+"/"+sid,headers={"X-CSRF-Token":csrf_of(a)}).json()=={"revoked":True}
        assert gateway.takeoff_reviews_for(ident,str(org))==[]
        print("BROWSER_TO_REAL_PG_TO_MCP_GATEWAY_AND_REVOCATION_PASS")
    finally:
        server.should_exit=True
        worker.join(timeout=8)
