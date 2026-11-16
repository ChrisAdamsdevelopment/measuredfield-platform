"""MF-018: deterministic UI template, CSS and JS contract tests for AI Visibility Lab."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.module_catalog import catalog, connection_policy
from app.services.visibility_lab import BENCHMARK_22, MARKETS, MODES


REPO = Path(__file__).resolve().parents[1]


def _render_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(REPO / "app" / "templates")),
        autoescape=select_autoescape(["html"]),
    )


def _base_context(path: str = "/app/modules/visibility") -> dict:
    user = SimpleNamespace(name="Demo Owner", email="demo@example.invalid", is_platform_admin=False)
    principal = SimpleNamespace(user=user)
    return dict(
        org=SimpleNamespace(name="Example Remodeling LLC", status="active"),
        principal=principal,
        settings=SimpleNamespace(is_production=False),
        role="owner",
        orgs=[],
        brand="MeasuredField",
        path=path,
        flash="",
        flash_error="",
        csrf="synthetic-csrf-token",
        request=object(),
    )


def _benchmark_items() -> list[dict]:
    return [{"id": f"q-seed-{i}", "text": q} for i, q in enumerate(BENCHMARK_22, 1)]


def test_modules_template_renders_full_capabilities_and_security_contract():
    env = _render_env()
    html = env.get_template("app/modules.html").render(
        **_base_context("/app/modules"),
        modules=catalog(),
        connection=connection_policy(),
    )
    assert "AI Visibility Lab" in html
    assert "Computer Connect" in html
    assert "Evidence provenance" in html
    assert "Tracked opportunities" in html
    assert 'Remote access enabled: <strong>No</strong>' in html
    assert 'Default permissions: <strong>Deny</strong>' in html
    assert "Zero fake results" in html


def test_visibility_lab_template_has_multi_market_checkboxes_and_all_three_methods():
    env = _render_env()
    html = env.get_template("app/visibility_lab.html").render(
        **_base_context("/app/modules/visibility"),
        questions=_benchmark_items(),
        markets=list(MARKETS),
        modes=list(MODES),
    )
    # All 22 benchmark questions rendered as checkboxes
    assert html.count('class="ml-benchmark"') == 22
    for q_text in BENCHMARK_22:
        assert q_text in html

    # Multiple market checkboxes instead of a single-select dropdown
    assert '<select id="ml-market">' not in html
    assert html.count('class="ml-market"') == len(MARKETS)
    for market in MARKETS:
        assert f'value="{market}"' in html

    # All three search methods present
    assert html.count('class="ml-mode"') == 3
    for mode in MODES:
        assert f'value="{mode}"' in html

    # Required explicit selection & workflow controls
    assert 'id="ml-all"' in html
    assert 'id="ml-approved-only"' in html
    assert 'id="ml-none"' in html
    assert 'id="ml-gsc-results"' in html
    assert 'id="ml-plan"' in html
    assert 'id="ml-export"' in html
    assert 'id="ml-import-file"' in html
    assert 'id="ml-export-draft"' in html
    assert "0 provider calls executed" in html
    assert "No automatic localStorage" in html


def test_visibility_lab_js_enforces_explicit_approval_and_no_localstorage():
    js_path = REPO / "app" / "static" / "module_lab.js"
    js = js_path.read_text(encoding="utf-8")

    # Never use automatic browser localStorage or sessionStorage for sensitive owner data
    assert "localStorage.setItem" not in js
    assert "sessionStorage.setItem" not in js

    # Explicit approval & multi-market support
    assert "approved: false" in js
    assert "ml-approved-only" in js
    assert 'input[name="ml-market"]' in js
    assert 'input[name="ml-mode"]' in js
    assert "source_queries" in js
    assert "restoreDraftFromJson" in js
    # Untrusted JSON imports may restore controls but not approval/evidence claims.
    assert "data.type !== \"measuredfield.local_preview_plan\"" in js
    assert "suggestions = [];" in js
    assert "latestPlan = null;" in js
    assert "reapprove GSC questions" in js


def test_visibility_lab_css_has_mobile_390px_and_desktop_responsive_rules():
    css_path = REPO / "app" / "static" / "module_lab.css"
    css = css_path.read_text(encoding="utf-8")
    assert "@media(max-width:900px)" in css
    assert "@media(max-width:570px)" in css
    assert ".ml-lineage-chip" in css
    assert ".ml-evidence-grid" in css
    assert ".ml-formula-bar" in css


def test_json_draft_revalidation_executes_in_real_node_runtime():
    """Untrusted imported JSON must not reinstate approval, counts, or fake results."""
    import subprocess

    script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const nodes = new Map();
function makeNode() {
  return {
    value: "", checked: false, hidden: false, disabled: false,
    textContent: "", className: "", dataset: {}, children: [],
    classList: { toggle() {} },
    append(...args) { this.children.push(...args); },
    replaceChildren() { this.children = []; },
    addEventListener() {},
  };
}
function byId(id) {
  if (!nodes.has(id)) nodes.set(id, makeNode());
  return nodes.get(id);
}
const benchmarks = [makeNode(), makeNode()];
benchmarks[0].value = "q-seed-1";
benchmarks[0].dataset.text = "First sample benchmark";
benchmarks[0].checked = true;
benchmarks[1].value = "q-seed-2";
benchmarks[1].dataset.text = "Second sample benchmark";
benchmarks[1].checked = true;
const market = makeNode();
market.value = "Prescott Valley, Arizona"; market.checked = true;
const mode = makeNode();
mode.value = "as_written"; mode.checked = true;
globalThis.document = {
  getElementById: byId,
  querySelectorAll(selector) {
    if (selector === ".ml-benchmark") return benchmarks;
    if (selector === 'input[name="ml-market"]') return [market];
    if (selector === 'input[name="ml-mode"]') return [mode];
    return [];
  },
  createElement() { return makeNode(); },
};
const patched = source.replace(/\}\)\(\);\s*$/, "globalThis.__restoreDraft = restoreDraftFromJson;})();");
assert.notEqual(patched, source, "Must expose actual module function for this isolated test");
eval(patched);
const imported = {
  type: "measuredfield.local_preview_plan",
  provider_calls_executed: 0,
  csv_text: "Query,Clicks,Impressions,Position\nprescott contractor,0,3,9.2",
  gsc_snapshot: { row_count: 90000, impressions: 9999999 },
  suggestions: [{ id: "injected", question: "Forged?", approved: true }],
  selected_benchmark_ids: ["q-seed-1"],
  plan: { request_count: 999999, provider_requests_executed: 9000 },
  config: {
    markets: ["Prescott Valley, Arizona"], modes: ["as_written"],
    repetitions: 1, chunk_size: 5, max_total: 250,
  },
};
__restoreDraft(JSON.stringify(imported));
assert.match(byId("ml-csv").value, /prescott contractor/);
assert.equal(byId("ml-export").disabled, true);
assert.equal(byId("ml-gsc-results").hidden, true);
assert.equal(byId("ml-plan-results").hidden, true);
assert.equal(benchmarks[0].checked, true);
assert.equal(benchmarks[1].checked, false);
assert.match(byId("ml-selected").textContent, /0 GSC approved/);
assert.match(byId("ml-plan-message").textContent, /reapprove GSC/);
assert.throws(
  () => __restoreDraft(JSON.stringify({ ...imported, type: "forged.provider_receipt" })),
  /not a valid zero-provider-call/
);
console.log("DRAFT_IMPORT_REVALIDATION_PASS");
"""
    result = subprocess.run(
        ["node", "-e", script, str(REPO / "app" / "static" / "module_lab.js")],
        text=True, capture_output=True, check=True, timeout=15,
    )
    assert "DRAFT_IMPORT_REVALIDATION_PASS" in result.stdout
