"""Trusted interactive before/after visual preview composer (MF-015).

Integrates validated ``VisualDesignProposal`` and ``BrandAssetCandidate`` inventories with
MF-014's ``PreviewConceptProfile`` and ``render_concept_preview()`` without ever rendering
AI-supplied HTML/CSS/JS or hotlinking unverified third-party media.
"""
from __future__ import annotations

import html
import re
from typing import Any, Sequence

from ..schemas.ai_visual import (
    DEFAULT_AI_DISCLOSURE,
    BrandAssetCandidate,
    EvidenceItem,
    VisualDesignProposal,
    build_authoritative_before_state,
    validate_proposal_against_inventory,
)
from ..schemas.preview import (
    DiagnosticObservationCard,
    PreviewConceptProfile,
)
from .brand_assets import build_evidence_items_from_card
from .preview_renderer import (
    DEMO_PREVIEW_JS,
    PREVIEW_CSP,
    render_concept_preview,
    render_diagnostic_card_html,
)


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


VISUAL_STUDIO_CSS = """
.mf-studio-toolbar{background:#10252a;color:#e9f4f2;border-bottom:1px solid #2b4950;padding:14px 0}
.mf-toolbar-row{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
.mf-toolbar-group{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.mf-toolbar-label{font-size:.82rem;text-transform:uppercase;letter-spacing:.08em;color:#9ec2bc;font-weight:750;margin-right:4px}
.mf-tool-btn{background:#1d3b42;color:#e9f4f2;border:1px solid #396069;border-radius:7px;padding:8px 13px;font:650 .88rem system-ui;cursor:pointer}
.mf-tool-btn[aria-pressed="true"]{background:var(--accent);color:#fff;border-color:var(--accent)}
.mf-visual-studio[data-mf-view="before"] .mf-pane-after{display:none}
.mf-visual-studio[data-mf-view="after"] .mf-pane-before{display:none}
.mf-visual-studio[data-mf-viewport="mobile"] .mf-pane-after{max-width:390px;margin:24px auto;border:2px solid #2b4950;border-radius:20px;overflow:hidden}
.mf-pane-header{background:#eaf3f1;border-bottom:1px solid #c5dbd7;padding:14px 0}
.mf-pane-header h2{margin:0;font-size:1.25rem;color:#18353c}
.mf-pane-header p{margin:4px 0 0;font-size:.88rem;color:#4d6669}
.mf-asset-inventory{background:#fff;border-bottom:1px solid #dce9e7;padding:34px 0}
.mf-asset-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin-top:14px}
.mf-asset-card{border:1px solid #c5dbd7;border-radius:10px;padding:15px;background:#fbfdfc}
.mf-asset-card h4{margin:6px 0;font-size:1rem;color:#18353c}
.mf-asset-meta{font-size:.84rem;color:#4d6669;margin:4px 0;word-break:break-all}
.mf-proposal-inspector{background:#f2f8f6;border-bottom:1px solid #c5dbd7;padding:34px 0}
.mf-change-list{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin:16px 0}
.mf-change-card{background:#fff;border:1px solid #c5dbd7;border-radius:10px;padding:16px}
.mf-change-card h4{margin:6px 0;font-size:1.02rem;color:#18353c}
.mf-change-meta{font-size:.8rem;color:#536a6d;margin-bottom:8px}
.mf-change-toggle{display:flex;align-items:center;gap:8px;font-weight:700;font-size:.9rem;margin-top:10px;cursor:pointer}
.mf-change-toggle input{width:18px;height:18px;min-height:18px;margin:0}
.mf-brand-slot{border:2px dashed #8ab3ad;background:#f4faf8;border-radius:10px;padding:12px 16px;margin:12px 0;font-size:.88rem;color:#18353c}
.mf-brand-slot-authorized{border-style:solid;border-color:var(--accent);background:#eaf6f3}
.mf-applied-component-bar{background:#eaf6f3;border:1px solid #9ec2bc;border-radius:9px;padding:10px 14px;margin:10px 0;font-size:.86rem;color:#18353c}
[data-mf-preview-component][data-mf-visible="false"]{display:none}
.mf-ai-disclosure{font-size:.83rem;color:#4d6669;margin-top:12px}
@media(max-width:720px){.mf-asset-grid,.mf-change-list{grid-template-columns:1fr}}
""".strip()

