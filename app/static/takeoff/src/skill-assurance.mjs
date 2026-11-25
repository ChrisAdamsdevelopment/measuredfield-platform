// Browser port of existing estimate-assurance skill; adapted checker with browser validation and bounded reporting.
// Source SHA256: c84a8cadaff081bb21c2fbf35805e0f0dc9b5f1000777989be7dd0b05ad45f99
const keys=['drawing_register','scope_items','measurements','field_entered_quantities','material_lines','price_research_queue'];
const validId=x=>typeof x==='string'&&/^[A-Za-z0-9_.:+-]{1,256}$/.test(x);
const qty=x=>typeof x==='number'&&Number.isFinite(x)&&x>=0;
export function auditPacket(p){
 if(p?.schema!=='mf.takeoff.review-packet/1'||p.mode!=='contractor_opt_in_read_only'||!keys.every(k=>Array.isArray(p[k])))throw Error('Unsupported or incomplete review packet');
 if(keys.some(k=>p[k].length>250))throw Error('Review packet exceeds supported bounds');
 const findings=[],dispositions=[];
 const add=(code,severity,ids=[])=>findings.push({code,severity,ids:ids.filter(validId)});
 const unique=(records,key)=>{
  const map=new Map();
  for(const r of records){if(!r||!validId(r[key]))throw Error('Invalid record ID');if(map.has(r[key])){add('DUPLICATE_RECORD_ID','block',[r[key]]);continue;}map.set(r[key],r);}
  return map;
 };
 if(p.export_manifest){
  const m=p.export_manifest;
  const inconsistent=!m.counts||!Array.isArray(m.truncated)||!Number.isSafeInteger(m.unresolved_material_count)||m.unresolved_material_count<0||keys.filter(k=>k!=='price_research_queue').some(k=>m.counts[k]?.exported!==p[k].length||!Number.isSafeInteger(m.counts[k]?.source)||m.counts[k].source!==m.counts[k].exported);
  if(inconsistent||m.complete!==true||m.truncated?.length||m.unresolved_material_count!==p.price_research_queue.length)add('EXPORT_INCOMPLETE','block');
 }
 const scopes=unique(p.scope_items,'id'),measurements=unique([...p.measurements,...p.field_entered_quantities],'id');
 unique(p.material_lines,'line_id');
 for(const [m,traced] of [...p.measurements.map(m=>[m,true]),...p.field_entered_quantities.map(m=>[m,false])]){
  const id=m.id;
  if(m.scope_item_id&&!scopes.has(m.scope_item_id))add('UNKNOWN_SCOPE_LINK','block',[id,m.scope_item_id]);
  const s=scopes.get(m.scope_item_id);
  if(s?.status==='rejected'&&m.counted!==false)add('REJECTED_SCOPE_COUNTED','block',[s.id,id]);
  if(traced){
   if(m.counted===false)continue;
   if(m.counted!==true)add('COUNTED_STATUS_UNKNOWN','block',[id]);
   if(m.kind!=='count'&&m.scale_status!=='verified')add('SCALE_NOT_VERIFIED','block',[id]);
   if(!qty(m.measured_quantity))add('QUANTITY_UNKNOWN','block',[id]);
   if(m.revision_warning)add('REVISION_REVIEW','block',[id]);
  }else{
   if(!qty(m.quantity))add('QUANTITY_UNKNOWN','block',[id]);
   add('FIELD_QUANTITY_REVIEW','review',[id]);
  }
 }
 const linkedLines=new Set();
 for(const l of p.material_lines){
  const id=l.line_id;
  if(!qty(l.quantity))add('MATERIAL_QUANTITY_UNKNOWN','block',[id]);
  if(!Array.isArray(l.measurement_ids)||!l.measurement_ids.length)add('MATERIAL_SOURCE_MISSING','block',[id]);
  for(const mid of Array.isArray(l.measurement_ids)?l.measurement_ids:[]){
   const m=measurements.get(mid);
   if(!m)add('MATERIAL_SOURCE_MISSING','block',[id,mid]);
   else if(m.counted===false)add('EXCLUDED_SOURCE_IN_MATERIAL','block',[id,mid]);
   else linkedLines.add(mid);
  }
  if(l.price_status==='BY_OWNER')add('OWNER_SUPPLY_CONFIRM','review',[id]);
  else if(['ALLOWANCE','PRICED_STALE','QUOTE_PROVISIONAL'].includes(l.price_status))add('PROVISIONAL_PRICE','block',[id]);
  else if(l.needs_price!==false||['UNKNOWN','UNPRICED','QUOTE_PENDING','PRICE_EXPIRED'].includes(l.price_status))add('PRICE_NOT_CONFIRMED','block',[id]);
  else if(!['PRICED','QUOTE_APPROVED'].includes(l.price_status))add('PRICE_STATUS_UNRECOGNIZED','block',[id]);
 }
 for(const [id,s] of scopes){
  const linked=[...measurements.values()].filter(m=>m.scope_item_id===id&&m.counted!==false);
  let disposition='unknown';
  if(s.status==='accepted'){
   disposition=linked.length?'accepted-linked':'accepted-unlinked';
   if(!linked.length)add('ACCEPTED_SCOPE_UNLINKED','block',[id]);
   if(s.quantity_confirmed!==true)add('SCOPE_COMPLETION_UNCONFIRMED','block',[id]);
   if(!Array.isArray(s.open_needs)||s.open_needs.length)add('SCOPE_NEEDS_REVIEW','block',[id]);
   for(const m of linked){
    if(m.assembly_id!==s.work_type)add('ASSEMBLY_SCOPE_MISMATCH','block',[id,m.id]);
    if(!linkedLines.has(m.id))add('MEASUREMENT_NO_MATERIAL','block',[id,m.id]);
   }
  }else if(s.status==='rejected')disposition='excluded';
  else{disposition='unresolved';add('SCOPE_DECISION_REQUIRED','block',[id]);}
  dispositions.push({id,disposition});
 }
 if(!scopes.size)add('NO_RECORDED_SCOPE','block');
 for(const a of p.printed_work_areas||[])if(a.verification==='PRINTED_NOT_MEASURED')add('PRINTED_AREA_NOT_MEASURED','review');
 if(p.warnings?.length)add('SOURCE_WARNINGS_REVIEW','review');
 if(keys.some(k=>p[k].length===250)||p.scope_items.some(s=>s.open_needs?.length===20)||p.material_lines.some(l=>l.measurement_ids?.length===30))add('EXPORT_CAP_REACHED','block');
 return {schema:'mf.takeoff.skill-assurance/1',as_of:p.as_of??null,readiness:findings.some(f=>f.severity==='block')?'blocked':'review_only',findings:[...new Map(findings.map(f=>[JSON.stringify(f),f])).values()],dispositions,limitations:['Supplied packet only; export may omit records.','No original drawing, requirement, price amount or physical overlap verified.','Not bid approval.']};
}

