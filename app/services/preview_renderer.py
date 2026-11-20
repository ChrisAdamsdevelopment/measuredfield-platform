"""Diagnostic observation card renderer and isolated concept preview renderer.

Guarantees:
1. All untrusted or observed strings are HTML-escaped via ``html.escape(..., quote=True)``.
2. ``DEMO_PREVIEW_JS`` contains zero network primitives (no ``fetch``, ``XMLHttpRequest``,
   ``sendBeacon``, ``/api/lead``, or ``/api/event``).
3. Form ``action`` attributes pointing to ``api/lead`` are neutralized to ``#contact`` with
   ``method="get"`` and ``data-mf-demo-form="true"``.
4. Zero inline ``style="..."`` attributes or ``<style>`` tags are emitted in HTML so the
   preview renders cleanly under ``style-src 'self'; script-src 'self'; connect-src 'none'``.
"""
from __future__ import annotations

import html
import re
from typing import Any

from ..schemas.preview import (
    DiagnosticObservationCard,
    ObservationStatus,
    PreviewConceptProfile,
)
from . import sitegen
from .widgets import WIDGET_CSS, default_widget_for_industry, render_widgets_html


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


PREVIEW_CSP = (
    "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
    "form-action 'none'; base-uri 'none'; frame-ancestors 'self'; object-src 'none'"
)

PREVIEW_CSS = """
.mf-preview-banner{background:#18343a;color:#f4f9f8;border-bottom:3px solid var(--accent);padding:14px 0;font-size:.92rem}
.mf-preview-banner strong{color:#fff;letter-spacing:.03em}
.mf-unconfirmed-bar{background:#fff8e6;color:#4a3500;border-bottom:1px solid #ead29c;padding:10px 0;font-size:.88rem}
.mf-diagnostic-section{background:#f7faf9;border-bottom:1px solid #dce9e7;padding:44px 0}
.mf-diagnostic-card{background:#fff;border:1px solid #c5dbd7;border-radius:14px;padding:26px}
.mf-diagnostic-card h2{margin:6px 0 12px;font-size:1.6rem}
.mf-diag-meta{font-size:.88rem;color:#536a6d;margin:0 0 18px}
.mf-diag-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin:18px 0}
.mf-diag-item{border:1px solid #dce9e7;border-radius:10px;padding:14px;background:#fbfdfc}
.mf-diag-item h3{margin:4px 0 6px;font-size:1.02rem}
.mf-diag-item p{margin:0;font-size:.9rem;color:#3b5359}
.mf-badge{display:inline-block;font-size:.76rem;font-weight:750;text-transform:uppercase;letter-spacing:.05em;padding:3px 9px;border-radius:99px}
.mf-badge-observed{background:#dff3ec;color:#16523d}
.mf-badge-not_observed{background:#fbe9dd;color:#7a340f}
.mf-badge-unable_to_determine{background:#e7ecef;color:#3d515c}
.mf-diag-alert{background:#eef4f6;border-left:4px solid #3f5560;padding:14px 16px;border-radius:8px;margin:14px 0}
.mf-diag-unknowns{margin:16px 0 0;padding-left:20px;font-size:.88rem;color:#4d6669}
.mf-diag-disclaimer{margin:14px 0 0;font-size:.83rem;color:#536a6d}
@media(max-width:720px){.mf-diag-grid{grid-template-columns:1fr}}
""".strip()