VISUAL_PREVIEW_JS = (
    DEMO_PREVIEW_JS.rstrip()
    + """
(function(){
var studio=document.querySelector('[data-mf-studio]');
if(!studio)return;
var viewBtns=studio.querySelectorAll('[data-mf-set-view]');
for(var i=0;i<viewBtns.length;i++){
  (function(btn){
    btn.addEventListener('click',function(){
      var mode=btn.getAttribute('data-mf-set-view')||'split';
      studio.setAttribute('data-mf-view',mode);
      for(var j=0;j<viewBtns.length;j++){
        viewBtns[j].setAttribute('aria-pressed',viewBtns[j]===btn?'true':'false');
      }
    });
  })(viewBtns[i]);
}
var vpBtns=studio.querySelectorAll('[data-mf-set-viewport]');
for(var k=0;k<vpBtns.length;k++){
  (function(btn){
    btn.addEventListener('click',function(){
      var vp=btn.getAttribute('data-mf-set-viewport')||'desktop';
      studio.setAttribute('data-mf-viewport',vp);
      for(var m=0;m<vpBtns.length;m++){
        vpBtns[m].setAttribute('aria-pressed',vpBtns[m]===btn?'true':'false');
      }
    });
  })(vpBtns[k]);
}
var toggles=studio.querySelectorAll('input[data-mf-toggle-change]');
var counter=studio.querySelector('[data-mf-active-changes]');
var heroEl=studio.querySelector('[data-mf-preview-component="hero_banner"]');
var headingEl=studio.querySelector('[data-mf-preview-heading]');
var subheadingEl=studio.querySelector('[data-mf-preview-subheading]');
var ctaEl=studio.querySelector('[data-mf-preview-cta]');
function updateChangeCount(){
  var active=0;
  var heroApplied=false;
  var ctaApplied=false;
  var activeLayout=studio.getAttribute('data-mf-before-layout')||'split_hero_intake';
  var activePalette=studio.getAttribute('data-mf-before-palette')||'ocean';
  for(var n=0;n<toggles.length;n++){
    var box=toggles[n];
    var isOn=!!box.checked;
    var chgId=box.getAttribute('data-mf-toggle-change');
    var compId=box.getAttribute('data-mf-affected-component')||'';
    var target=studio.querySelector('[data-mf-change-card="'+chgId+'"]');
    if(isOn){
      active++;
      if(target)target.setAttribute('data-mf-change-enabled','true');
      var propLayout=box.getAttribute('data-mf-after-layout');
      var propPalette=box.getAttribute('data-mf-after-palette');
      if(propLayout)activeLayout=propLayout;
      if(propPalette)activePalette=propPalette;
    }else{
      if(target)target.setAttribute('data-mf-change-enabled','false');
    }
    var changeTargets=studio.querySelectorAll('[data-mf-change-target="'+chgId+'"]');
    for(var c=0;c<changeTargets.length;c++){
      changeTargets[c].setAttribute('data-mf-visible',isOn?'true':'false');
      changeTargets[c].hidden=!isOn;
    }
    if(compId&&compId!=='hero_banner'){
      var compEls=studio.querySelectorAll('[data-mf-preview-component="'+compId+'"]');
      for(var p=0;p<compEls.length;p++){
        compEls[p].setAttribute('data-mf-visible',isOn?'true':'false');
        compEls[p].hidden=!isOn;
      }
    }
    if(compId==='hero_banner'&&isOn){
      heroApplied=true;
      var afterH=box.getAttribute('data-mf-after-heading');
      var afterSub=box.getAttribute('data-mf-after-subheading');
      var afterCta=box.getAttribute('data-mf-after-cta');
      var afterTarget=box.getAttribute('data-mf-after-target');
      if(headingEl&&afterH)headingEl.textContent=afterH;
      if(subheadingEl&&afterSub)subheadingEl.textContent=afterSub;
      if(ctaEl&&afterCta)ctaEl.textContent=afterCta;
      if(ctaEl&&afterTarget&&afterTarget.charAt(0)==='#')ctaEl.setAttribute('href',afterTarget);
    }
    if(compId==='primary_cta'&&isOn){
      ctaApplied=true;
      var ctaLbl=box.getAttribute('data-mf-after-cta');
      if(ctaEl&&ctaLbl)ctaEl.textContent=ctaLbl;
    }
  }
  if(!heroApplied&&heroEl){
    var beforeH=heroEl.getAttribute('data-mf-before-heading')||'';
    var beforeSub=heroEl.getAttribute('data-mf-before-subheading')||'';
    if(headingEl&&beforeH)headingEl.textContent=beforeH;
    if(subheadingEl&&beforeSub)subheadingEl.textContent=beforeSub;
  }
  if(!heroApplied&&!ctaApplied&&heroEl&&ctaEl){
    var beforeCta=heroEl.getAttribute('data-mf-before-cta')||'Request a conversation';
    var beforeHref=heroEl.getAttribute('data-mf-before-target')||'#contact';
    ctaEl.textContent=beforeCta;
    ctaEl.setAttribute('href',beforeHref);
    ctaEl.setAttribute('data-mf-visible','true');
    ctaEl.hidden=false;
  }
  studio.setAttribute('data-mf-layout',activeLayout);
  studio.setAttribute('data-mf-palette',activePalette);
  if(counter){
    counter.textContent=active+' of '+toggles.length+' proposed improvements enabled in preview';
  }
}
for(var t=0;t<toggles.length;t++){
  toggles[t].addEventListener('change',updateChangeCount);
}
var attestBoxes=studio.querySelectorAll('input[data-mf-attest-asset]');
for(var a=0;a<attestBoxes.length;a++){
  (function(box){
    box.addEventListener('change',function(){
      var aid=box.getAttribute('data-mf-attest-asset');
      var slots=studio.querySelectorAll('[data-mf-asset-slot="'+aid+'"]');
      for(var s=0;s<slots.length;s++){
        var stateEl=slots[s].querySelector('[data-mf-asset-slot-state]');
        if(box.checked){
          slots[s].classList.add('mf-brand-slot-authorized');
          if(stateEl)stateEl.textContent='Owner rights attested in preview (saved only upon claim)';
        }else{
          slots[s].classList.remove('mf-brand-slot-authorized');
          if(stateEl)stateEl.textContent='Unconfirmed candidate — owner rights confirmation required';
        }
      }
    });
  })(attestBoxes[a]);
}
})();
"""
)


