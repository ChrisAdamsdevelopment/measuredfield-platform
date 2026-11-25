// Evidence-bound review of field entries and legitimately superseded drawings.
// Never use an acknowledgement to clear conflicts, absent drawings or rejected scope.
export function quantityReviewItems({manuals=[],excluded=[],register=[],measurements=[]}={}){
 const items=manuals.map(m=>({key:'field:'+m.id,type:'field',label:m.label||m.assemblyId,description:'Field-entered quantity '+m.quantity+' '+m.unit,basis:JSON.stringify([m.id,m.assemblyId,m.quantity,m.unit,m.measured,m.params,m.layer,m.scope_item_id]),acknowledgeable:true}));
 for(const e of excluded){
  const m=measurements.find(x=>x.id===e.measurement_id);
  items.push({key:'revision:'+e.measurement_id,type:'revision',label:m?.label||e.measurement_id,description:e.explanation||e.reason,basis:JSON.stringify([e.measurement_id,e.reason,e.page_id,e.explanation,register,m]),acknowledgeable:e.reason==='superseded_sheet'&&!!e.page_id});
 }
 return items;
}
export function reviewedQuantity(item,reviews={}){
 const r=reviews[item.key];return item.acknowledgeable&&r?.basis===item.basis&&typeof r.note==='string'&&r.note.trim().length>=8&&typeof r.reviewed_on==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(r.reviewed_on);
}
