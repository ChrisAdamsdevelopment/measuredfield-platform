// Independent local estimate assurance. Read-only; never creates quantities or prices.
import {scopeItemStage} from './scope-items.mjs';
import {duplicateTraceReview} from './trace-overlap.mjs';
const arr=v=>Array.isArray(v)?v:[];
const finite=v=>typeof v==='number'&&Number.isFinite(v);
export function reconcileEstimate({scopeItems,measurements,takeoff,estimate}={}){
  if(!Array.isArray(scopeItems)||!Array.isArray(measurements)||!Array.isArray(takeoff?.lines))
    throw new TypeError('Complete scope, measurement and takeoff arrays required');
  const findings=[];
  const add=(code,scope_id=null,measurement_ids=[],line_ids=[],severity='review')=>
    findings.push({code,severity,scope_id,measurement_ids:[...new Set(measurement_ids)],line_ids:[...new Set(line_ids)]});
  const lines=takeoff.lines, priced=arr(estimate?.lines);
  const scopes=new Map(scopeItems.map(s=>[s.id,s]));
  const sources=new Map(measurements.map(m=>[m.id,m]));
  const duplicate=(items,key,code)=>{
    const seen=new Set();
    for(const item of items){const id=item[key];if(!id||seen.has(id))add(code,null,[],id?[id]:[],'block');seen.add(id);}
  };
  duplicate(lines,'line_id','DUPLICATE_BOM_ID');
  duplicate(priced,'line_id','DUPLICATE_ESTIMATE_ID');
  duplicate(measurements,'id','DUPLICATE_MEASUREMENT_ID');
  duplicate(scopeItems,'id','DUPLICATE_SCOPE_ID');
  for(const f of duplicateTraceReview(measurements,lines))add(f.code,null,f.measurement_ids,f.line_ids,'block');
  const pricedIds=new Set(priced.map(l=>l.line_id));
  const bomIds=new Set(lines.map(l=>l.line_id));
  if(!Array.isArray(estimate?.lines))add('ESTIMATE_UNAVAILABLE',null,[],[],'block');
  for(const l of lines){
    const ids=arr(l.measurement_ids);
    if(!ids.length||ids.some(id=>!sources.has(id)))add('MATERIAL_SOURCE_MISSING',null,ids,[l.line_id],'block');
    if(ids.some(id=>sources.get(id)?.counted==='no'))add('EXCLUDED_SOURCE_IN_BOM',null,ids,[l.line_id],'block');
    if(!finite(l.qty_with_waste??l.qty)||(l.qty_with_waste??l.qty)<0)add('MATERIAL_QUANTITY_INVALID',null,ids,[l.line_id],'block');
    if(!pricedIds.has(l.line_id))add('MATERIAL_NOT_IN_ESTIMATE',null,ids,[l.line_id],'block');
  }
  for(const l of priced)if(!bomIds.has(l.line_id))add('ESTIMATE_LINE_WITHOUT_BOM',null,[],[l.line_id],'block');
  for(const m of measurements){
    const s=scopes.get(m.scope_item_id);
    if(m.scope_item_id&&!s)add('UNKNOWN_SCOPE_LINK',m.scope_item_id,[m.id],[],'block');
    if(s?.status==='rejected'&&m.counted!=='no')add('REJECTED_SCOPE_COUNTED',s.id,[m.id],[],'block');
    if(s&&m.assembly?.id!==s.work_type&&m.counted!=='no')add('SCOPE_ASSEMBLY_MISMATCH',s.id,[m.id],[],'block');
  }
  for(const s of scopeItems){
    if(s.status==='proposed'){add('SCOPE_DECISION_REQUIRED',s.id,[],[],'block');continue;}
    if(s.status!=='accepted')continue;
    const linked=measurements.filter(m=>m.scope_item_id===s.id&&m.assembly?.id===s.work_type);
    const active=linked.filter(m=>m.counted!=='no');
    const ids=active.map(m=>m.id);
    const coverage=lines.filter(l=>arr(l.measurement_ids).some(id=>ids.includes(id)));
    if(!linked.length){add('SCOPE_NO_EXPLICIT_MEASUREMENT',s.id,[],[],'block');continue;}
    const stage=scopeItemStage(s,linked);
    if(stage.code!=='ready')add('SCOPE_'+stage.code.toUpperCase(),s.id,linked.map(m=>m.id),[],'block');
    for(const m of active)if(!coverage.some(l=>arr(l.measurement_ids).includes(m.id)))
      add('MEASUREMENT_NO_MATERIAL',s.id,[m.id],[],'block');
    if(active.some(m=>m.field_entered===true))add('FIELD_QUANTITY_REVIEW',s.id,ids,coverage.map(l=>l.line_id));
  }
  return {schema:'mf.takeoff.assurance/1',findings,blocking:findings.filter(f=>f.severity==='block').length,
    scope_count:scopeItems.length,material_count:lines.length,limitations:'Checks recorded scope and source links only; cannot prove omitted work outside recorded scope or approve a bid.'};
}
export const ASSURANCE_LABELS=Object.freeze({
 POSSIBLE_DUPLICATE_TRACE:'Potential duplicate trace in the same drawing view and work layer',DUPLICATE_LOCATION_UNVERIFIED:'Matching quantity records lack spatial evidence of distinct work',TRACE_REVIEW_LIMIT:'Duplicate trace review limit reached',
 DUPLICATE_BOM_ID:'Duplicate material line identifier',DUPLICATE_ESTIMATE_ID:'Duplicate priced line identifier',
 DUPLICATE_MEASUREMENT_ID:'Duplicate measurement identifier',DUPLICATE_SCOPE_ID:'Duplicate scope identifier',
 ESTIMATE_UNAVAILABLE:'Estimate calculation unavailable',MATERIAL_SOURCE_MISSING:'Material line lacks a valid measurement source',
 EXCLUDED_SOURCE_IN_BOM:'Material line uses an excluded drawing measurement',MATERIAL_QUANTITY_INVALID:'Material quantity is unknown or invalid',
 MATERIAL_NOT_IN_ESTIMATE:'Material line missing from priced estimate',ESTIMATE_LINE_WITHOUT_BOM:'Priced line has no matching material line',
 UNKNOWN_SCOPE_LINK:'Measurement references an unknown scope item',REJECTED_SCOPE_COUNTED:'Rejected work still has a counted measurement',
 SCOPE_ASSEMBLY_MISMATCH:'Measurement assembly differs from accepted work type',SCOPE_DECISION_REQUIRED:'Decide whether this work belongs in the job',
 SCOPE_NO_EXPLICIT_MEASUREMENT:'Accepted work lacks an explicitly linked measurement',SCOPE_NEEDS_REMEASURE:'Measure on a current drawing',
 SCOPE_MEASURED_DRAFT:'Verify drawing scale',SCOPE_MEASURED_REVIEW:'Review clouded revision',
 SCOPE_RECORDED:'Confirm that all quantities for this work item are recorded',SCOPE_NEEDS_INFO:'Resolve outstanding scope requirements',
 MEASUREMENT_NO_MATERIAL:'Recorded measurement produced no material line',FIELD_QUANTITY_REVIEW:'Confirm field-entered quantity'
});
