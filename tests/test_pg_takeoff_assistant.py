"""Actual authenticated route, PostgreSQL reservations, offline provider fixture."""
from app.services import takeoff_assistant as a
from .conftest import client_for, csrf_of, onboard_business

def test_assistant_authorization_consent_and_durable_limits(platform,monkeypatch):
    owner,org,_=onboard_business(platform,business="Synthetic Remodel",owner_email="takeoff-assistant@fictional.example")
    other,_,_=onboard_business(platform,business="Unrelated Synthetic",owner_email="takeoff-other@fictional.example")
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ENABLED","true")
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ORG_IDS",str(org))
    monkeypatch.setenv("MF_TAKEOFF_ASSISTANT_ENABLED","true")
    monkeypatch.setenv("NVIDIA_API_KEY","synthetic-offline-fixture")
    calls=[]
    monkeypatch.setattr(a,"infer",lambda body:calls.append(body) or {"actual_inference":True,"reply":"Synthetic fixture","work_items":[]})
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