def merge_proposal_with_concept(
    profile: PreviewConceptProfile | dict[str, Any],
    proposal: VisualDesignProposal,
    *,
    evidence_items: Sequence[EvidenceItem],
    assets: Sequence[BrandAssetCandidate],
    active_change_ids: set[str] | None = None,
) -> PreviewConceptProfile:
    """Validate ``proposal`` against inventory and merge safe recommendations into a ``PreviewConceptProfile``."""
    validate_proposal_against_inventory(proposal, evidence_items, assets)

    concept = (
        profile.model_copy(deep=True)
        if isinstance(profile, PreviewConceptProfile)
        else PreviewConceptProfile.model_validate(profile)
    )

    updates: dict[str, Any] = {"color": proposal.recommended_palette}
    unconfirmed = list(concept.unconfirmed_fields)

    for chg in proposal.changes:
        if active_change_ids is not None and chg.change_id not in active_change_ids:
            continue
        if chg.component_id == "hero_banner" and chg.after_config.heading_text and not concept.headline:
            updates["headline"] = chg.after_config.heading_text
            if "headline" not in unconfirmed:
                unconfirmed.append("headline")
        if chg.component_id == "hero_banner" and chg.after_config.subheading_text and not concept.intro:
            updates["intro"] = chg.after_config.subheading_text
            if "intro" not in unconfirmed:
                unconfirmed.append("intro")

    if proposal.selected_logo_asset_id and "brand_logo_rights" not in unconfirmed:
        unconfirmed.append("brand_logo_rights")
    if proposal.selected_hero_asset_id and "hero_image_rights" not in unconfirmed:
        unconfirmed.append("hero_image_rights")

    updates["unconfirmed_fields"] = unconfirmed
    return concept.model_copy(update=updates)


