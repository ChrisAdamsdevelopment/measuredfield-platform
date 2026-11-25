"""Real PG with signed SDK OAuth draft creation; no automatic estimate changes."""
import time
from types import SimpleNamespace
import pytest
from app.mcp_connector.gateway import ConnectorAccessDenied
from app.services import takeoff_proposals as proposals, takeoff_share as share
from .conftest import csrf_of,onboard_business
from .test_pg_mcp_plugin import _gateway,_identity,_link,ISSUER,RESOURCE
from .test_pg_takeoff_share import owner_id,PATH
from .test_takeoff_proposals import manual_packet


def _bearer(sub,write=True):
    identity=_identity(sub)
    from dataclasses import replace
    if write: identity=replace(identity,scopes=frozenset({"mf:read","mf:drafts.write"}))
    return SimpleNamespace(identity=identity,issued_at=int(time.time()),client_id="https://chatgpt.com/oauth/client.json")


def _create(bearer,org,sid,**kwargs):
    args=dict(section="field_entered_quantities",target_id="field_1",kind="field_quantity",
              quantity=20,rationale="Synthetic measurement correction")
    args.update(kwargs)
    return proposals.create_for_oauth(bearer,issuer=ISSUER,audience=RESOURCE,read_scope="mf:read",
        write_scope="mf:drafts.write",business_id=str(org),snapshot_id=sid,**args)


def test_owner_draft_consent_isolation_idempotency_and_revoke(platform,monkeypatch):
    a,org,_=onboard_business(platform,business="Fictional Draft A",owner_email="takeoff-draft-a@fictional.test")
    b,other,_=onboard_business(platform,business="Fictional Draft B",owner_email="takeoff-draft-b@fictional.test")
    user,other_user=owner_id(org),owner_id(other)
    _link(user,"oidc|draft-a"); _link(other_user,"oidc|draft-b")
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ENABLED","true")
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ORG_IDS",f"{org},{other}")
    headers={"X-CSRF-Token":csrf_of(a)}
    body={"consent":True,"packet":manual_packet()}
    read_only=a.post(PATH,json=body,headers=headers).json()["snapshot_id"]
    with pytest.raises(ConnectorAccessDenied):
        _create(_bearer("oidc|draft-a"),org,read_only)
    assert a.post(PATH,json={**body,"allow_drafts":True},headers=headers).status_code==400
    r=a.post(PATH,json={**body,"allow_drafts":True,"project_ref":"a"*64,"client_fingerprint":"b"*64},headers=headers)
    assert r.status_code==200,r.text
    sid=r.json()["snapshot_id"]
    with pytest.raises(ConnectorAccessDenied):
        _create(_bearer("oidc|draft-a",False),org,sid)
    with pytest.raises(ConnectorAccessDenied):
        _create(_bearer("oidc|draft-b"),org,sid)
    with pytest.raises(ConnectorAccessDenied):
        _create(_bearer("oidc|draft-b"),other,sid)
    with pytest.raises(share.ShareError):
        _create(_bearer("oidc|draft-a"),org,sid,section="measurements",target_id="m1")
    first=_create(_bearer("oidc|draft-a"),org,sid)
    assert first["status"]=="draft_only" and not first["already_exists"]
    assert _create(_bearer("oidc|draft-a"),org,sid)["already_exists"]
    group=a.get(PATH+"/"+sid+"/drafts").json()
    assert group["drafts"][0]["proposed_quantity"]==20
    assert group["drafts"][0]["expected_quantity"]==15
    assert group["project_ref"]=="a"*64 and group["client_fingerprint"]=="b"*64
    assert b.get(PATH+"/"+sid+"/drafts").status_code==404
    original=_gateway().takeoff_review_for(_identity("oidc|draft-a"),str(org),sid,"field_entered_quantities",0)
    assert original["items"][0]["quantity"]==15
    _sdk_proposal(org,sid)
    assert a.delete(PATH+"/"+sid,headers=headers).status_code==200
    assert a.get(PATH+"/"+sid+"/drafts").status_code==404
    with pytest.raises(ConnectorAccessDenied):
        _create(_bearer("oidc|draft-a"),org,sid)
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import Document
    with session_scope(org_id=org,user_id=user) as db:
        assert not db.scalars(select(Document).where(Document.content_type==proposals.CONTENT_TYPE)).all()