// Current app packets must have an explicit manifest; allowances are never firm prices.
export function auditCurrentPacket(packet){
 const report=auditPacket(packet);
 const findings=[...report.findings];
 const add=(code,ids=[])=>{if(!findings.some(f=>f.code===code&&JSON.stringify(f.ids)===JSON.stringify(ids)))findings.push({code,severity:'block',ids});};
 if(!packet.export_manifest)add('EXPORT_MANIFEST_MISSING');

 return {...report,skill:'estimate-assurance',runtime:'mf.skill-assurance.browser/1',
  readiness:findings.some(f=>f.severity==='block')?'blocked':'review_only',findings};
}
export function failedSkillReview(){
 return {schema:'mf.takeoff.skill-assurance/1',skill:'estimate-assurance',runtime:'mf.skill-assurance.browser/1',readiness:'blocked',findings:[{code:'SKILL_AUDIT_FAILED',severity:'block',ids:[]}],dispositions:[],limitations:['Evidence audit could not run. No assurance is established.']};
}
export const SKILL_FINDING_LABELS=Object.freeze({
 SKILL_AUDIT_FAILED:'Evidence audit could not run',
 DUPLICATE_RECORD_ID:'Repeated record identifier',EXPORT_INCOMPLETE:'Export omitted or truncated evidence',
 EXPORT_MANIFEST_MISSING:'Evidence manifest missing',UNKNOWN_SCOPE_LINK:'Measurement links to unknown scope',
 REJECTED_SCOPE_COUNTED:'Excluded work still counted',COUNTED_STATUS_UNKNOWN:'Measurement inclusion unknown',
 SCALE_NOT_VERIFIED:'Drawing scale still needs verification',QUANTITY_UNKNOWN:'Quantity is missing or invalid',
 REVISION_REVIEW:'Drawing revision needs review',FIELD_QUANTITY_REVIEW:'Confirm field-entered quantity',
 MATERIAL_QUANTITY_UNKNOWN:'Material quantity missing',MATERIAL_SOURCE_MISSING:'Material lacks measurement evidence',
 EXCLUDED_SOURCE_IN_MATERIAL:'Material uses an excluded measurement',OWNER_SUPPLY_CONFIRM:'Confirm owner-supplied material and installation',
 PRICE_NOT_CONFIRMED:'Material price not confirmed',PROVISIONAL_PRICE:'Allowance, stale price or provisional quote is not a firm price',
 PRICE_STATUS_UNRECOGNIZED:'Pricing status is not recognized by the current app',
 ACCEPTED_SCOPE_UNLINKED:'Accepted work has no linked measurement',SCOPE_COMPLETION_UNCONFIRMED:'Confirm all quantities for this work',
 SCOPE_NEEDS_REVIEW:'Resolve open scope requirements',ASSEMBLY_SCOPE_MISMATCH:'Assembly differs from accepted work',
 MEASUREMENT_NO_MATERIAL:'Measurement produced no material line',SCOPE_DECISION_REQUIRED:'Decide whether this work is included',
 NO_RECORDED_SCOPE:'No recorded scope to audit',PRINTED_AREA_NOT_MEASURED:'Printed work area has not been measured',
 SOURCE_WARNINGS_REVIEW:'Review drawing and takeoff warnings',EXPORT_CAP_REACHED:'Evidence export reached a limit'
});