def _render_asset_inventory_html(
    assets: Sequence[BrandAssetCandidate],
    authorized_ids: set[str],
) -> str:
    safe_assets = [a for a in assets if a.verification_state != "rejected_unsafe"]
    rejected_count = sum(1 for a in assets if a.verification_state == "rejected_unsafe")

    if not safe_assets:
        cards_html = (
            "<p>No safe raster logo or photography candidates were discovered in the initial static HTML.</p>"
        )
    else:
        cards: list[str] = []
        for asset in safe_assets:
            is_auth = asset.id in authorized_ids or asset.license_state == "owner_attested"
            dims_str = (
                f"{asset.dimensions.width}&times;{asset.dimensions.height}px"
                if asset.dimensions
                else "Dimensions not declared in HTML"
            )
            checked_attr = " checked" if is_auth else ""
            cards.append(
                f'<article class="mf-asset-card" data-mf-asset-id="{_e(asset.id)}">'
                f'<span class="mf-badge mf-badge-observed">{_e(asset.kind.replace("_", " "))}</span> '
                f'<span class="mf-badge mf-badge-unable_to_determine">{_e(asset.verification_state)}</span>'
                f"<h4>Candidate {_e(asset.id)} ({_e(asset.discovery_source)})</h4>"
                f'<p class="mf-asset-meta"><strong>Source URL:</strong> {_e(asset.source_url)}</p>'
                f'<p class="mf-asset-meta"><strong>Format / Size:</strong> {_e(asset.mime)} &middot; {dims_str}</p>'
                f'<p class="mf-asset-meta"><strong>Alt text:</strong> {_e(asset.alt_text or "(none)")}</p>'
                f'<label class="mf-change-toggle" for="mf-attest-{_e(asset.id)}">'
                f'<input type="checkbox" id="mf-attest-{_e(asset.id)}" '
                f'data-mf-attest-asset="{_e(asset.id)}"{checked_attr}>'
                f"I own or have permission to use this {_e(asset.kind.replace('_', ' '))}"
                f"</label>"
                f"</article>"
            )
        cards_html = f'<div class="mf-asset-grid">{"".join(cards)}</div>'

    rejected_notice = (
        f'<p class="mf-ai-disclosure">Excluded {rejected_count} unsafe or active-markup (SVG/script) asset reference(s).</p>'
        if rejected_count
        else ""
    )
    return (
        '<section class="mf-asset-inventory" aria-labelledby="mf-assets-heading">'
        '<div class="wrap">'
        '<div class="eyebrow">Discovered Brand Media Candidates</div>'
        '<h3 id="mf-assets-heading">Logos, Icons &amp; Photos Found on Page</h3>'
        "<p>Third-party images are never hotlinked or copied to permanent hosting without owner confirmation.</p>"
        f"{cards_html}"
        f"{rejected_notice}"
        "</div></section>"
    )


