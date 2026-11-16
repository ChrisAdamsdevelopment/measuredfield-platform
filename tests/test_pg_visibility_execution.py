"""MF-036 real PostgreSQL consent, FORCE RLS, budget, idempotency, no-network tests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.schemas.ai_visibility_run import (
    ExperimentSpec, LocationSupport, Market, ProviderFamily, ProviderProfile, Question,
)
from app.services.ai_visibility_adapter import build_ai_visibility_plan
from app.services.visibility_execution import (
    ResearchBudgetExceeded, ResearchConflict, ResearchNotAuthorized, _micros,
    authorize_budget, cancel_reserved_search, list_reservations,
    reserve_planned_search,
)
from .conftest import csrf_of, onboard_business


def _spec():
    return ExperimentSpec(
        experiment_id="mf036-owner-review",
        brand_name="Fictional Valley Remodelers",
        brand_domain="fictional-valley.example",
        markets=(Market(id="prescott", city="Prescott", region="Arizona",
                        country_code="US", timezone="America/Phoenix"),),
        questions=(
            Question(id="q1", wording="Who can remodel a kitchen around Prescott?"),
            Question(id="q2", wording="Which company can renovate a bathroom near Prescott?"),
            Question(id="q3", wording="Who specializes in residential remodeling near Prescott?"),
        ),
        providers=(ProviderProfile(
            id="p1", family=ProviderFamily.OPENAI_WEB_SEARCH,
            model="synthetic-openai-model",
            location_support=LocationSupport.APPROXIMATE_MARKET,
            max_charge_per_run_usd=Decimal("0.2500"),
            max_web_search_uses=2,
        ),),
        repetitions=1, max_planned_runs=10,
        max_estimated_total_usd=Decimal("1.00"),
    )


def _paid_owner(org_id):
    """Synthetic verified paid state only inside disposable PostgreSQL."""
    from app.db import session_scope
    from app.models import Organization, Membership, ServiceOrder, now
    with session_scope(org_id=org_id) as db:
        org = db.get(Organization, org_id)
        order = db.scalar(select(ServiceOrder).where(ServiceOrder.org_id == org_id))
        assert order is not None and order.status == "provisioned"
        org.plan = "growth"
        order.offer_code = "growth"
        order.amount_cents = 10000  # Fictional test charge; never a real payment.
        order.paid_at = now()
        order.payment_provider = "stripe"
        order.payment_evidence = "stripe:synthetic-test-only"
        org.settings = {**(org.settings or {}), "billing": {
            "order_id": str(order.id), "paid": True, "amount_cents": 10000,
        }}
        membership = db.scalar(select(Membership).where(Membership.org_id == org_id))
        assert membership is not None and membership.role == "owner"
        return membership.user_id


def _client_and_owner(platform, suffix):
    client, org_id, _ = onboard_business(
        platform, business="Research Budget " + suffix,
        owner_email=f"research-{suffix}@fictional.example")
    user_id = _paid_owner(org_id)
    return client, org_id, user_id


def test_exact_micro_dollars_never_binary_float():
    assert _micros("0.0001") == 100
    assert _micros(Decimal("0.2500")) == 250000
    assert _micros("0", allow_zero=True) == 0
    for bad in (float(0.1), "NaN", "Infinity", "0.0000001", "-0.01", "501", None, True):
        with pytest.raises(ResearchConflict):
            _micros(bad)


def test_budget_and_reservation_routes_require_active_paid_owner_and_csrf(platform):
    from app.db import session_scope
    from app.models import Organization
    owner, org_id, _ = onboard_business(
        platform, business="Trial Research Owner",
        owner_email="budget-consent@example.invalid")
    budget_url = "/api/v1/modules/visibility/execution/budget"
    consent = {"monthly_ceiling_usd": "0.50",
               "confirm": "AUTHORIZE RESEARCH RESERVATIONS"}
    assert owner.get(budget_url).status_code == 422  # Existing consult != paid subscription.
    assert owner.put(budget_url, json=consent).status_code == 403  # CSRF first
    assert owner.put(budget_url, json=consent,
                     headers={"X-CSRF-Token": csrf_of(owner)}).status_code == 422
    _paid_owner(org_id)
    assert owner.put(budget_url, json={**consent, "confirm": "maybe"},
                     headers={"X-CSRF-Token": csrf_of(owner)}).status_code == 422
    saved = owner.put(budget_url, json=consent,
                      headers={"X-CSRF-Token": csrf_of(owner)})
    assert saved.status_code == 200, saved.text[:400]
    data = saved.json()
    assert data["created"] and data["no_provider_calls_made"]
    assert data["budget"]["monthly_ceiling_micros"] == 500000
    assert data["budget"]["available_micros"] == 500000
    assert data["budget"]["live_dispatch_enabled"] is False
    assert owner.get(budget_url).json()["status"] == "owner_authorized_reservations_only"
    with session_scope(org_id=org_id) as db:
        db.get(Organization, org_id).status = "deletion_pending"
    assert owner.get(budget_url).status_code == 422


def test_single_owner_plan_reservation_idempotency_and_cancel(platform):
    from app.db import session_scope
    from app.models import VisibilityResearchBudget, VisibilityResearchReservation
    client, org, user = _client_and_owner(platform, "reserve")
    spec = _spec()
    plan = build_ai_visibility_plan(spec)
    budget_url = "/api/v1/modules/visibility/execution/budget"
    endpoint = "/api/v1/modules/visibility/execution/reservations"
    csrf = {"X-CSRF-Token": csrf_of(client)}
    body = {"experiment": spec.model_dump(mode="json"),
            "planned_run_id": plan.requests[0].id,
            "confirm": "RESERVE THIS SEARCH"}
    assert client.post(endpoint, json=body).status_code == 403
    assert client.post(endpoint, json={**body, "confirm": "yes"},
                       headers=csrf).status_code == 422
    assert client.post(endpoint, json=body, headers=csrf).status_code == 422
    assert client.put(budget_url, json={"monthly_ceiling_usd": "0.40",
                       "confirm": "AUTHORIZE RESEARCH RESERVATIONS"},
                      headers=csrf).status_code == 200
    first = client.post(endpoint, json=body, headers=csrf)
    assert first.status_code == 200, first.text[:400]
    value = first.json()
    assert value["created"] is True
    assert value["reservation"]["status"] == "reserved"
    assert value["reservation"]["ceiling_micros"] == 250000
    assert value["reservation"]["verified_provider_results"] == 0
    again = client.post(endpoint, json=body, headers=csrf)
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["reservation"]["id"] == value["reservation"]["id"]
    second = client.post(endpoint, json={**body, "planned_run_id": plan.requests[1].id},
                         headers=csrf)
    assert second.status_code == 422, second.text[:300]
    assert "ceiling" in second.text
    assert client.get(budget_url).json()["held_micros"] == 250000
    with session_scope(org_id=org) as db:
        assert db.query(VisibilityResearchReservation).count() == 1
        assert db.query(VisibilityResearchBudget).count() == 1
    cancel_url = endpoint + "/" + value["reservation"]["id"] + "/cancel"
    cancellation = {"confirm": "CANCEL UNSENT SEARCH"}
    assert client.post(cancel_url, json=cancellation).status_code == 403
    changed = client.post(cancel_url, json=cancellation, headers=csrf)
    assert changed.status_code == 200 and changed.json()["cancelled_now"] is True
    assert client.post(cancel_url, json=cancellation,
                       headers=csrf).json()["cancelled_now"] is False
    assert client.get(budget_url).json()["held_micros"] == 0
    assert client.post(endpoint, json=body, headers=csrf).json()["created"] is False
    other = client.post(endpoint, json={**body, "planned_run_id": plan.requests[1].id},
                        headers=csrf)
    assert other.status_code == 200
    assert client.get(budget_url).json()["held_micros"] == 250000
    assert len(client.get(endpoint).json()["reservations"]) == 2


def test_owner_cannot_reduce_budget_below_prior_holds(platform):
    from app.db import session_scope
    client, org, user = _client_and_owner(platform, "decrease")
    spec = _spec()
    with session_scope(org_id=org) as db:
        authorize_budget(db, org, user, monthly_ceiling_usd="0.30",
                         owner_confirmed=True)
        row, created = reserve_planned_search(
            db, org, user, spec=spec, planned_run_id=build_ai_visibility_plan(spec).requests[0].id,
            owner_confirmed=True)
        assert created and row.ceiling_micros == 250000
    with pytest.raises(ResearchBudgetExceeded):
        with session_scope(org_id=org) as db:
            authorize_budget(db, org, user, monthly_ceiling_usd="0.10",
                             owner_confirmed=True)
    with session_scope(org_id=org) as db:
        budget, changed = authorize_budget(db, org, user,
                                           monthly_ceiling_usd="0.30",
                                           owner_confirmed=True)
        assert changed is False and budget.held_micros == 250000


def test_concurrent_requests_cannot_double_book_budget(platform):
    from app.db import session_scope
    client, org, user = _client_and_owner(platform, "concurrent")
    spec = _spec()
    runs = build_ai_visibility_plan(spec).requests[:2]
    with session_scope(org_id=org) as db:
        authorize_budget(db, org, user, monthly_ceiling_usd="0.30", owner_confirmed=True)

    def attempt(run_id):
        try:
            with session_scope(org_id=org) as db:
                row, created = reserve_planned_search(
                    db, org, user, spec=spec, planned_run_id=run_id, owner_confirmed=True)
                return "reserved" if created else "repeat"
        except ResearchBudgetExceeded:
            return "out_of_budget"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [r.id for r in runs]))
    assert sorted(results) == ["out_of_budget", "reserved"], results
    with session_scope(org_id=org) as db:
        from app.models import VisibilityResearchBudget, VisibilityResearchReservation
        budget = db.query(VisibilityResearchBudget).one()
        assert budget.held_micros == 250000
        assert db.query(VisibilityResearchReservation).count() == 1


def test_tenant_rls_and_cross_org_access_refused(platform):
    from app.db import get_engine, isolation_self_check, session_scope
    from app.models import VisibilityResearchBudget, VisibilityResearchReservation
    a, org_a, user_a = _client_and_owner(platform, "tenant-a")
    b, org_b, user_b = _client_and_owner(platform, "tenant-b")
    spec = _spec()
    with session_scope(org_id=org_a) as db:
        budget, _ = authorize_budget(db, org_a, user_a,
                                      monthly_ceiling_usd="0.35", owner_confirmed=True)
        row, _ = reserve_planned_search(db, org_a, user_a, spec=spec,
            planned_run_id=build_ai_visibility_plan(spec).requests[0].id,
            owner_confirmed=True)
        budget_id, row_id = budget.id, row.id
    assert isolation_self_check() == []
    with get_engine().connect() as conn:
        names = ["visibility_research_budgets", "visibility_research_reservations"]
        flags = conn.execute(text(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = ANY(:names)"), {"names": names}).all()
        assert len(flags) == 2 and all(r[1] and r[2] for r in flags)
        assert conn.execute(text("SELECT count(*) FROM visibility_research_reservations")).scalar() == 0
    with session_scope(org_id=org_b) as db:
        assert db.get(VisibilityResearchBudget, budget_id) is None
        assert db.get(VisibilityResearchReservation, row_id) is None
        assert list_reservations(db, org_b, user_b) == []
        with pytest.raises(ResearchConflict):
            cancel_reserved_search(db, org_b, user_b, row_id)
    with session_scope() as db:
        assert db.query(VisibilityResearchReservation).count() == 0
    with session_scope(org_id=org_b) as db:
        with pytest.raises(ResearchNotAuthorized):
            authorize_budget(db, org_a, user_a, monthly_ceiling_usd="1",
                             owner_confirmed=True)


def test_dispatched_unknown_cannot_be_cancelled_without_reconciliation(platform):
    from app.db import session_scope
    from app.models import VisibilityResearchReservation, VisibilityResearchBudget
    client, org, user = _client_and_owner(platform, "unknown")
    spec = _spec()
    with session_scope(org_id=org) as db:
        authorize_budget(db, org, user, monthly_ceiling_usd="0.30", owner_confirmed=True)
        row, _ = reserve_planned_search(db, org, user, spec=spec,
            planned_run_id=build_ai_visibility_plan(spec).requests[0].id, owner_confirmed=True)
        row_id = row.id
        # Simulate a future trusted worker's persisted dispatch state. No network here.
        row.status = "dispatch_unknown"
    with session_scope(org_id=org) as db:
        with pytest.raises(ResearchConflict, match="unknown-charge"):
            cancel_reserved_search(db, org, user, row_id)
    with session_scope(org_id=org) as db:
        row = db.get(VisibilityResearchReservation, row_id)
        budget = db.query(VisibilityResearchBudget).one()
        assert row.status == "dispatch_unknown" and budget.held_micros == 250000


def test_no_live_transport_or_trusted_receipt_in_reservation_layer():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "app" / "services" / "visibility_execution.py").read_text(encoding="utf-8")
    route = (Path(__file__).resolve().parents[1] /
             "app" / "routers" / "visibility_execution.py").read_text(encoding="utf-8")
    for body in (source, route):
        for forbidden in ("requests.post(", "httpx.post(", "OpenAI(", "TrustedReceipt(",
                          "OPENAI_API_KEY", "urllib.request.urlopen("):
            assert forbidden not in body
