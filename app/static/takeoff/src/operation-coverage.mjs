// Explicit cost inclusions and ownership. Never infer that a price includes installation from prose.
const has=v=>typeof v==='string'&&v.trim().length>0;
const allowed=new Set(['supply_only','installed','labor_only','other_nonlabor']);
export function checkOperationCoverage(estimate){
 const lines=estimate?.lines||[],labor=estimate?.labor||[],findings=[],ops=new Map(),ids=new Set();
 const add=(code,ids)=>findings.push({code,ids});
 const op=id=>{if(!ops.has(id))ops.set(id,{installed:[],labor:[]});return ops.get(id)};
 for(const l of lines){
  if(l.extended_cents==null&&l.quote_allocation!=='included')continue;
  if(l.price_status==='BY_OWNER')continue;
  const role=l.cost_inclusion,oid=l.operation_id;
  if(!allowed.has(role)||!has(oid)){
   if(labor.length||l.quote_id)add('COST_INCLUSION_UNREVIEWED',[l.line_id]);
   continue;
  }
  if(role==='installed')op(oid).installed.push(l);
  if(role==='labor_only')op(oid).labor.push(l.line_id);
 }
 for(const l of labor){
  if(ids.has(l.id))add('DUPLICATE_LABOR_ID',[l.id]);ids.add(l.id);
  if(!has(l.operation_id)){add('LABOR_OPERATION_UNASSIGNED',[l.id]);continue;}
  op(l.operation_id).labor.push(l.id);
 }
 for(const [oid,o] of ops){
  // An approved multi-line quote may represent one installed cost, not several.
  const distinct=new Set(o.installed.map(l=>l.quote_id?'quote:'+l.quote_id:'line:'+l.line_id));
  if(distinct.size>1)add('OVERLAPPING_INSTALLED_COSTS',[oid,...o.installed.map(l=>l.line_id)]);
  if(o.installed.length&&o.labor.length)add('INSTALLED_LABOR_DOUBLE_COUNT',[oid,...o.installed.map(l=>l.line_id),...o.labor]);
 }
 return findings;
}