def _sdk_proposal(org,sid,*,subject="oidc|draft-a",target_id="field_1",quantity=21):
    from importlib.metadata import version
    if not version("mcp").startswith("2."):
        return
    from urllib.parse import urlsplit
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.testclient import TestClient
    from app.mcp_plugin.config import McpPluginSettings
    from app.mcp_plugin.tokens import OAuthJWTVerifier
    from app.mcp_plugin.links import linked_subject_user_id
    from app.mcp_plugin.server import create_server
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    settings=McpPluginSettings(issuer=ISSUER,jwks_url=ISSUER+"/.well-known/jwks.json",resource_url=RESOURCE)
    jwks=SimpleNamespace(get_signing_key_from_jwt=lambda _:SimpleNamespace(key=key.public_key()))
    verifier=OAuthJWTVerifier(settings,jwks_client=jwks,resolve_subject=linked_subject_user_id)
    now=int(time.time())
    def token(scopes):
        return jwt.encode({"iss":ISSUER,"sub":subject,"aud":RESOURCE,"resource":RESOURCE,
            "iat":now,"exp":now+600,"scope":scopes,"client_id":"https://chatgpt.com/oauth/client.json"},
            key,algorithm="RS256",headers={"kid":"synthetic"})
    host=urlsplit(RESOURCE).hostname; origin="https://"+host
    server=create_server(settings,token_verifier=verifier)
    app=server.streamable_http_app(json_response=True,host=host,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=[host],allowed_origins=[origin]))
    init={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26",
        "capabilities":{},"clientInfo":{"name":"takeoff-draft-fixture","version":"1"}}}
    args={"business_id":str(org),"snapshot_id":sid,"section":"field_entered_quantities",
        "target_id":target_id,"kind":"field_quantity","quantity":quantity,"rationale":"Signed SDK test"}
    with TestClient(app,base_url=origin) as client:
        h={"Accept":"application/json, text/event-stream","Authorization":"Bearer "+token("mf:read")}
        r=client.post("/mcp",json=init,headers=h);assert r.status_code==200,r.text
        if r.headers.get("mcp-session-id"): h["Mcp-Session-Id"]=r.headers["mcp-session-id"]
        call={"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"propose_takeoff_correction","arguments":args}}
        denied=client.post("/mcp",json=call,headers=h)
        assert denied.status_code==200 and denied.json()["result"].get("isError"),denied.text[:300]
        h["Authorization"]="Bearer "+token("mf:read mf:drafts.write")
        call["id"]=3
        r=client.post("/mcp",json=call,headers=h)
        assert r.status_code==200,r.text[:300]
        result=r.json()["result"]
        assert not result.get("isError") and "draft_only" in str(result),str(result)[:300]
        print("MCP_2_3_0_SIGNED_DRAFT_WRITE_PASS")


def test_real_chrome_mcp_draft_apply_and_stale_rejection(platform,monkeypatch,tmp_path):
    """Opt-in real Chrome + signed SDK + PG; no production or private plans."""
    import os
    if os.getenv("MF_TAKEOFF_DRAFT_BROWSER_SMOKE")!="1":
        return
    import subprocess
    import threading
    import urllib.request
    from pathlib import Path
    import uvicorn
    from app.main import create_main_app
    from scripts.takeoff_browser_smoke import free_port
    a,org,_=onboard_business(platform,business="Synthetic Contractor Business",
        owner_email="takeoff-draft-chrome@fictional.test",owner_name="Andrew Test")
    user=owner_id(org); subject="oidc|draft-chrome"
    _link(user,subject)
    gateway=_gateway(); ident=_identity(subject)
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ENABLED","true")
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ORG_IDS",str(org))
    monkeypatch.delenv("MF_TAKEOFF_ASSISTANT_ENABLED",raising=False)
    monkeypatch.delenv("MF_TAKEOFF_SYNTHETIC_DEMO_ENABLED",raising=False)
    root=Path(__file__).resolve().parents[1]
    source=(root/"scripts/takeoff_browser_smoke.cjs").read_text(encoding="utf-8")
    source=source.replace('const response=await page.goto(url,','await page.setCookie({name:"mf_session",value:process.env.MF_SYNTHETIC_BROWSER_SESSION,url});\n    const response=await page.goto(url,',1)
    end='    // Wait for actual IndexedDB completion, not elapsed download time.'
    tail=source[source.rfind('  }finally{\n    await browser.close();'):]
    source=source.split(end,1)[0]+"""
    await page.waitForSelector("#chatgpt-share-panel:not([hidden])");
    await page.click("#chatgpt-share-consent");
    await page.click("#chatgpt-draft-consent");
    await page.click("#share-review-packet");
    await page.waitForFunction(()=>document.querySelector("#chatgpt-share-status").textContent.startsWith("Shared immutable snapshot"),{timeout:20000});
    await page.waitForFunction(()=>[...document.querySelectorAll("#chatgpt-shared-reviews button")].some(e=>e.textContent==="Review ChatGPT drafts"));
    const openDrafts=async()=>page.evaluate(()=>[...document.querySelectorAll("#chatgpt-shared-reviews button")].find(e=>e.textContent==="Review ChatGPT drafts").click());
    for(let i=0;i<40;i++){
      await openDrafts();await wait(350);
      if(await page.$("#chatgpt-review-drafts button"))break;
    }
    await page.waitForSelector("#chatgpt-review-drafts button",{timeout:10000});
    assert.match(await page.$eval("#chatgpt-review-drafts",e=>e.textContent),/15 to 20 square feet/);
    await page.click("#chatgpt-review-drafts button");
    try{
      await page.waitForFunction(()=>document.querySelector("#chatgpt-share-status").textContent.startsWith("Owner-approved field correction saved locally"),{timeout:15000});
    }catch(e){console.log("DRAFT_APPLY_DIAGNOSTIC",await page.$eval("#chatgpt-share-status",e=>e.textContent));throw e;}
    const saved=await page.evaluate(async()=>{const db=await import("/static/takeoff/public/guided/storage.mjs");const [r]=await db.listProjects();return r.manuals.map(m=>[m.quantity,m.measured.area_sf]);});
    assert.deepEqual(saved,[[20,20]]);
    await openDrafts();await page.waitForSelector("#chatgpt-review-drafts button");
    await page.click("#chatgpt-review-drafts button");
    await page.waitForFunction(()=>document.querySelector("#chatgpt-share-status").textContent.includes("Snapshot is stale"));
    assert.deepEqual(errors,[]);assert.deepEqual(failed,[]);
    console.log("REAL_CHROME_MCP_DRAFT_APPROVE_APPLY_PERSIST_STALE_REJECT_PASS");
""" + tail
    js=tmp_path/"draft-smoke.cjs";js.write_text(source,encoding="utf-8")
    port=free_port()
    server=uvicorn.Server(uvicorn.Config(create_main_app(),host="127.0.0.1",port=port,log_level="error",lifespan="off"))
    worker=threading.Thread(target=server.run,daemon=True);worker.start()
    child=None
    try:
        cookie=a.cookies.get("mf_session")
        for _ in range(100):
            try:
                req=urllib.request.Request(f"http://127.0.0.1:{port}/takeoff",headers={"Cookie":"mf_session="+cookie})
                with urllib.request.urlopen(req,timeout=1) as response:
                    if b"chatgpt-draft-consent" in response.read():break
            except Exception:pass
            time.sleep(.1)
        else:raise RuntimeError("Browser fixture did not start")
        env={**os.environ,"MF_TAKEOFF_BROWSER_URL":f"http://127.0.0.1:{port}/takeoff","MF_SYNTHETIC_BROWSER_SESSION":cookie}
        child=subprocess.Popen(["node",str(js)],cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        for _ in range(120):
            receipts=gateway.takeoff_reviews_for(ident,str(org))
            if receipts:break
            if child.poll() is not None:break
            time.sleep(.25)
        if not receipts:
            stdout,stderr=child.communicate(timeout=5)
            print(stdout);raise AssertionError("No browser snapshot: "+stderr)
        sid=receipts[0]["snapshot_id"]
        manual=gateway.takeoff_review_for(ident,str(org),sid,"field_entered_quantities",0)["items"][0]
        assert manual["quantity"]==15
        _sdk_proposal(org,sid,subject=subject,target_id=manual["id"],quantity=20)
        stdout,stderr=child.communicate(timeout=60)
        print(stdout)
        assert child.returncode==0,stderr
        assert gateway.takeoff_review_for(ident,str(org),sid,"field_entered_quantities",0)["items"][0]["quantity"]==15
        assert a.delete(PATH+"/"+sid,headers={"X-CSRF-Token":csrf_of(a)}).status_code==200
        print("MCP_DRAFT_TO_AUTHENTICATED_BROWSER_DETERMINISTIC_UPDATE_PASS")
    finally:
        if child is not None and child.poll() is None:child.kill();child.communicate()
        server.should_exit=True;worker.join(timeout=8)