def _render_proposal_inspector_html(
    proposal: VisualDesignProposal,
    evidence_items: Sequence[EvidenceItem] = (),
) -> str:
    evidence_by_id = {ev.id: ev for ev in evidence_items}
    if not proposal.changes:
        changes_html = (
            "<p>No missing-feature layout changes were proposed from the static HTML observations.</p>"
        )
    else:
        cards: list[str] = []
        for chg in proposal.changes:
            ev_list = ", ".join(_e(eid) for eid in chg.evidence_ids)
            authoritative_before = (
                build_authoritative_before_state(chg.evidence_ids, evidence_by_id)
                if evidence_by_id
                else chg.before_state
            )
            asset_note = (
                f" &middot; Candidate asset: <code>{_e(chg.after_config.asset_id)}</code>"
                if chg.after_config.asset_id
                else ""
            )
            cards.append(
                f'<article class="mf-change-card" data-mf-change-card="{_e(chg.change_id)}" data-mf-change-enabled="true">'
                f'<div class="mf-change-meta">'
                f"Component: <strong>{_e(chg.component_id)}</strong> &middot; "
                f"Evidence: <code>{ev_list}</code> &middot; "
                f"Confidence: {_e(chg.confidence)}{asset_note}"
                f"</div>"
                f"<h4>{_e(chg.title)}</h4>"
                f"<p><strong>Observed before:</strong> {_e(authoritative_before)}</p>"
                f"<p><strong>Why change this:</strong> {_e(chg.rationale)}</p>"
                f'<label class="mf-change-toggle" for="mf-toggle-{_e(chg.change_id)}">'
                f'<input type="checkbox" id="mf-toggle-{_e(chg.change_id)}" '
                f'data-mf-toggle-change="{_e(chg.change_id)}" '
                f'data-mf-affected-component="{_e(chg.component_id)}" '
                f'data-mf-after-heading="{_e(chg.after_config.heading_text)}" '
                f'data-mf-after-subheading="{_e(chg.after_config.subheading_text)}" '
                f'data-mf-after-cta="{_e(chg.after_config.cta_label)}" '
                f'data-mf-after-target="{_e(chg.after_config.cta_target)}" '
                f'data-mf-after-palette="{_e(chg.after_config.palette_key)}" '
                f'data-mf-after-layout="{_e(chg.after_config.layout_preset)}" checked>'
                f"Include this improvement in draft"
                f"</label>"
                f"</article>"
            )
        changes_html = f'<div class="mf-change-list">{"".join(cards)}</div>'

    warnings_html = ""
    if proposal.warnings:
        w_items = "".join(f"<li>{_e(w)}</li>" for w in proposal.warnings)
        warnings_html = f'<ul class="mf-diag-unknowns">{w_items}</ul>'

    return (
        '<aside class="mf-proposal-inspector" aria-labelledby="mf-proposal-heading">'
        '<div class="wrap">'
        '<div class="eyebrow">Evidence-Linked Design Proposal</div>'
        '<h3 id="mf-proposal-heading">Recommended Improvements ('
        f'Palette: {_e(proposal.recommended_palette)}, Layout: {_e(proposal.recommended_layout.replace("_", " "))})</h3>'
        f'<p role="status" aria-live="polite" data-mf-active-changes="true">'
        f"{len(proposal.changes)} of {len(proposal.changes)} proposed improvements enabled in preview</p>"
        f"{changes_html}"
        f"{warnings_html}"
        f'<p class="mf-ai-disclosure">{_e(DEFAULT_AI_DISCLOSURE)}</p>'
        "</div></aside>"
    )


def _render_brand_slot_html(
    asset: BrandAssetCandidate,
    *,
    authorized: bool,
    slot_role: str,
    component_id: str = "brand_header",
) -> str:
    cls = "mf-brand-slot mf-brand-slot-authorized" if authorized else "mf-brand-slot"
    state_label = (
        "Owner rights attested in preview (saved only upon claim)"
        if authorized
        else "Unconfirmed candidate — owner rights confirmation required"
    )
    dims_str = (
        f" ({asset.dimensions.width}x{asset.dimensions.height}px)"
        if asset.dimensions
        else ""
    )
    return (
        f'<div class="{cls}" data-mf-asset-slot="{_e(asset.id)}" data-mf-slot-role="{_e(slot_role)}" '
        f'data-mf-preview-component="{_e(component_id)}" data-mf-visible="true">'
        f"<strong>Candidate {_e(asset.kind.replace('_', ' ').title())}: {_e(asset.id)}</strong>"
        f"{_e(dims_str)} &mdash; {_e(asset.alt_text or asset.discovery_source)} "
        f'(<span data-mf-asset-slot-state="true">{_e(state_label)}</span>)'
        f"</div>"
    )


