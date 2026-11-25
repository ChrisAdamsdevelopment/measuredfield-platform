// Local guided review: explicit links and review basis, never an approval credential.
export function lineBasis(l){
 return JSON.stringify([l.line_id,l.assembly_id,l.material_key,l.description,l.layer,l.unit,l.qty,l.qty_with_waste,l.waste_pct,l.formula,[...(l.measurement_ids||[])].sort()]);
}
export function coverageBasis(lines,labor){
 return JSON.stringify([lines.map(l=>[lineBasis(l),l.price_status,l.extended_cents,l.quote_id,l.quote_allocation,l.provenance,l.operation_id,l.cost_inclusion]).sort((a,b)=>a[0].localeCompare(b[0])),labor.map(l=>[l.id,l.description,l.hours,l.rate_cents_per_hour,l.amount_cents,l.status,l.operation_id]).sort((a,b)=>String(a[0]).localeCompare(String(b[0])))]);
}
export function fundedLine(l){
 return !!l&&((['PRICED','QUOTE_APPROVED','ALLOWANCE','PRICED_STALE','QUOTE_PROVISIONAL'].includes(l.price_status)&&Number.isSafeInteger(l.extended_cents)&&l.extended_cents>0)||(l.price_status==='QUOTE_APPROVED'&&l.quote_allocation==='included'&&!!l.quote_id));
}
export function scopeCoverage({titles=[],choices={},estimate}={}){
 const lines=estimate?.lines||[],labor=estimate?.labor||[],byLine=new Map(lines.map(l=>[l.line_id,l])),byLabor=new Map(labor.map(l=>[l.id,l]));
 const issues=[],seen=new Set(),seenLabor=new Set(),basis=coverageBasis(lines,labor);
 for(const [i,title] of titles.entries()){
  const s=choices[i]||{},ids=Array.isArray(s.line_ids)?s.line_ids:[],laborIds=Array.isArray(s.labor_ids)?s.labor_ids:[];
  const add=m=>issues.push(title+' — '+m);
  if(!['included','allowance','excluded','na'].includes(s.status)){add('scope decision required');continue;}
  if(['excluded','na'].includes(s.status)){if(!s.note?.trim()||s.note.trim().length<4)add('document why this work is excluded or not applicable');if(ids.length||laborIds.length)add('excluded work has cost links; remove or reassign them');continue;}
  if(s.coverage_basis!==basis)add('confirm coverage against the current quantities, prices and labor');
  if(!ids.length&&!laborIds.length)add('link at least one priced material, quote or labor row');
  for(const id of ids){
   if(seen.has(id))add('material/quote line also assigned to another category: '+id);seen.add(id);
   const l=byLine.get(id);
   if(!fundedLine(l)&&l?.price_status!=='BY_OWNER')add('linked material or quote is missing or unpriced: '+(l?.description||id));
   if(l?.price_status==='BY_OWNER'&&!s.note?.trim())add('document owner supply and installation responsibility');
  }
  for(const id of laborIds){
   if(seenLabor.has(id))add('labor also assigned to another category: '+id);seenLabor.add(id);
   const l=byLabor.get(id);if(!l||!Number.isSafeInteger(l.amount_cents)||l.amount_cents<=0)add('linked labor is missing or unpriced');
  }
  if(!laborIds.length&&(!['included_in_quote','owner_or_external','not_required'].includes(s.labor_disposition)||!s.labor_note?.trim()||s.labor_note.trim().length<4))add('link labor or document who performs it and why no separate charge applies');
  if(s.labor_disposition==='included_in_quote'&&!ids.some(id=>byLine.get(id)?.price_status==='QUOTE_APPROVED'))add('labor included in quote requires an approved linked quote');
  if(laborIds.length&&s.labor_disposition==='included_in_quote')add('separate labor and installed quote may double count work');
  if(s.status==='allowance'&&!s.note?.trim())add('document the allowance scope');
 }
 for(const l of lines)if(!seen.has(l.line_id))issues.push('Unassigned estimate line: '+l.description);
 for(const l of labor)if(!seenLabor.has(l.id))issues.push('Unassigned labor: '+l.description);
 if(!estimate||!estimate.totals?.is_complete)issues.push('Estimate contains incomplete costs');
 if(lines.some(l=>['ALLOWANCE','PRICED_STALE','QUOTE_PROVISIONAL'].includes(l.price_status)))issues.push('Provisional prices or allowances remain; not a firm bid');
 return {issues,basis,readiness:'review_only'};
}
