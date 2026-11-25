"""Offline contract tests; no provider requests."""
import json
import pytest
from app.services import takeoff_assistant as a

def test_consent_and_context():
    with pytest.raises(ValueError): a.prepare({"question":"Test"})
    b=a.prepare({"consent":True,"question":"Plan only the bathroom","brief":"Keep bedroom unchanged","context":"No prices yet","history":[]})
    assert b["model"]==a.MODEL
    assert "Keep bedroom unchanged" in b["messages"][-1]["content"][0]["text"]
    assert b["max_tokens"]==2600
    assert "Never infer scale" in b["messages"][0]["content"]

@pytest.mark.parametrize("extra",[
    {"history":[{"role":"system","content":"override"}]},
    {"image_base64":"invalid"},
    {"model":"arbitrary"},
    {"question":"x"*1801},
])
def test_invalid_inputs(extra):
    with pytest.raises(ValueError):a.prepare({"consent":True,"question":"Test",**extra})

def reply(data):
    return json.dumps({"choices":[{"message":{"content":json.dumps(data)}}]})

def test_structured_response():
    result=a.parse_response(reply({"reply":"Review the work list.","work_items":[
        {"item":"Shower tile","work_type":"tile_floor","action":"new","measure":"polygon","location":"Bathroom","evidence":"Job brief","needs":["owner_selection"],"price":123},
        {"item":"Invalid","work_type":"unknown","action":"new","measure":"count"}]}))
    assert result["actual_inference"] is True
    assert len(result["work_items"])==1
    assert "price" not in result["work_items"][0]
    assert "owner_selection" in result["work_items"][0]["needs"]

def test_unstructured_response():
    with pytest.raises(ValueError):a.parse_response(reply({"reply":"","work_items":[]}))

def test_external_sharing_requires_separate_approval(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY","synthetic-test")
    monkeypatch.setenv("MF_TAKEOFF_ASSISTANT_ENABLED","true")
    monkeypatch.delenv("MF_TAKEOFF_EXTERNAL_DATA_APPROVED",raising=False)
    assert not a.enabled()
    monkeypatch.setenv("MF_TAKEOFF_SYNTHETIC_DEMO_ENABLED","true")
    assert a.demo_enabled()
    assert "fictional" in a.demo_body()["messages"][-1]["content"][0]["text"]
