/** Portable, consented takeoff evidence for ChatGPT/MCP review.
 * The app remains authoritative. This JSON is READ-ONLY analysis input:
 * never raw PDFs, raw point geometry, account identifiers, customer contacts,
 * internal labor rates or bid totals. No secrets and no network upload.
 */
import {measure as measureGeometry,calibrationStatus} from "./geometry.mjs";
export const REVIEW_SCHEMA="mf.takeoff.review-packet/1";
const txt=(v,n=180)=>{
 if(typeof v!=="string")return "";
 return v.replace(/[\r\n\t]+/g," ")
  .replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi,"[email redacted]")
  .replace(/\b(?:\+1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}\b/g,"[phone redacted]")
  .replace(/https?:\/\/\S+/gi,"[link redacted]").trim().slice(0,n);
};
const list=(v,max=250)=>Array.isArray(v)?v.slice(0,max):[];
const token=(v,n=160)=>txt(v,n);
const four=v=>typeof v==="number"&&Number.isFinite(v)?Math.round(v*10000)/10000:null;
const validStamp=v=>typeof v==="string"&&/^\d{4}-\d{2}-\d{2}$/.test(v)?v:null;

export function buildReviewPacket({project,documents=[],manuals=[],takeoff,estimate,revision,asOf}={}){
 if(!project||!Array.isArray(project.measurements)||!Array.isArray(project.sheets)||!Array.isArray(project.calibrations))
  throw TypeError("Validated takeoff project required");
 if(!takeoff||!Array.isArray(takeoff.lines))throw TypeError("A calculated takeoff is required");
 if(!validStamp(asOf))throw TypeError("Explicit YYYY-MM-DD date required");
 const docs=list(documents,100);
 const docKeys=new Map(docs.map((d,i)=>[d.id,"document_"+(i+1)]));
 const sheetMap=new Map(project.sheets.map(s=>[s.id,s]));
 const calMap=new Map(project.calibrations.map(c=>[c.sheet_id,c]));
 const checkMap=new Map(project.calibration_checks?.map(c=>[c.sheet_id,(project.calibration_checks||[]).filter(x=>x.sheet_id===c.sheet_id)])||[]);
 const excluded=new Map(list(revision?.excluded).map(e=>[e.measurement_id,txt(e.explanation||e.reason,300)]));
 const flagged=new Map(list(revision?.flagged).map(e=>[e.measurement_id,txt(e.explanation||e.reason,300)]));
 const docRef=(id,page)=>({document:docKeys.get(id)||"document_unavailable",page:typeof page==="number"?page+1:null});
 const evidence=e=>({
   ...docRef(e?.doc_id,e?.page_index),
   printed_text:txt(e?.text,220),
   text_matches_pdf:e?.source_text_match===true?true:e?.source_text_match===false?false:null
 });
 const pages=list(project.sheet_register).map(x=>{
   const resolved=revision?.status instanceof Map?revision.status.get(x.id):null;
   return {id:token(x.id),...docRef(x.file_id,x.page_index),
     sheet_number:txt(x.sheet_number,28)||null,revision:Number.isInteger(x.revision)?x.revision:null,
     issued_on:validStamp(x.issued_on),status:txt(resolved?.status||"unresolved",36),
     contractor_confirmed:x.confirmed===true};
 });
 const scopes=list(project.scope_items).map(s=>({
  id:token(s.id),title:txt(s.title),work_type:token(s.work_type),action:token(s.action),
  status:token(s.status),quantity_confirmed:s.quantity_confirmed===true,
  open_needs:list(s.needs,20).map(n=>typeof n==="string"?txt(n,80):txt(n?.type||n?.label,80)).filter(Boolean),
  evidence:list(s.evidence,4).map(evidence)
 }));
 const measurements=list(project.measurements).map(m=>{
   const s=sheetMap.get(m.sheet_id),cal=calMap.get(m.sheet_id);
   const status=m.kind==="count"?"count_no_scale_required":cal?calibrationStatus(cal,checkMap.get(m.sheet_id)||[]):"unset";
   let value=null;
   if(m.kind==="count")value=m.points?.length??null;
   else if(cal){
    try{const computed=measureGeometry(m,cal);
      value=four(m.kind==="polygon"?computed.area_sf:computed.length_ft);}catch{}
   }
   return {id:token(m.id),scope_item_id:token(m.scope_item_id)||null,
     ...docRef(s?.file_id,s?.page_index),view:txt(s?.title,100),
     kind:token(m.kind),layer:token(m.layer),assembly_id:token(m.assembly?.id),
     measured_quantity:value,unit:m.kind==="polygon"?"SF":m.kind==="count"?"EA":"LF",
     scale_status:status,
     counted:!excluded.has(m.id),revision_warning:flagged.get(m.id)||null,
     exclusion_reason:excluded.get(m.id)||null,source:"drawing_trace"};
 });
 const manualQuantities=list(manuals).map(m=>({id:token(m.id),scope_item_id:token(m.scope_item_id)||null,
  assembly_id:token(m.assemblyId),quantity:four(m.quantity),unit:txt(m.unit,40),
  source:"field_entered_unverified"}));
 const estimateMap=new Map(list(estimate?.lines).map(e=>[e.line_id,e]));
 const bom=list(takeoff.lines).map(line=>{
  const priced=estimateMap.get(line.line_id);
  return {line_id:token(line.line_id),assembly_id:token(line.assembly_id),
   description:txt(line.description,260),quantity:four(line.qty_with_waste??line.qty),
   unit:txt(line.unit,24),formula:txt(line.formula,300),
   measurement_ids:list(line.measurement_ids,30).map(x=>token(x)),flags:list(line.flags,20).map(x=>token(x)),
   price_status:token(priced?.price_status||"UNKNOWN"),
   needs_price:priced?.extended_cents==null&&priced?.price_status!=="BY_OWNER",
   pricing_reason:txt(priced?.price_reason||priced?.reason||"",180)||null};
 });
 const printedAreas=docs.flatMap(d=>list(d.textScan?.facts?.areas,24)
  .filter(a=>/REMODEL|AREA OF WORK|ADDITION/i.test(a.label||""))
  .map(a=>({label:txt(a.label,100),printed_sf:four(a.value),
   ...docRef(a.evidence?.doc_id??d.id,a.evidence?.page_index),
   verification:"PRINTED_NOT_MEASURED"})));
 const packet={
  schema:REVIEW_SCHEMA,as_of:asOf,mode:"contractor_opt_in_read_only",
  project:{scope:txt(project.scope,40),code_basis:project.approved_plan_basis?.editions||null,
   // Do not export project/customer name or actual PDF filenames.
   project_name_shared:false,original_pdf_shared:false,raw_geometry_shared:false,cost_amounts_shared:false},
  drawing_register:pages,printed_work_areas:printedAreas,scope_items:scopes,
  measurements,field_entered_quantities:manualQuantities,material_lines:bom,
  price_research_queue:bom.filter(l=>l.needs_price).map(l=>({
   line_id:l.line_id,description:l.description,quantity:l.quantity,unit:l.unit,assembly_id:l.assembly_id,
   require_supplier_and_date:true,do_not_assume_stock_or_price:true
  })),
  warnings:list(takeoff.warnings,60).map(w=>({
   code:token(w.code,60),message:txt(w.message,280)})),
  review_questions:[
   "Do the scope items and measured quantities cover the same work? Cite source ids; do not invent geometry.",
   "Which printed work-area references have not been independently traced?",
   "Are any quantities excluded by drawing revisions or still based on an unverified scale?",
   "Which material lines need supplier pricing, units, coverage, or a contractor quote?",
   "Recommend checks or draft changes only. Do not edit the takeoff, approve a bid, or invent a price."
  ]
 };
 return packet;
}