# Isolated client script for concept preview mode: strictly local DOM updates, zero network I/O.
DEMO_PREVIEW_JS = """(function(){
function updateExplorer(card){
  var selects=card.querySelectorAll('select[data-mf-param]');
  var list=card.querySelector('[data-mf-summary-list]');
  if(!list)return [];
  while(list.firstChild){list.removeChild(list.firstChild);}
  var lines=[];
  for(var i=0;i<selects.length;i++){
    var sel=selects[i];
    var label=sel.getAttribute('data-mf-param-label')||sel.getAttribute('data-mf-param')||'Parameter';
    var opt=sel.options[sel.selectedIndex];
    if(!opt)continue;
    var text=opt.textContent||'';
    var note=opt.getAttribute('data-mf-note')||'';
    var li=document.createElement('li');
    var b=document.createElement('strong');
    b.textContent=label+': ';
    li.appendChild(b);
    li.appendChild(document.createTextNode(text+(note?' \\u2014 '+note:'')));
    list.appendChild(li);
    lines.push(label+': '+text+(note?' ('+note+')':''));
  }
  return lines;
}
var cards=document.querySelectorAll('[data-mf-widget="scope-explorer"]');
for(var c=0;c<cards.length;c++){
  (function(card){
    var selects=card.querySelectorAll('select[data-mf-param]');
    for(var j=0;j<selects.length;j++){
      selects[j].addEventListener('change',function(){updateExplorer(card);});
    }
    var btn=card.querySelector('[data-mf-scope-apply]');
    var statusEl=card.querySelector('[data-mf-widget-status]');
    if(btn){
      btn.addEventListener('click',function(){
        var lines=updateExplorer(card);
        var msg=document.getElementById('message');
        if(msg&&lines.length){
          msg.value='Scope summary (concept preview):\\n- '+lines.join('\\n- ');
          msg.focus();
        }
        if(statusEl){
          statusEl.textContent='Scope summary copied into the sample inquiry form below (local preview only).';
        }
      });
    }
  })(cards[c]);
}
var form=document.getElementById('lead');
if(form){
  var status=document.getElementById('status');
  form.addEventListener('submit',function(ev){
    ev.preventDefault();
    if(status){
      status.textContent='Concept Preview Only: No inquiry was sent or stored. Claim and publish your website to receive real customer inquiries.';
    }
  });
}
})();
"""

_STATUS_LABELS: dict[ObservationStatus, str] = {
    "observed": "Observed in static HTML",
    "not_observed": "Not observed in static HTML",
    "unable_to_determine": "Unable to determine",
}


def _badge_html(status: ObservationStatus) -> str:
    label = _STATUS_LABELS.get(status, "Unable to determine")
    return f'<span class="mf-badge mf-badge-{_e(status)}">{_e(label)}</span>'


def render_diagnostic_card_html(card: DiagnosticObservationCard) -> str:
    """Render a ``DiagnosticObservationCard`` as an accessible, CSP-safe HTML section."""
    scanned_iso = card.scanned_at.strftime("%Y-%m-%d %H:%M UTC")
    meta_parts = [f"Inspected URL: {_e(card.input_url)}", f"Time: {_e(scanned_iso)}"]
    if card.final_url and card.final_url != card.input_url:
        meta_parts.append(f"Final URL: {_e(card.final_url)}")
    if card.http_status is not None:
        meta_parts.append(f"HTTP status: {_e(card.http_status)}")

    if card.scan_state != "completed":
        reason = card.failure_reason or "The website could not be inspected deterministically."
        items_html = (
            '<div class="mf-diag-alert" role="status">'
            f"<strong>Inspection status ({_e(card.scan_state)}):</strong> {_e(reason)} "
            "Because the page could not be verified, all feature checks below are marked "
            "<em>Unable to determine</em> and no missing features are assumed."
            "</div>"
        )
        checks = [
            ("Page title (<title>)", card.title_status, "Not evaluated due to incomplete scan."),
            ("Primary heading (<h1>)", card.h1_status, "Not evaluated due to incomplete scan."),
            ("Tap-to-call phone link (tel:)", card.tap_to_call_status, "Not evaluated due to incomplete scan."),
            ("Structured contact <form>", card.contact_form_status, "Not evaluated due to incomplete scan."),
            ("Mobile viewport meta tag", card.viewport_meta_status, "Not evaluated due to incomplete scan."),
        ]
        grid_cards = "".join(
            f'<div class="mf-diag-item">{_badge_html(st)}<h3>{_e(title)}</h3><p>{_e(detail)}</p></div>'
            for title, st, detail in checks
        )
        items_html += f'<div class="mf-diag-grid">{grid_cards}</div>'
    else:
        finding_cards: list[str] = []
        for f in card.findings:
            finding_cards.append(
                f'<div class="mf-diag-item">'
                f"{_badge_html(f.status)}"
                f"<h3>{_e(f.title)}</h3>"
                f"<p>{_e(f.observed_detail)}</p>"
                f"<p><strong>Evidence status:</strong> {_e(f.evidence_status)}</p>"
                f"</div>"
            )
        items_html = f'<div class="mf-diag-grid">{"".join(finding_cards)}</div>'

    unknown_lis = "".join(f"<li>{_e(u)}</li>" for u in card.unknowns)
    return (
        '<section id="diagnostic-evidence" class="mf-diagnostic-section" aria-labelledby="mf-diag-heading">'
        '<div class="wrap">'
        '<div class="mf-diagnostic-card">'
        '<div class="eyebrow">Public HTML Diagnostic Evidence</div>'
        '<h2 id="mf-diag-heading">What we observed on the public page</h2>'
        f'<p class="mf-diag-meta">{" &middot; ".join(meta_parts)}</p>'
        f"{items_html}"
        "<h3>What this static check cannot know yet</h3>"
        f'<ul class="mf-diag-unknowns">{unknown_lis}</ul>'
        f'<p class="mf-diag-disclaimer">{_e(card.disclaimer)}</p>'
        "</div></div></section>"
    )