def compose_interactive_visual_preview(
    profile: PreviewConceptProfile | dict[str, Any],
    card: DiagnosticObservationCard,
    assets: Sequence[BrandAssetCandidate],
    proposal: VisualDesignProposal,
    *,
    owner_authorized_asset_ids: set[str] | None = None,
    asset_base: str = "/preview-assets/",
) -> dict[str, Any]:
    """Compose a complete interactive Before/After visual preview bundle."""
    authorized_ids = set(owner_authorized_asset_ids or ())
    evidence_items = build_evidence_items_from_card(card, assets)
    validated_proposal = validate_proposal_against_inventory(proposal, evidence_items, assets)
    initial_concept = (
        profile.model_copy(deep=True)
        if isinstance(profile, PreviewConceptProfile)
        else PreviewConceptProfile.model_validate(profile)
    )
    merged_concept = merge_proposal_with_concept(
        initial_concept,
        validated_proposal,
        evidence_items=evidence_items,
        assets=assets,
    )

    # Render the After concept page without inline diagnostic card so Before and After are distinct panes
    base_bundle = render_concept_preview(merged_concept, card=None, asset_base=asset_base)
    after_html = base_bundle["html"]

    # Annotate rendered After website components so recommendation toggles reversibly apply/revert them
    before_heading = initial_concept.headline or card.observed_h1 or initial_concept.name
    before_subheading = (
        initial_concept.intro
        or card.observed_title
        or f"Serving {initial_concept.city or 'Service Area To Be Confirmed'}."
    )
    after_html = after_html.replace(
        '<div class="hero">',
        f'<div class="hero" data-mf-preview-component="hero_banner" data-mf-visible="true" '
        f'data-mf-before-heading="{_e(before_heading)}" '
        f'data-mf-before-subheading="{_e(before_subheading)}" '
        f'data-mf-before-cta="Request a conversation" '
        f'data-mf-before-target="#contact">',
        1,
    )
    after_html = after_html.replace("<h1>", '<h1 data-mf-preview-heading="true">', 1)
    after_html = after_html.replace(
        '</h1><p>',
        '</h1><p data-mf-preview-subheading="true">',
        1,
    )
    after_html = after_html.replace(
        '<a href="#contact" class="btn">Request a conversation</a>',
        '<a href="#contact" class="btn" data-mf-preview-cta="true" '
        'data-mf-preview-component="primary_cta" data-mf-visible="true">Request a conversation</a>',
        1,
    )
    after_html = after_html.replace(
        'data-mf-widget="scope-explorer">',
        'data-mf-widget="scope-explorer" data-mf-preview-component="scope_explorer" data-mf-visible="true">',
        1,
    )
    after_html = after_html.replace(
        '<section id="services">',
        '<section id="services" data-mf-preview-component="service_cards" data-mf-visible="true">',
        1,
    )
    after_html = after_html.replace(
        '<section id="about" class="about">',
        '<section id="about" class="about" data-mf-preview-component="trust_summary" data-mf-visible="true">',
        1,
    )
    after_html = after_html.replace(
        '<section id="contact">',
        '<section id="contact" data-mf-preview-component="contact_intake" data-mf-visible="true">',
        1,
    )

    # Render reversible change target callout bars inside the After preview for each proposed change
    if proposal.changes:
        target_bars = "".join(
            f'<div class="mf-applied-component-bar" '
            f'data-mf-change-target="{_e(chg.change_id)}" '
            f'data-mf-preview-component="{_e(chg.component_id)}" '
            f'data-mf-visible="true">'
            f"<strong>Active Preview Change ({_e(chg.component_id)}):</strong> {_e(chg.title)}"
            f"</div>"
            for chg in proposal.changes
        )
        after_html = after_html.replace(
            '<main id="top">',
            f'<main id="top"><div class="wrap" data-mf-applied-changes-strip="true">{target_bars}</div>',
            1,
        )

    safe_assets_by_id = {
        a.id: a
        for a in assets
        if a.verification_state != "rejected_unsafe" and a.license_state != "rejected"
    }

    # Inject safe brand asset slots into After concept if selected in proposal
    if proposal.selected_logo_asset_id and proposal.selected_logo_asset_id in safe_assets_by_id:
        logo_asset = safe_assets_by_id[proposal.selected_logo_asset_id]
        logo_slot = _render_brand_slot_html(
            logo_asset,
            authorized=(logo_asset.id in authorized_ids or logo_asset.license_state == "owner_attested"),
            slot_role="header_logo",
            component_id="brand_header",
        )
        after_html = after_html.replace(
            "</header>",
            f'<div class="wrap">{logo_slot}</div></header>',
            1,
        )

    if proposal.selected_hero_asset_id and proposal.selected_hero_asset_id in safe_assets_by_id:
        hero_asset = safe_assets_by_id[proposal.selected_hero_asset_id]
        hero_slot = _render_brand_slot_html(
            hero_asset,
            authorized=(hero_asset.id in authorized_ids or hero_asset.license_state == "owner_attested"),
            slot_role="hero_visual",
            component_id="hero_banner",
        )
        after_html = after_html.replace(
            'data-mf-visible="true">Request a conversation</a>',
            'data-mf-visible="true">Request a conversation</a>' + hero_slot,
            1,
        )

    toolbar_html = (
        '<section class="mf-studio-toolbar" aria-label="Before and after comparison controls">'
        '<div class="wrap mf-toolbar-row">'
        '<div class="mf-toolbar-group" role="group" aria-label="Comparison view mode">'
        '<span class="mf-toolbar-label">Compare View:</span>'
        '<button type="button" class="mf-tool-btn" data-mf-set-view="split" aria-pressed="true">'
        "Split Compare (Before &amp; After)</button>"
        '<button type="button" class="mf-tool-btn" data-mf-set-view="before" aria-pressed="false">'
        "Before: Public HTML &amp; Assets</button>"
        '<button type="button" class="mf-tool-btn" data-mf-set-view="after" aria-pressed="false">'
        "After: Interactive Redesign Concept</button>"
        "</div>"
        '<div class="mf-toolbar-group" role="group" aria-label="Preview screen size">'
        '<span class="mf-toolbar-label">Viewport:</span>'
        '<button type="button" class="mf-tool-btn" data-mf-set-viewport="desktop" aria-pressed="true">'
        "Desktop</button>"
        '<button type="button" class="mf-tool-btn" data-mf-set-viewport="mobile" aria-pressed="false">'
        "Mobile (375px)</button>"
        "</div>"
        "</div></section>"
    )

    before_pane_html = (
        '<section id="mf-pane-before" class="mf-studio-pane mf-pane-before" aria-labelledby="mf-before-title">'
        '<div class="mf-pane-header"><div class="wrap">'
        '<h2 id="mf-before-title">BEFORE: Observed Public HTML &amp; Discovered Brand Media</h2>'
        "<p>Deterministic non-JS HTML observations and candidate brand assets from the current URL.</p>"
        "</div></div>"
        + render_diagnostic_card_html(card)
        + _render_asset_inventory_html(assets, authorized_ids)
        + "</section>"
    )

    after_header_and_inspector = (
        '<section id="mf-pane-after" class="mf-studio-pane mf-pane-after" aria-labelledby="mf-after-title">'
        '<div class="mf-pane-header"><div class="wrap">'
        '<h2 id="mf-after-title">AFTER: Interactive Improvement Concept (Not Published)</h2>'
        "<p>Toggle individual recommendations, explore scope intake, and test mobile/desktop layouts with zero network side effects.</p>"
        "</div></div>"
        + _render_proposal_inspector_html(validated_proposal, evidence_items)
    )

    # Wrap <main id="top">...</main> inside the studio container with Before and After panes
    studio_open = (
        f'<div class="mf-visual-studio" data-mf-studio="true" data-mf-view="split" '
        f'data-mf-viewport="desktop" data-mf-layout="{_e(validated_proposal.recommended_layout)}" '
        f'data-mf-palette="{_e(validated_proposal.recommended_palette)}" '
        f'data-mf-before-layout="split_hero_intake" '
        f'data-mf-before-palette="{_e(initial_concept.color)}">'
        + toolbar_html
        + before_pane_html
        + after_header_and_inspector
    )
    after_html = after_html.replace('<main id="top">', studio_open + '<main id="top">', 1)
    after_html = after_html.replace("</main>", "</main></section></div>", 1)

    # Safety assertion: ensure zero inline style attributes exist in composed HTML
    if re.search(r"\bstyle\s*=", after_html, re.IGNORECASE):
        raise RuntimeError("Composed visual preview HTML must not contain inline style attributes.")

    combined_css = base_bundle["css"].rstrip() + "\n" + VISUAL_STUDIO_CSS + "\n"
    return {
        "html": after_html,
        "css": combined_css,
        "js": VISUAL_PREVIEW_JS,
        "csp": PREVIEW_CSP,
        "proposal": validated_proposal.model_dump(mode="json"),
        "evidence_items": [ev.model_dump() for ev in evidence_items],
        "unconfirmed_fields": list(merged_concept.unconfirmed_fields),
    }
