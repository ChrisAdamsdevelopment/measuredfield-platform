// Reviewed quantity-based labor input; re-evaluate on every guided estimate.
import {planLabor} from './labor-plan.mjs';
import {lineBasis} from './bid-readiness.mjs';
export function createLaborActivity(line,{id,description,operation_id,evidence,reviewed,person_hours_per_unit,crew,crew_efficiency,max_workers,wait_workdays_after=0}){
 if(!line||!Number.isFinite(line.qty)||line.qty<=0||!line.unit||!id||!description?.trim()||!operation_id?.trim()||reviewed!==true||!evidence?.trim()||!Number.isFinite(person_hours_per_unit)||person_hours_per_unit<=0)throw Error('Review the measured net quantity, operation and productivity evidence first');
 if(line.needs?.length||line.flags?.includes('calibration_unverified'))throw Error('Resolve quantity requirements or scale before labor pricing');
 const input={crew,tasks:[{id,description,reviewed:true,evidence,qty:line.qty,unit:line.unit,productivity:{unit:line.unit,person_hours_per_unit},crew_efficiency,max_workers,wait_workdays_after}]};
 const result=planLabor(input),r=result.tasks[0];
 if(!result.totals.effort_complete)throw Error('Productivity unresolved');
 return {id,description,operation_id,hours:r.paid_person_hours,amount_cents:r.direct_cost_cents,activity_basis:{line_id:line.line_id,quantity_basis:lineBasis(line),input,active_workdays:r.active_workdays,productive_person_hours:r.adjusted_productive_person_hours,sell_charge_cents:r.sell_charge_cents}};
}
export function effectiveLaborItems(items,lines){
 const map=new Map(lines.map(l=>[l.line_id,l]));
 return items.map(item=>{
  const b=item.activity_basis;if(!b)return item;
  const l=map.get(b.line_id);
  const unresolved=()=>({...item,hours:null,amount_cents:null,rate_cents_per_hour:null,activity_stale:true});
  if(!l||lineBasis(l)!==b.quantity_basis||l.needs?.length||l.flags?.includes('calibration_unverified'))return unresolved();
  try{
   const task=b.input.tasks[0];
   if(b.input.tasks.length!==1||task.qty!==l.qty||task.unit!==l.unit||task.id!==item.id||task.reviewed!==true)return unresolved();
   const r=planLabor(b.input).tasks[0];
   return {...item,hours:r.paid_person_hours,amount_cents:r.direct_cost_cents,rate_cents_per_hour:null,activity_stale:r.direct_cost_cents===null};
  }catch{return unresolved();}
 });
}