def render_concept_preview(
    profile: PreviewConceptProfile | dict[str, Any],
    card: DiagnosticObservationCard | None = None,
    *,
    asset_base: str = "/preview-assets/",
) -> dict[str, Any]:
    """Render an isolated, interactive concept preview bundle without mutating ``sitegen.py``.

    Returns a dictionary containing:
    - ``html``: Complete HTML document with banner, optional diagnostic card, Scope Explorer
      widget, and neutralized demo contact form.
    - ``css``: Combined stylesheet (``sitegen`` palette CSS + preview/diagnostic/widget CSS).
    - ``js``: ``DEMO_PREVIEW_JS`` (local-only interaction script with zero network calls).
    - ``csp``: Recommended strict Content-Security-Policy header.
    - ``unconfirmed_fields``: List of business profile fields awaiting owner confirmation.
    """
    concept = (
        profile
        if isinstance(profile, PreviewConceptProfile)
        else PreviewConceptProfile.model_validate(profile)
    )

    if not asset_base.endswith("/"):
        asset_base = asset_base + "/"

    widgets = list(concept.widgets) if concept.widgets else [default_widget_for_industry(concept.industry)]
    display_city = concept.city if concept.city else "Service Area To Be Confirmed"

    render_input = {
        "name": concept.name,
        "city": display_city,
        "industry": concept.industry,
        "color": concept.color,
        "email": concept.email,
        "phone": concept.phone,
        "about": concept.about,
        "headline": concept.headline,
        "intro": concept.intro,
        "response_window": concept.response_window,
        "services": concept.services,
        "structured_intake": concept.structured_intake,
    }

    base_html, base_css = sitegen.render(render_input, asset_base=asset_base)

    banner_html = (
        '<aside class="mf-preview-banner" role="region" aria-label="Concept preview notice">'
        '<div class="wrap">'
        "<strong>NOT LIVE — CONCEPT PREVIEW ONLY.</strong> "
        "This interactive demonstration is not a published website, makes no network requests, "
        "and sends no emails or leads. An authorized business owner must confirm all details "
        "and approve a saved version before publication."
        "</div></aside>"
    )

    if concept.unconfirmed_fields:
        labels_str = ", ".join(_e(f.replace("_", " ")) for f in concept.unconfirmed_fields)
        banner_html += (
            '<div class="mf-unconfirmed-bar" role="status">'
            '<div class="wrap">'
            f"<strong>Owner confirmation needed before publish:</strong> {labels_str}."
            "</div></div>"
        )

    # Insert banner immediately after Skip link / at top of <body>
    base_html = base_html.replace(
        '<a class="skip" href="#top">Skip to content</a>',
        '<a class="skip" href="#top">Skip to content</a>' + banner_html,
        1,
    )

    # Add Scope Explorer link to main navigation
    base_html = base_html.replace(
        '<a href="#services">Services</a>',
        '<a href="#services">Services</a><a href="#scope-explorer">Scope Explorer</a>',
        1,
    )

    # Insert diagnostic observation card at top of <main> if provided
    if card is not None:
        diag_html = render_diagnostic_card_html(card)
        base_html = base_html.replace('<main id="top">', '<main id="top">' + diag_html, 1)

    # Insert interactive Scope & Readiness Explorer section before <section id="contact">
    widgets_html = render_widgets_html(widgets)
    if widgets_html:
        base_html = base_html.replace(
            '<section id="contact">',
            widgets_html + '<section id="contact">',
            1,
        )

    # Neutralize form action so it never points to /api/lead or submits via POST
    base_html = re.sub(
        r'<form\s+id="lead"\s+method="post"\s+action="[^"]*"\s+novalidate>',
        '<form id="lead" method="get" action="#contact" data-mf-demo-form="true" novalidate>',
        base_html,
        count=1,
    )

    combined_css = base_css.rstrip() + "\n" + WIDGET_CSS + "\n" + PREVIEW_CSS + "\n"
    return {
        "html": base_html,
        "css": combined_css,
        "js": DEMO_PREVIEW_JS,
        "csp": PREVIEW_CSP,
        "unconfirmed_fields": list(concept.unconfirmed_fields),
    }
