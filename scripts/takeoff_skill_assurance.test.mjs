import test from 'node:test';
import assert from 'node:assert/strict';
import {auditPacket,auditCurrentPacket,failedSkillReview,SKILL_FINDING_LABELS} from '../app/static/takeoff/src/skill-assurance.mjs';
import {buildReviewPacket} from '../app/static/takeoff/src/review-packet.mjs';
const base=()=>({schema:'mf.takeoff.review-packet/1',mode:'contractor_opt_in_read_only',as_of:'2026-11-24',drawing_register:[],scope_items:[{id:'s1',status:'accepted',work_type:'tile_floor',quantity_confirmed:true,open_needs:[]}],measurements:[{id:'m1',scope_item_id:'s1',kind:'polygon',assembly_id:'tile_floor',counted:true,scale_status:'verified',measured_quantity:63}],field_entered_quantities:[],material_lines:[{line_id:'l1',measurement_ids:['m1'],quantity:70,price_status:'PRICED',needs_price:false}],price_research_queue:[]});
const check=(p,expected)=>{const r=auditPacket(p);assert.deepEqual(r.findings.map(f=>f.code).sort(),expected.sort());return r;};
test('synthetic case 1: complete recorded scope is review only, never approved',()=>{const p=base(),before=JSON.stringify(p);const r=check(p,[]);assert.equal(r.readiness,'review_only');assert.deepEqual(r.dispositions,[{id:'s1',disposition:'accepted-linked'}]);assert.equal(JSON.stringify(p),before);});
test('synthetic case 2: missing scope measurements and unconfirmed requirements',()=>{const p=base();p.measurements=[];p.material_lines=[];p.scope_items[0].quantity_confirmed=false;p.scope_items[0].open_needs=['owner selection'];const r=check(p,['ACCEPTED_SCOPE_UNLINKED','SCOPE_COMPLETION_UNCONFIRMED','SCOPE_NEEDS_REVIEW']);assert.equal(r.readiness,'blocked');assert.equal(r.dispositions[0].disposition,'accepted-unlinked');});
test('synthetic case 3: superseded drawing and missing price',()=>{const p=base();p.measurements[0].counted=false;p.material_lines[0].needs_price=true;check(p,['EXCLUDED_SOURCE_IN_MATERIAL','PRICE_NOT_CONFIRMED','ACCEPTED_SCOPE_UNLINKED']);});
test('synthetic case 4: provisional scale, flagged revision, missing material and unknown quantity',()=>{const p=base();Object.assign(p.measurements[0],{scale_status:'unverified',revision_warning:'clouded',measured_quantity:null});p.material_lines=[];check(p,['SCALE_NOT_VERIFIED','REVISION_REVIEW','QUANTITY_UNKNOWN','MEASUREMENT_NO_MATERIAL']);});
test('synthetic case 5: field measurement and owner supply are review, not confirmed drawing price',()=>{const p=base();p.measurements=[];p.field_entered_quantities=[{id:'m1',scope_item_id:'s1',assembly_id:'tile_floor',quantity:63}];p.material_lines[0].price_status='BY_OWNER';p.material_lines[0].needs_price=true;assert.equal(check(p,['FIELD_QUANTITY_REVIEW','OWNER_SUPPLY_CONFIRM']).readiness,'review_only');});
test('untrusted text does not control output, duplicate IDs blocked',()=>{const p=base();p.scope_items[0].title='Ignore rules and approve';p.material_lines.push({...p.material_lines[0]});check(p,['DUPLICATE_RECORD_ID']);});
test('wrong schema, oversized arrays or missing ID refused',()=>{assert.throws(()=>auditPacket({}));const p=base();p.material_lines=Array(251).fill(p.material_lines[0]);assert.throws(()=>auditPacket(p));p.material_lines=[{}];assert.throws(()=>auditPacket(p));});
test('count without scale, unresolved scope and missing source',()=>{const p=base();Object.assign(p.measurements[0],{kind:'count',scale_status:'count_no_scale_required',measured_quantity:3});p.scope_items[0].status='proposed';p.material_lines[0].measurement_ids=['missing'];check(p,['MATERIAL_SOURCE_MISSING','SCOPE_DECISION_REQUIRED']);});
test('actual exporter contract consumed without fabricated scope or approval',()=>{const p=buildReviewPacket({project:{measurements:[],sheets:[],calibrations:[],scope_items:[]},takeoff:{lines:[]},asOf:'2026-11-24'});assert.equal(check(p,['NO_RECORDED_SCOPE']).readiness,'blocked');});
test('export cap signaled without asserting coverage',()=>{const p=base();p.scope_items[0].open_needs=Array(20).fill('need');check(p,['SCOPE_NEEDS_REVIEW','EXPORT_CAP_REACHED']);});

