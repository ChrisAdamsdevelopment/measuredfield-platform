"""Python fallback must never invent traffic, rankings, revenue, or approvals."""
from app.services import growth
from app.services.native_growth import review_from_counts


def test_no_observations_produce_unknowns_and_only_proposed_actions():
    result = review_from_counts("Synthetic Cleaning", {}, days=30)
    assert result["engine"] == "measuredfield_native_fallback_v1"
    assert result["evidenceStatus"]["site"] == "unknown"
    assert result["unknowns"]
    assert all(a["approvalRequired"] and a["status"] == "proposed"
               for a in result["analysis"]["actions"])
    assert "baseline" in result["analysis"]["actions"][0]["claim"].lower()


def test_observed_dropoff_generates_specific_testable_hypothesis():
    report = review_from_counts("Demo Repairs", {"page_view": 120, "form_start": 26, "lead": 2})
    claims = [x["claim"].lower() for x in report["analysis"]["actions"]]
    assert any("form starts" in x for x in claims)
    assert report["evidenceStatus"]["site"] == "measured_first_party"
    assert all(a.get("metric") and a.get("experiment") for a in report["analysis"]["actions"])


def test_inconsistent_counters_are_flagged_not_silently_normalized():
    report = review_from_counts("Demo", {"page_view": 2, "form_start": 12, "lead": 15})
    assert any("not internally consistent" in warning for warning in report["unknowns"])


def test_node_unavailable_falls_back_without_crashing(monkeypatch):
    def unavailable(*args, **kwargs):
        raise growth.GrowthBridgeError("unavailable")
    monkeypatch.setattr(growth, "assess_business", unavailable)
    report = growth.review_for_org("Synthetic Construction", {
        "page_view": 35, "form_start": 1, "lead": 0,
    })
    assert report is not None
    assert report["engine"] == "measuredfield_native_fallback_v1"
    assert report["evidenceStatus"]["searchConsole"] == "unavailable"


def test_node_available_keeps_original_evaluator_path(monkeypatch):
    def real_response(_):
        return {"evidenceStatus": {"site": "unknown"}, "analysis": {"actions": []}, "unknowns": []}
    monkeypatch.setattr(growth, "assess_business", real_response)
    report = growth.review_for_org("Demo", {"page_view": 7})
    assert report["evidenceStatus"]["site"] == "measured_first_party"
    assert report.get("engine") != "measuredfield_native_fallback_v1"
