"""MF-020 saved-workspace UI and authenticated integration regression tests."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services.visibility_lab import BENCHMARK_22, MARKETS, MODES

ROOT = Path(__file__).resolve().parents[1]


def test_private_template_has_saved_workspace_and_role_signal():
    env = Environment(
        loader=FileSystemLoader(str(ROOT / "app" / "templates")),
        autoescape=select_autoescape(["html"]),
    )
    for role, expected in (("owner", "true"), ("readonly", "false")):
        html = env.get_template("app/visibility_lab.html").render(
            org=SimpleNamespace(name="Synthetic Shop", status="active"),
            principal=SimpleNamespace(user=SimpleNamespace(
                name="Demo Owner", email="owner@example.invalid", is_platform_admin=False)),
            settings=SimpleNamespace(is_production=False),
            role=role, orgs=[], path="/app/modules/visibility",
            brand="MeasuredField", flash="", flash_error="", csrf="test-csrf",
            request=object(),
            questions=[{"id": f"q-seed-{i}", "text": q}
                       for i, q in enumerate(BENCHMARK_22, 1)],
            markets=MARKETS, modes=MODES,
        )
        assert f'data-write-enabled="{expected}"' in html
        assert 'data-storage-api="/api/v1/modules/visibility/storage"' in html
        for identifier in ("mf-save-gsc", "mf-save-questions", "mf-save-plan",
                           "mf-refresh-saved", "mf-source-start", "mf-source-end",
                           "mf-save-stats", "mf-saved-list"):
            assert f'id="{identifier}"' in html
        assert 'src="/static/module_persist.js"' in html
        assert "Saved does not mean published, searched" in html


def test_saved_bridge_node_runtime_does_not_trust_synthetic_or_bypass_csrf():
    script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const nodes = new Map();
function element() {
  const listeners = new Map();
  return {
    value: "", disabled: false, hidden: false, textContent: "", children: [],
    className: "", dataset: {}, files: [], _listeners: listeners,
    append(...items) { this.children.push(...items); },
    replaceChildren() { this.children = []; },
    addEventListener(type, fn) {
      const handlers = listeners.get(type) || [];
      handlers.push(fn); listeners.set(type, handlers);
    },
    dispatchEvent(event) {
      for (const fn of (listeners.get(event.type) || [])) fn(event);
    },
    classList: { toggle() {} },
  };
}
function byId(id) {
  if (!nodes.has(id)) nodes.set(id, element());
  return nodes.get(id);
}
globalThis.CustomEvent = class {
  constructor(type, options) { this.type = type; this.detail = options.detail; }
};
globalThis.document = {
  getElementById: byId, createElement: element,
};
const app = byId("mf-lab");
app.dataset = {
  storageApi: "/api/v1/modules/visibility/storage",
  writeEnabled: process.argv[2] === "readonly" ? "false" : "true",
  csrf: "synthetic-123",
};
byId("mf-plan-title").value = "Owner's test baseline";
const lab = {
  csv_text: "Query,Clicks,Impressions,Position\nprescott kitchen contractor,0,8,4.1",
  has_analysis: true,
  synthetic_sample: true,
  selected: [
    { id: "q-seed-1", text: "Benchmark", source: "benchmark" },
    { id: "q-local-1", text: "Who remodels kitchens?", source: "gsc_approved" },
  ],
  approved: [{ text: "Who remodels kitchens?" }],
  has_plan: true,
  config: {
    markets: ["Prescott, Arizona"], modes: ["as_written", "persona_context"],
    repetitions: 1, chunk_size: 5, max_total: 20,
  },
};
app.addEventListener("mf:request-visibility-state", (event) => event.detail.resolve(lab));
let rows = [];
let questions = [{
  id: "old-approved", text: "Who remodels kitchens?",
  origin: "gsc_suggestion", status: "approved",
  snapshot_id: "snap-old", market: "Prescott, Arizona",
}];
let plans = [];
const posts = [];
globalThis.fetch = async (url, options = {}) => {
  assert(url.startsWith("/api/v1/modules/visibility/storage/"));
  const suffix = url.replace("/api/v1/modules/visibility/storage", "");
  if (options.method === "POST") {
    assert.equal(options.headers["X-CSRF-Token"], "synthetic-123");
    const data = JSON.parse(options.body);
    posts.push({ suffix, data });
    if (suffix === "/snapshots") {
      rows = [{
        id: "snap-1", filename: data.filename, row_count: 1,
        date_start: data.date_start, date_end: data.date_end,
        impressions: 8, clicks: 0,
      }];
      return ok({ snapshot: rows[0], created: true });
    }
    if (suffix === "/snapshots/snap-1/suggestions") {
      questions.push({
        id: "question-uuid", text: "Who remodels kitchens?",
        origin: "gsc_suggestion", status: "draft",
        snapshot_id: "snap-1", market: "Prescott, Arizona",
      });
      return ok({ created: 1, already_existed: 0 });
    }
    if (suffix === "/questions/question-uuid/review") {
      const current = questions.find((question) => question.id === "question-uuid");
      current.status = data.status;
      return ok({ question: current });
    }
    if (suffix === "/experiments") {
      assert.deepEqual(data.questions.map((x) => x.id),
        ["q-seed-1", "question-uuid"]);
      plans = [{
        id: "plan-1", title: data.title, status: "planned",
        request_count: 4, provider_requests_executed: 0,
      }];
      return ok({ experiment: plans[0] });
    }
    throw Error("Unexpected POST " + suffix);
  }
  if (suffix === "/snapshots") return ok({ snapshots: rows });
  if (suffix === "/questions") return ok({ questions });
  if (suffix === "/experiments") return ok({ experiments: plans });
  throw Error("Unexpected GET " + suffix);
};
function ok(payload) {
  return { ok: true, status: 200, json: async () => payload };
}
async function tick() {
  await new Promise((resolve) => setTimeout(resolve, 15));
}
async function click(id) {
  const item = byId(id);
  const listener = (item._listeners.get("click") || [])[0];
  assert(listener, "missing click listener for " + id);
  await listener();
  await tick();
}
(async () => {
  eval(source);
  await tick();
  if (app.dataset.writeEnabled === "false") {
    assert.equal(byId("mf-save-gsc").disabled, true);
    await click("mf-save-gsc");
    assert.equal(posts.length, 0);
    console.log("READONLY_BRIDGE_PASS");
    return;
  }
  // Sample never becomes owner-claimed source.
  await click("mf-save-gsc");
  assert.equal(posts.length, 0);
  assert.match(byId("mf-save-status").textContent, /genuine owner-supplied/);
  lab.synthetic_sample = false;
  app.dispatchEvent({ type: "mf:visibility-changed" });
  assert.equal(byId("mf-save-gsc").disabled, false);
  await click("mf-save-gsc");
  assert.equal(posts[0].suffix, "/snapshots");
  assert.equal(posts[0].data.date_start, null);
  assert.equal(posts[0].data.date_end, null);
  assert.equal(posts[1].suffix, "/snapshots/snap-1/suggestions");
  assert.equal(questions[0].status, "approved");
  assert.equal(questions[1].status, "draft");
  await click("mf-save-questions");
  assert.equal(questions[0].status, "approved");
  assert.equal(questions[0].snapshot_id, "snap-old");
  assert.equal(questions[1].status, "approved");
  assert(posts.some((post) => post.suffix === "/questions/question-uuid/review"));
  await click("mf-save-plan");
  assert.equal(plans[0].provider_requests_executed, 0);
  assert.equal(plans[0].request_count, 4);
  assert.match(byId("mf-save-status").textContent, /0 provider calls executed/);
  const planCount = posts.filter((p) => p.suffix === "/experiments").length;
  // A truncated question list must fail closed, never attach an older approved snapshot.
  questions = questions.filter((q) => q.snapshot_id === "snap-old");
  await click("mf-refresh-saved");
  await click("mf-save-plan");
  assert.equal(posts.filter((p) => p.suffix === "/experiments").length, planCount);
  assert.match(byId("mf-save-status").textContent, /Save and explicitly approve GSC question/);
  // Nor can an unsaved or synthetic CSV piggyback on the older approved question text.
  lab.csv_text = "Different unsaved CSV";
  lab.synthetic_sample = true;
  app.dispatchEvent({ type: "mf:visibility-changed" });
  assert.equal(byId("mf-save-plan").disabled, true);
  await click("mf-save-plan");
  assert.equal(posts.filter((p) => p.suffix === "/experiments").length, planCount);
  assert.match(byId("mf-save-status").textContent, /current genuine GSC source/);
  console.log("SAVED_WORKFLOW_BRIDGE_PASS");
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    for role, success in (("owner", "SAVED_WORKFLOW_BRIDGE_PASS"),
                          ("readonly", "READONLY_BRIDGE_PASS")):
        result = subprocess.run(
            ["node", "-e", script,
             str(ROOT / "app" / "static" / "module_persist.js"), role],
            capture_output=True, text=True, timeout=25,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert success in result.stdout


def test_real_saved_api_owner_can_write_readonly_cannot(platform):
    """Real PostgreSQL and session role fixture, not browser mocks."""
    from .conftest import csrf_of, onboard_business
    from .test_pg_isolation import add_member

    owner, _org_id, _ = onboard_business(
        platform, business="Saved Lab Test Company",
        owner_email="visibility-bridge-owner@example.invalid",
    )
    viewer = add_member(
        platform, owner, "visibility-bridge-reader@example.invalid", "readonly",
    )
    assert 'data-write-enabled="true"' in owner.get("/app/modules/visibility").text
    assert 'data-write-enabled="false"' in viewer.get("/app/modules/visibility").text
    url = "/api/v1/modules/visibility/storage/snapshots"
    csv_text = "Query,Clicks,Impressions,Position\nprescott general contractor,0,7,5.8"
    body = {"filename": "real-owner.csv", "csv_text": csv_text}
    assert viewer.post(
        url, json=body, headers={"X-CSRF-Token": csrf_of(viewer)}
    ).status_code == 403
    saved = owner.post(
        url, json=body, headers={"X-CSRF-Token": csrf_of(owner)}
    )
    assert saved.status_code == 200, saved.text[:250]
    snap = saved.json()["snapshot"]
    assert snap["date_start"] is None and snap["date_end"] is None
    read = viewer.get(url)
    assert read.status_code == 200
    assert any(row["id"] == snap["id"] for row in read.json()["snapshots"])
    plan_url = "/api/v1/modules/visibility/storage/experiments"
    plan = {
        "title": "No provider calls", "questions": [
            {"id": "q-seed-1", "text": BENCHMARK_22[0]}],
        "markets": ["Prescott, Arizona"], "modes": ["as_written"],
        "repetitions": 1, "chunk_size": 5, "max_total": 20,
    }
    assert viewer.post(
        plan_url, json=plan, headers={"X-CSRF-Token": csrf_of(viewer)}
    ).status_code == 403
    accepted = owner.post(
        plan_url, json=plan, headers={"X-CSRF-Token": csrf_of(owner)}
    )
    assert accepted.status_code == 200, accepted.text[:250]
    assert accepted.json()["experiment"]["provider_requests_executed"] == 0
    assert len(viewer.get(plan_url).json()["experiments"]) >= 1
