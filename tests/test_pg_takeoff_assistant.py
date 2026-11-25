"""Actual authenticated route, PostgreSQL reservations, offline provider fixture."""
from app.services import takeoff_assistant as a
from .conftest import client_for, csrf_of, onboard_business

def test_assistant_authorization_consent_and_durable_limits(platform,monkeypatch):
    owner,org,_=onboard_business(platform,business="Synthetic Remodel",owner_email="takeoff-assistant@fictional.example")
    other,other_org,_=onboard_business(platform,business="Unrelated Synthetic",owner_email="takeoff-other@fictional.example")
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ENABLED","true")
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ORG_IDS",str(org))
    monkeypatch.setenv("MF_TAKEOFF_ASSISTANT_ENABLED","true")
    monkeypatch.setenv("NVIDIA_API_KEY","synthetic-offline-fixture")
    monkeypatch.setenv("MF_TAKEOFF_EXTERNAL_DATA_APPROVED","true")
    calls=[]
    async def fake(body):
        calls.append(body)
        return {"actual_inference":True,"reply":"Synthetic fixture","work_items":[]}
    monkeypatch.setattr(a,"infer",fake)
    path="/api/takeoff/assistant"
    body={"consent":True,"question":"Plan a synthetic room"}
    headers={"X-CSRF-Token":csrf_of(owner)}
    assert client_for(platform).post(path,json=body).status_code==401
    assert owner.post(path,json=body).status_code==403
    assert other.post(path,json=body,headers={"X-CSRF-Token":csrf_of(other)}).status_code==404
    assert owner.post(path,json={**body,"consent":False},headers=headers).status_code==400
    assert owner.get("/api/capabilities").json()["assistant"] is True
    assert len(calls)==0
    for _ in range(20):
        assert owner.post(path,json=body,headers=headers).status_code==200
    assert owner.post(path,json=body,headers=headers).status_code==429
    assert len(calls)==20
    for _ in range(25):
        assert owner.post(path,json=body,headers=headers).status_code==429
    # An exhausted organization must not consume the shared quota.
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ORG_IDS",f"{org},{other_org}")
    assert other.post(path,json=body,headers={"X-CSRF-Token":csrf_of(other)}).status_code==200
    assert len(calls)==21

    # Exhaust the global limit with a second organization, then use a third.
    third,third_org,_=onboard_business(platform,business="Third Synthetic",owner_email="takeoff-third@fictional.example")
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ORG_IDS",f"{org},{other_org},{third_org}")
    second_headers={"X-CSRF-Token":csrf_of(other)}
    for _ in range(19):
        assert other.post(path,json=body,headers=second_headers).status_code==200
    assert third.post(path,json=body,headers={"X-CSRF-Token":csrf_of(third)}).status_code==429
    assert len(calls)==40

def test_demo_only_refuses_project_data(platform,monkeypatch):
    owner,org,_=onboard_business(platform,business="Synthetic Demo",owner_email="takeoff-demo@fictional.example")
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ENABLED","true")
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ORG_IDS",str(org))
    monkeypatch.setenv("MF_TAKEOFF_SYNTHETIC_DEMO_ENABLED","true")
    monkeypatch.delenv("MF_TAKEOFF_EXTERNAL_DATA_APPROVED",raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY","synthetic-offline-fixture")
    calls=[]
    async def fake(body):
        calls.append(body)
        return {"actual_inference":True,"reply":"Fictional response","work_items":[]}
    monkeypatch.setattr(a,"infer",fake)
    # Global quota belongs to prior test's database; this test has its own fixture.
    headers={"X-CSRF-Token":csrf_of(owner)}
    path="/api/takeoff/assistant"
    for body in [{"consent":True,"question":"PRIVATE_TEST_SENTINEL"},{"consent":True,"demo":True,"brief":"PRIVATE_TEST_SENTINEL"}]:
        assert owner.post(path,json=body,headers=headers).status_code==403
    assert calls==[]
    result=owner.post(path,json={"consent":True,"demo":True},headers=headers)
    assert result.status_code==200
    assert result.json()["synthetic_demo"] is True
    assert len(calls)==1 and "PRIVATE_TEST_SENTINEL" not in str(calls)
