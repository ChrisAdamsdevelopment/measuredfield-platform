// Bounded same-view duplicate-trace review. Not general polygon intersection or cross-sheet matching.
const valid=m=>Array.isArray(m.points)&&m.points.length>0&&m.points.length<=500&&m.points.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y));
const close=(a,b,t)=>Math.hypot(a.x-b.x,a.y-b.y)<=t;
function matches(a,b,t,polygon){
 if(a.length!==b.length)return false;
 // Polygon vertices may start anywhere and follow either winding direction.
 const n=a.length;
 for(let offset=0;offset<(polygon?n:1);offset++)for(const step of [1,-1]){
  const start=polygon?offset:step===1?0:n-1;
  if(a.every((p,i)=>close(p,b[(start+step*i+n)%n],t)))return true;
 }
 return false;
}
export function duplicateTraceReview(measurements,lines,{tolerance=1.5}={}){
 const active=measurements.filter(m=>m.counted!=='no'&&!m.field_entered);
 const groups=new Map(),findings=[],byMeasurement=new Map();
 for(const l of lines)for(const id of l.measurement_ids||[]){const a=byMeasurement.get(id)||[];a.push(l);byMeasurement.set(id,a);}
 for(const m of active){
  if(!valid(m)||!m.sheet_id||!m.assembly?.id||!m.layer||!['line','polyline','polygon','count'].includes(m.kind))continue;
  const keys=[...new Set((byMeasurement.get(m.id)||[]).map(l=>l.material_key).filter(Boolean))].sort();
  if(!keys.length)continue;
  const key=JSON.stringify([m.sheet_id,m.layer,m.kind,m.assembly.id,keys]);
  const g=groups.get(key)||[];g.push(m);groups.set(key,g);
 }
 let comparisons=0;
 for(const group of groups.values())for(let i=0;i<group.length;i++)for(let j=i+1;j<group.length;j++){
  if(++comparisons>20000)return [...findings,{code:'TRACE_REVIEW_LIMIT',measurement_ids:[],line_ids:[]}];
  const a=group[i],b=group[j];
  if(matches(a.points,b.points,tolerance,a.kind==='polygon')){
   const ids=[a.id,b.id];
   findings.push({code:'POSSIBLE_DUPLICATE_TRACE',measurement_ids:ids,line_ids:[...new Set(ids.flatMap(id=>(byMeasurement.get(id)||[]).map(l=>l.line_id)))]});
  }
 }
 // A caller that omits spatial provenance cannot establish that two identical
 // assembly quantities are physically distinct. This is a review flag, not proof.
 const noGeometry=new Map();
 for(const m of active.filter(m=>!valid(m)))for(const l of byMeasurement.get(m.id)||[]){
  if(!l.material_key||!l.assembly_id||!Number.isFinite(l.qty_with_waste))continue;
  const key=JSON.stringify([l.assembly_id,l.material_key,l.layer,l.unit,l.qty_with_waste,m.sheet_id??null]);
  const previous=noGeometry.get(key);
  if(previous&&previous.mid!==m.id)findings.push({code:'DUPLICATE_LOCATION_UNVERIFIED',measurement_ids:[previous.mid,m.id],line_ids:[previous.lid,l.line_id]});
  else noGeometry.set(key,{mid:m.id,lid:l.line_id});
 }
 return findings;
}
