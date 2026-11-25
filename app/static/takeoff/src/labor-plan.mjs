// Deterministic labor planner. Local candidate module; no defaults for rate/productivity.
import {multiplyCents} from './estimate.mjs';
const positive=(v,max=1e7)=>typeof v==='number'&&Number.isFinite(v)&&v>0&&v<=max;
const count=(v,max=100)=>Number.isInteger(v)&&v>0&&v<=max;
const money=v=>Number.isSafeInteger(v)&&v>0;
const label=v=>typeof v==='string'&&v.trim().length>0;
const round=v=>Math.round(v*1e6)/1e6;
const ceilDays=v=>Math.ceil(v-1e-10);
export function planLabor(input){
 const {crew,tasks}=input??{};
 if(crew?.billing_policy==='whole_visit_days')return planVisitLabor(input);
 if(!crew||!count(crew.people)||!positive(crew.paid_hours_per_day,24)||!positive(crew.productive_hours_per_day,24)||crew.productive_hours_per_day>crew.paid_hours_per_day)throw Error('Invalid crew capacity');
 if(!Array.isArray(tasks)||tasks.length>500)throw Error('Expected up to 500 ordered tasks');
 for(const rate of [crew.cost_rate,crew.sell_rate]){
  if(rate!=null&&(!money(rate.cents)||!['person_hour','person_day'].includes(rate.basis)))throw Error('Invalid labor rate');
 }
 if(!['prorated','whole_task_days'].includes(crew.billing_policy))throw Error('Explicit billing policy required');
 const rows=[],issues=[],seen=new Set(),done=new Map();
 let cursor=0,clockKnown=true,knownBaseline=0,knownAdjusted=0,knownPaid=0;
 const mark=(id,code)=>issues.push({task_id:id,code});
 for(const task of tasks){
  if(!label(task?.id)||seen.has(task.id))throw Error('Invalid or duplicate task ID');
  seen.add(task.id);
  const deps=task.depends_on??[];
  if(!Array.isArray(deps)||new Set(deps).size!==deps.length||deps.some(id=>!done.has(id)))throw Error('Dependencies must be unique earlier task IDs');
  const wait=task.wait_workdays_after??0;
  if(typeof wait!=='number'||!Number.isFinite(wait)||wait<0||wait>365)throw Error('Invalid waiting period');
  const max=task.max_workers??crew.people;
  if(!count(max)||!positive(task.crew_efficiency,1)||task.crew_efficiency<0.01)throw Error('Invalid task capacity or efficiency (1–100%)');
  const people=Math.min(crew.people,max);
  let baseline=null;
  if(task.reviewed===true&&label(task.evidence)){
   if(task.person_hours!=null&&task.productivity!=null)throw Error('Choose explicit hours OR productivity');
   if(positive(task.person_hours))baseline=task.person_hours;
   else if(task.productivity&&positive(task.qty)&&label(task.unit)&&task.unit===task.productivity.unit&&positive(task.productivity.person_hours_per_unit))baseline=task.qty*task.productivity.person_hours_per_unit;
  }
  if(!positive(baseline,1e9)){
   baseline=null;mark(task.id,'EFFORT_UNRESOLVED');
  }
  const effort=baseline===null?null:baseline/task.crew_efficiency;
  const active=effort===null?null:effort/(people*crew.productive_hours_per_day);
  // Entire task crew is reserved for full task shifts only when explicitly selected.
  const booked=active===null?null:crew.billing_policy==='whole_task_days'?ceilDays(active):active;
  const personDays=booked===null?null:booked*people;
  const paid=personDays===null?null:personDays*crew.paid_hours_per_day;
  const price=rate=>rate&&paid!==null?multiplyCents(rate.basis==='person_hour'?paid:personDays,rate.cents):null;
  const cost=price(crew.cost_rate),sell=price(crew.sell_rate);
  if(cost===null)mark(task.id,'DIRECT_COST_UNRESOLVED');
  if(sell===null)mark(task.id,'SELL_RATE_UNRESOLVED');
  const start=clockKnown?cursor:null;
  const finish=start!==null&&booked!==null?start+booked:null;
  const release=finish===null?null:finish+wait;
  if(release===null)clockKnown=false;else cursor=release;
  const row={id:task.id,description:task.description??task.id,evidence:task.evidence??null,depends_on:deps,assigned_people:people,baseline_person_hours:baseline===null?null:round(baseline),adjusted_productive_person_hours:effort===null?null:round(effort),average_productive_hours_per_assigned_person:effort===null?null:round(effort/people),active_workdays:active===null?null:round(active),booked_workdays:booked===null?null:round(booked),paid_person_hours:paid===null?null:round(paid),billable_person_days:personDays===null?null:round(personDays),wait_workdays_after:wait,start_workday:start===null?null:round(start),finish_workday:finish===null?null:round(finish),release_workday:release===null?null:round(release),direct_cost_cents:cost,sell_charge_cents:sell};
  rows.push(row);done.set(task.id,row);
  knownBaseline+=baseline??0;knownAdjusted+=effort??0;knownPaid+=paid??0;
 }
 const sum=key=>{const n=rows.reduce((a,r)=>a+(r[key]??0),0);if(!Number.isSafeInteger(n))throw Error('Labor amount overflow');return n;};
 if(!rows.length)mark(null,'NO_LABOR_TASKS');
 return {schema:'mf.labor-plan/1',readiness:'review_only',crew:{...crew},tasks:rows,issues,totals:{known_baseline_person_hours:round(knownBaseline),known_adjusted_productive_person_hours:round(knownAdjusted),known_paid_person_hours:round(knownPaid),elapsed_workdays:clockKnown&&rows.length?round(cursor):null,scheduled_workdays:clockKnown&&rows.length?ceilDays(cursor):null,known_direct_cost_cents:sum('direct_cost_cents'),known_sell_charge_cents:sum('sell_charge_cents'),effort_complete:rows.length>0&&rows.every(r=>r.baseline_person_hours!==null),direct_cost_complete:rows.length>0&&rows.every(r=>r.direct_cost_cents!==null),sell_charge_complete:rows.length>0&&rows.every(r=>r.sell_charge_cents!==null)},limitations:['Serial single-crew planning; no calendar dates, parallel trades, critical path or individual assignments.','Waits are explicitly entered working-day equivalents and block this serial schedule; no labor charged during waits.','Crew efficiency, effort and rates must be reviewed; no universal productivity or cost defaults.','Sell charges are separate from direct costs; do not feed sell rates into cost-plus markup.']};
}
// Existing estimating engine accepts direct cost amount rows, including unresolved nulls.
// No sell charges, no automatic markup, no silent zero. Caller must avoid duplicate labor.
export function laborCostItems(plan){
 if(plan?.schema!=='mf.labor-plan/1'||!Array.isArray(plan.tasks))throw Error('Expected labor plan');
 return plan.tasks.map(r=>({id:'labor:'+r.id,description:r.description,hours:r.paid_person_hours,amount_cents:r.direct_cost_cents}));
}