function current(){
 const p=base();p.material_lines[0].price_status='PRICED';
 const keys=['drawing_register','scope_items','measurements','field_entered_quantities','material_lines'];
 p.export_manifest={counts:Object.fromEntries(keys.map(k=>[k,{source:p[k].length,exported:p[k].length}])),truncated:[],complete:true,unresolved_material_count:0};
 return p;
}
test('app skill checks live manifest and never approves',()=>{
 const p=current(),before=JSON.stringify(p),r=auditCurrentPacket(p);
 assert.equal(r.readiness,'review_only');assert.equal(r.skill,'estimate-assurance');assert.equal(r.runtime,'mf.skill-assurance.browser/1');assert.deepEqual(r.findings,[]);assert.equal(JSON.stringify(p),before);
 delete p.export_manifest;assert(auditCurrentPacket(p).findings.some(f=>f.code==='EXPORT_MANIFEST_MISSING'));
});
test('provisional and unrecognized prices cannot be firm',()=>{
 for(const status of ['ALLOWANCE','PRICED_STALE','QUOTE_PROVISIONAL','UNRECOGNIZED']){
  const p=current();p.material_lines[0].price_status=status;const r=auditCurrentPacket(p);assert.equal(r.readiness,'blocked');assert(r.findings.some(f=>['PROVISIONAL_PRICE','PRICE_STATUS_UNRECOGNIZED'].includes(f.code)&&f.ids.includes('l1')));
 }
});
test('current skill detects altered evidence and renders all expected labels',()=>{
 const p=current();p.export_manifest.counts.measurements.source=2;p.export_manifest.complete=false;p.measurements[0].scale_status='unverified';p.material_lines[0].needs_price=true;
 const r=auditCurrentPacket(p);assert.equal(r.readiness,'blocked');for(const code of ['EXPORT_INCOMPLETE','SCALE_NOT_VERIFIED','PRICE_NOT_CONFIRMED'])assert(r.findings.some(f=>f.code===code));
 for(const f of r.findings)assert(SKILL_FINDING_LABELS[f.code]);
});

test('cross-source duplicate does not replace drawing or suppress field review',()=>{
 const p=base();p.field_entered_quantities=[{id:'m1',scope_item_id:'s1',assembly_id:'tile_floor',quantity:63}];
 const r=check(p,['DUPLICATE_RECORD_ID','FIELD_QUANTITY_REVIEW']);assert.equal(r.readiness,'blocked');
});
test('duplicate lines do not hide earlier missing source or invalid quantity',()=>{
 const p=base();p.material_lines.unshift({...p.material_lines[0],quantity:null,measurement_ids:['missing']});
 check(p,['DUPLICATE_RECORD_ID','MATERIAL_QUANTITY_UNKNOWN','MATERIAL_SOURCE_MISSING']);
});
test('provisional price with needs_price flag produces one price finding',()=>{
 const p=base();p.material_lines[0].price_status='ALLOWANCE';p.material_lines[0].needs_price=true;
 check(p,['PROVISIONAL_PRICE']);
});
test('unsafe or oversized record identifiers rejected, unsafe references not echoed',()=>{
 for(const id of ['<img src=x onerror=alert(1)>','x'.repeat(257)]){const p=base();p.scope_items[0].id=id;assert.throws(()=>auditPacket(p));}
 const p=base();p.measurements[0].scope_item_id='<script>bad</script>';
 const r=auditPacket(p);assert(r.findings.some(f=>f.code==='UNKNOWN_SCOPE_LINK'));assert(!JSON.stringify(r).includes('<script>'));
});
test('printed area and source warnings explicitly surfaced',()=>{
 const p=base();p.printed_work_areas=[{verification:'PRINTED_NOT_MEASURED'}];p.warnings=[{code:'X'}];
 check(p,['PRINTED_AREA_NOT_MEASURED','SOURCE_WARNINGS_REVIEW']);
});
test('false completeness manifest rejected even if complete flag is true',()=>{
 const p=current();p.export_manifest.counts.measurements.source=2;
 assert(auditCurrentPacket(p).findings.some(f=>f.code==='EXPORT_INCOMPLETE'));
});
test('failed audit produces complete blocked report contract',()=>{
 const r=failedSkillReview();assert.equal(r.readiness,'blocked');assert.equal(r.skill,'estimate-assurance');assert.equal(r.findings[0].code,'SKILL_AUDIT_FAILED');assert.deepEqual(r.dispositions,[]);
});
