/* Model-neutral, source-linked AI plan suggestions.
 * An LLM may hallucinate. No output from this module can become geometry,
 * a BOM quantity, product selection, price, code approval or signed bid.
 */
export const AI_REVIEW_VERSION="0.1.0";
const kinds=new Set(["site","floor_plan","elevation","framing","detail","schedule","notes","unknown"]);
const states=new Set(["existing","demolition","proposed","reference","unknown"]);
const regions=new Set(["left","right","top","bottom","whole","unknown"]);
const short=(v,max=160)=>typeof v==="string"?v.trim().slice(0,max):"";
const normalized=s=>String(s||"").toLowerCase().replace(/[^a-z0-9]+/g,"");
const strList=(list,max,maxLen=160)=>Array.isArray(list)?list.filter(x=>typeof x==="string"&&x.trim()).slice(0,max).map(x=>short(x,maxLen)):[];
export function validateAiSuggestion(payload,{pageIndex,sourceId,sourceText="",now=new Date().toISOString()}={}){
 if(!Number.isInteger(pageIndex)||pageIndex<0||typeof sourceId!=="string"||!sourceId.trim())
   throw Error("Source page and document are required");
 const input=payload?.analysis;
 if(payload?.actual_inference!==true||!input||typeof input!=="object"||Array.isArray(input))
   throw Error("Actual model inference with a structured object is required");
 if(!Array.isArray(input.views)||input.views.length>30)
   throw Error("Model view list is invalid or too large");
 const text=normalized(sourceText);
 const views=input.views.slice(0,12).map((v,index)=>{
  if(!v||typeof v!=="object"||Array.isArray(v))throw Error("Model view item invalid");
  const kind=kinds.has(v.kind)?v.kind:"unknown";
  const state=states.has(v.state)?v.state:"unknown";
  const region=regions.has(v.approximate_region)?v.approximate_region:"unknown";
  const support=short(v.visible_support,240);
  const title=short(v.title,100)||"Unidentified drawing view";
  const needle=normalized(support.replace(/^(heading|label|title)[:\s]+/i,""));
  const matched=Boolean(text&&needle.length>=6&&text.includes(needle));
  return {id:"proposal-"+index,title,kind,state,region,evidence:support,
          source_text_match:matched,review:"unreviewed",pageIndex,sourceId};
 });
 return {schema:"mf.takeoff.ai-review/1",created_at:now,sourceId,pageIndex,
   provider:short(payload.provider,60)||"unknown",
   model:short(payload.model,120)||"unknown",
   status:"unreviewed",page_summary:short(input.page_summary,600),
   views,dimension_labels:strList(input.dimension_labels,8,80),
   scale_labels:strList(input.scale_labels,8,80),
   readable_notes:strList(input.readable_notes,6,200),
   unknowns:strList(input.unknowns,12,180),
   needs_human_review:true,
   audit_note:"AI extracted candidate labels only. Source-text match is evidence of a phrase, not verification of the drawing or any quantity."};
}
export function viewTypeForSuggestion(v){
 switch(v?.kind){
  case "floor_plan": return v.state==="proposed"?"Proposed Floor Plan":v.state==="demolition"?"Demolition Plan":v.state==="existing"?"Existing Floor Plan":"Floor Plan";
  case "site": return "Site Plan";
  case "elevation":return v.state==="proposed"?"Proposed Side Elevation":v.state==="existing"?"Existing Side Elevation":"Architectural Elevations";
  case "framing":return "Structural Framing Plan";
  case "notes":return "General Notes";
  case "detail":return "Reference / Details";
  default:return "Other / Unclassified";
 }
}