/** Whole-shift minimum charged per explicitly identified contiguous visit, not per task. */
function planVisitLabor(input){
 const {crew,tasks}=input;
 const base=planLabor({...input,crew:{...crew,billing_policy:'prorated'}});
 const groups=[],closed=new Set();
 for(let i=0;i<tasks.length;i++){
  const id=tasks[i].visit_id;
  if(!label(id))throw Error('Every task requires a visit ID for visit-based booking');
  const last=groups.at(-1);
  if(last?.id===id)last.indices.push(i);
  else{if(closed.has(id))throw Error('Visit tasks must be contiguous');closed.add(id);groups.push({id,indices:[i]});}
 }
 const rows=base.tasks.map(r=>({...r})),issues=base.issues.filter(x=>!['DIRECT_COST_UNRESOLVED','SELL_RATE_UNRESOLVED'].includes(x.code));
 let cursor=0,known=true,paidTotal=0;
 for(const g of groups){
  const members=g.indices.map(i=>rows[i]),last=members.at(-1),people=members[0].assigned_people;
  if(members.some(r=>r.assigned_people!==people)||members.slice(0,-1).some(r=>r.wait_workdays_after!==0))throw Error('A visit requires one crew size and no internal hold; split the visit at holds or crew changes');
  const valid=members.every(r=>r.active_workdays!==null);
  if(!valid){
   known=false;
   for(const r of members){Object.assign(r,{visit_id:g.id,booked_workdays:null,paid_person_hours:null,billable_person_days:null,start_workday:null,finish_workday:null,release_workday:null,direct_cost_cents:null,sell_charge_cents:null});issues.push({task_id:r.id,code:'VISIT_EFFORT_UNRESOLVED'});}
   continue;
  }
  // Reuse the reviewed original quantity/effort for unrounded durations.
  const active=g.indices.map(i=>{const t=tasks[i];return (t.person_hours??t.qty*t.productivity.person_hours_per_unit)/t.crew_efficiency/(people*crew.productive_hours_per_day);});
  const sum=active.reduce((a,b)=>a+b,0),booking=ceilDays(sum),padding=booking-sum;
  let accrued=0,priorCost=0,priorSell=0;
  const amount=(rate,days)=>rate?multiplyCents(days*people*(rate.basis==='person_hour'?crew.paid_hours_per_day:1),rate.cents):null;
  for(const [j,r] of members.entries()){
   const days=active[j]+(r===last?padding:0),start=known?cursor:null;
   accrued+=days;paidTotal+=days*people*crew.paid_hours_per_day;
   const cost=amount(crew.cost_rate,accrued),sell=amount(crew.sell_rate,accrued);
   const finish=known?cursor+days:null,release=known?finish+r.wait_workdays_after:null;
   Object.assign(r,{visit_id:g.id,visit_booking_padding_days:r===last?round(padding):0,booked_workdays:round(days),paid_person_hours:round(days*people*crew.paid_hours_per_day),billable_person_days:round(days*people),start_workday:start===null?null:round(start),finish_workday:finish===null?null:round(finish),release_workday:release===null?null:round(release),direct_cost_cents:cost===null?null:cost-priorCost,sell_charge_cents:sell===null?null:sell-priorSell});
   if(cost===null)issues.push({task_id:r.id,code:'DIRECT_COST_UNRESOLVED'});else priorCost=cost;
   if(sell===null)issues.push({task_id:r.id,code:'SELL_RATE_UNRESOLVED'});else priorSell=sell;
   if(known)cursor=release;
  }
 }
 const sum=key=>{const v=rows.reduce((n,r)=>n+(r[key]??0),0);if(!Number.isSafeInteger(v))throw Error('Labor amount overflow');return v;};
 return {...base,crew:{...crew},tasks:rows,issues,totals:{...base.totals,known_paid_person_hours:round(paidTotal),elapsed_workdays:known&&rows.length?round(cursor):null,scheduled_workdays:known&&rows.length?ceilDays(cursor):null,known_direct_cost_cents:sum('direct_cost_cents'),known_sell_charge_cents:sum('sell_charge_cents'),direct_cost_complete:rows.length>0&&rows.every(r=>r.direct_cost_cents!==null),sell_charge_complete:rows.length>0&&rows.every(r=>r.sell_charge_cents!==null)},limitations:[...base.limitations,'Whole visit shifts: minimum booking applied once per contiguous visit; idle booking allocated to its final task. Different crews or internal holds require separate visits. No parallel calendar scheduling.']};
}
