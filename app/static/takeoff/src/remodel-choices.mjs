// Project-derived remodel selections; no project-type product catalog or assumed prices.
import {lineBasis} from "./bid-readiness.mjs";
export const CHOICE_LEVELS=Object.freeze([["as_specified","As specified / match existing"],["standard","Standard"],["premium","Premium upgrade"],["custom","Custom / specialty"]]);
const allowed=new Set(CHOICE_LEVELS.map(x=>x[0]));
const text=(value,max)=>typeof value==="string"?value.trim().slice(0,max):"";
const rows=project=>Array.isArray(project?.material_choices)?project.material_choices:[];
export const materialChoice=(project,line)=>rows(project).find(x=>x?.line_id===line.line_id&&allowed.has(x.level))||null;
export function normalizeChoice(input={}){
 if(!allowed.has(input.level))throw Error("Choose a valid product selection");
 const specification=text(input.specification,500),supplier=text(input.supplier,160);
 if(input.level==="custom"&&!specification)throw Error("Describe the custom item or specification to request");
 return {level:input.level,specification,supplier};
}
export function changeMaterialChoice(project,line,input){
 const next=normalizeChoice(input),old=materialChoice(project,line);
 if(old&&["level","specification","supplier"].every(k=>old[k]===next[k]))return project;
 if(!old&&next.level==="as_specified"&&!next.specification&&!next.supplier)return project;
 if((project.quotes||[]).some(q=>q?.line_ids?.includes(line.line_id))||
    (project.selections||[]).some(s=>s.line_id===line.line_id&&s.quote_id))
  throw Error("This line has a linked supplier quote. Review or replace the quote first.");
 // Explicit selection change invalidates price and installation classification,
 // never the measured geometry or unrelated lines.
 const prior=(project.selections||[]).find(s=>s.line_id===line.line_id);
 return {...project,material_choices:[...rows(project).filter(x=>x?.line_id!==line.line_id),{line_id:line.line_id,...next,review:null}],
 selections:[...(project.selections||[]).filter(s=>s.line_id!==line.line_id),{
   line_id:line.line_id,mode:next.level==="custom"||line.price_class==="supplier_quote"?"supplier_quote":"unpriced",
   operation_id:prior?.operation_id||null,cost_inclusion:null}],
 labor_scope_reviewed:false};
}
export function materialImpactPending(project,line){
 const c=materialChoice(project,line);
 return Boolean(c&&(!c.review||c.review.basis!==lineBasis(line)||text(c.review.note,500).length<8));
}
export function reviewMaterialImpact(project,line,note){
 const c=materialChoice(project,line),verified=text(note,500);
 if(!c||verified.length<8)throw Error("Record what changed in installation, waste, preparation or labor (or why none changed).");
 return {...project,material_choices:rows(project).map(x=>x?.line_id===line.line_id?{...c,review:{basis:lineBasis(line),note:verified}}:x)};
}
export function remodelDecisionQueue(project,lines=[],estimate=null){
 const prices=new Map((estimate?.lines||[]).map(l=>[l.line_id,l]));
 return lines.map((line,index)=>{
  const choice=materialChoice(project,line),status=prices.get(line.line_id)?.price_status||"UNPRICED";
  const quote=status==="QUOTE_PENDING"||(status==="UNPRICED"&&(choice?.level==="custom"||line.price_class==="supplier_quote"));
  const pending=["UNPRICED","PRICE_EXPIRED","QUOTE_PENDING"].includes(status);
  const selection=!choice&&(line.needs||[]).includes("owner_selection")&&status!=="BY_OWNER";
  const impact=materialImpactPending(project,line);
  const provisional=["ALLOWANCE","PRICED_STALE","QUOTE_PROVISIONAL"].includes(status);
  const group=quote?"quote":pending?"price":selection||impact?"selection":provisional?"allowance":"ready";
  return {line,index,group,status,choice,impact,selection};
 }).sort((a,b)=>["quote","price","selection","allowance","ready"].indexOf(a.group)-["quote","price","selection","allowance","ready"].indexOf(b.group)||a.index-b.index);
}
