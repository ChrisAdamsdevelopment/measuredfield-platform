/*
 * Real uploaded PDF intake. Never uses filename/page number to invent sheet
 * roles. Text-anchored view suggestions are NOT verified drawing recognition.
 * A physical PDF page can contain several separately scaled drawing views.
 * Text from PDFs is untrusted: render it only as textContent, never as HTML.
 */
import {extractPlanFacts} from "../../src/plan-facts.mjs";
import {proposeScopeFromFacts} from "../../src/scope-items.mjs";
const TERMS=[
 ["SITE PLAN","Site plan"],["EXISTING","Existing"],["DEMOLITION","Demolition"],
 ["DEMO","Demo"],["PROPOSED","Proposed"],["FRAMING","Framing"],
 ["ELEVATION","Elevation"],["DETAIL","Details"],["SCHEDULE","Schedule"],
 ["BATHROOM","Bathroom"],["SHOWER","Shower"],["WINDOW","Window"],
 ["FLOOR PLAN","Floor plan"],["PLUMBING","Plumbing"],["ELECTRICAL","Electrical"],
 ["WATERPROOFING","Waterproofing"],["REVISED","Revision"]
];
const VIEW_TYPES=Object.freeze([
 "Site Plan","Existing / Demo Floor Plan","Proposed Floor Plan",
 "Existing Side Elevation","Proposed Side Elevation",
 "Existing Floor Plan","Demolition Plan","Floor Plan",
 "Structural Framing Plan","General Notes","Structural Details",
 "Architectural Elevations","Reference / Details","Other / Unclassified"
]);
export {VIEW_TYPES};
const clean=s=>s.toUpperCase().replace(/\s+/g," ").trim();
function findDimensions(str){
 const labels=[];
 const matches=str.match(/\b\d{1,5}(?:\.\d{1,2})?\s*(?:S\.?F\.?|SQ\.?\s*FT\.?|SQUARE FEET)\b|\b\d{1,3}\s*['’]\s*(?:-?\s*\d{1,2}(?:\s+\d\/\d)?\s*["”])?/gi)||[];
 for(const raw of matches){
  const label=raw.replace(/\s+/g," ").trim();
  if(label.length>2&&!labels.some(a=>a.toUpperCase()===label.toUpperCase()))labels.push(label);
  if(labels.length===8)break;
 }
 return labels;
}
function clamp(v){return Math.max(0,Math.min(1,v))}
function extractItems(page,items){
 const [left,bottom,right,top]=page.view,w=right-left,h=top-bottom;
 return items.filter(x=>typeof x.str==="string"&&x.str.trim()).map(x=>({
   text:x.str.trim().replace(/\s+/g," "),
   x:clamp(((x.transform?.[4]??left)-left)/w),
   y:clamp(((x.transform?.[5]??bottom)-bottom)/h)
 }));
}
/** Text items in DISPLAY coordinates (page rotation applied, y down) for plan-fact extraction. */
function displayItems(page,items){
 const vp=page.getViewport({scale:1});
 return items.filter(x=>typeof x.str==="string"&&x.str.trim()).map(x=>{
  const [vx,vy]=vp.convertToViewportPoint(x.transform?.[4]??0,x.transform?.[5]??0);
  return {text:x.str.trim().replace(/\s+/g," "),x:clamp(vx/vp.width),y:clamp(vy/vp.height),w:clamp((x.width||0)/vp.width)};
 });
}
function detectViews(items){
 // Locate drawing headings rather than general note sentences or index legends.
 const short=items.filter(x=>x.text.length<=85&&x.text.length>=3);
 const find=pattern=>short.filter(x=>pattern.test(clean(x.text)));
 const findMain=pattern=>find(pattern).filter(x=>x.x<.83&&x.y>.18);
 const near=(a,b)=>Math.abs(a.y-b.y)<=.057&&Math.abs(a.x-b.x)<=.19;
 const first=(a,b)=>a.y>b.y?-1:1;
 const views=[];
 const add=(type,item,source="pdf_text")=>{
  if(!item||views.some(v=>v.type===type))return;
  views.push({
   key:type.toLowerCase().replace(/[^a-z0-9]+/g,"_").replace(/^_|_$/g,""),
   type,title:type,
   source,anchor:{x:item.x,y:item.y}
  });
 };
 const site=findMain(/^SITE PLAN$|^SITE PLAN\b/).sort((a,b)=>a.x-b.x);
 if(site.length)add("Site Plan",site[0]);
 // Distinguish multiple views sharing a PDF page: e.g. EXISTING/DEMO
 // and PROPOSED are headings adjacent to their own FLOOR PLAN labels.
 const floors=findMain(/^FLOOR PLAN$|^FLOOR PLAN\b/);
 const elevs=findMain(/^SIDE ELEVATION$|^SIDE ELEVATION\b/);
 const oldHeadings=findMain(/^(EXISTING\s*\/\s*DEMO|EXISTING\s*\/\s*DEMOLITION|EXISTING)\s*$/);
 const demoHeadings=findMain(/^DEMO(?:LITION)?\s*$/);
 const newHeadings=findMain(/^PROPOSED\s*$/);
 for(const f of floors){
  const original=[...oldHeadings,...demoHeadings].filter(x=>near(x,f))
    .sort((a,b)=>(/DEMO/.test(clean(b.text))?1:0)-(/DEMO/.test(clean(a.text))?1:0)||Math.abs(a.y-f.y)-Math.abs(b.y-f.y))[0];
  const newView=newHeadings.find(x=>near(x,f));
  if(original)add(/DEMO/.test(clean(original.text))?"Existing / Demo Floor Plan":"Existing Floor Plan",f);
  else if(newView)add("Proposed Floor Plan",f);
 }
 if(floors.length&&!views.some(v=>v.type.includes("Floor Plan")))add("Floor Plan",floors[0]);
 for(const e of elevs){
  const old=oldHeadings.find(x=>near(x,e)),newView=newHeadings.find(x=>near(x,e));
  if(old)add("Existing Side Elevation",e);
  else if(newView)add("Proposed Side Elevation",e);
 }
 if(elevs.length&&!views.some(v=>v.type.includes("Elevation")))add("Architectural Elevations",elevs[0]);
 const notes=findMain(/^GENERAL NOTES$|^--GENERAL NOTES--$/);
 if(notes.length&&!views.some(v=>v.type==="Site Plan"))add("General Notes",notes[0]);
 const framing=findMain(/^MAIN LEVEL FRAMING PLAN$|^FRAMING PLAN$|^STRUCTURAL FRAMING PLAN$/);
 if(framing.length)add("Structural Framing Plan",framing[0]);
 const structuralDetail=findMain(/^SHOWER RECESS FRAMING$|^TYPICAL.*(JOIST|HANGER|LEDGER).*DETAIL$|^STRUCTURAL DETAILS$/);
 if(structuralDetail.length)add("Structural Details",structuralDetail[0]);
 if(!views.length){
  const genericDemo=findMain(/^DEMOLITION PLAN$|^DEMO PLAN$/);
  if(genericDemo.length)add("Demolition Plan",genericDemo[0]);
  const genericProp=findMain(/^PROPOSED.*FLOOR PLAN$/);
  if(genericProp.length)add("Proposed Floor Plan",genericProp[0]);
  const genericEx=findMain(/^EXISTING.*FLOOR PLAN$/);
  if(genericEx.length)add("Existing Floor Plan",genericEx[0]);
 }
 if(!views.length){
  const detail=findMain(/^(?:ELEVATIONS|DETAILS|NOTES)$/);
  if(detail.length)add("Reference / Details",detail[0]);
 }
 if(!views.length)views.push({key:"whole_page",type:"Other / Unclassified",title:"Whole page — confirm drawing type",source:"unknown",anchor:null});
 // Keep multiple proposed/existing views distinct but sorted as printed on page.
 views.sort((a,b)=>(b.anchor?.y??.5)-(a.anchor?.y??.5));
 return views;
}
function scaleAnnotations(items,views){
 const annotations=items.filter(x=>/^SCALE\b/i.test(x.text)||/^NOT TO SCALE$/i.test(x.text)).map(x=>({
  text:x.text.slice(0,85),x:x.x,y:x.y
 }));
 for(const view of views){
  if(!view.anchor){view.scaleNote=null;continue;}
  // Scale text near a drawing heading is indicative; NEVER auto-calibrate.
  // Exclude distant labels on other drawings of the same page.
  const nearby=annotations.map(a=>({
    a,d:Math.abs(a.y-view.anchor.y)+Math.abs(a.x-view.anchor.x)*.22
  })).filter(o=>Math.abs(o.a.y-view.anchor.y)<=.08).sort((a,b)=>a.d-b.d);
  view.scaleNote=nearby[0]?.a.text||null;
 }
 return annotations.slice(0,12).map(x=>x.text);
}
export async function inspectPdf(pdf,{docId=null}={}){
 if(!pdf||!Number.isInteger(pdf.numPages))throw Error("Missing PDF document");
 const sheets=[],factPages=[];
 for(let i=1;i<=pdf.numPages;i++){
  const page=await pdf.getPage(i);
  let layer;
  try{layer=await page.getTextContent({includeMarkedContent:false});}
  catch{
   sheets.push({pageIndex:i-1,status:"unavailable",textItems:0,readableChars:0,
     terms:[],candidates:[],suggestedRole:null,suggestedViews:[],scaleLabels:[]});continue;
  }
  const items=extractItems(page,layer.items||[]),text=items.map(x=>x.text).join(" "),uppercase=clean(text);
  factPages.push({doc_id:docId,page_index:i-1,items:displayItems(page,layer.items||[])});
  const terms=[];
  for(const [needle,label] of TERMS)if(uppercase.includes(needle)&&!terms.includes(label))terms.push(label);
  const views=detectViews(items),scaleLabels=scaleAnnotations(items,views);
  const chars=text.replace(/\s/g,"").length;
  sheets.push({
   pageIndex:i-1,status:chars>15?"text_found":"image_or_unreadable",
   textItems:items.length,readableChars:chars,terms:terms.slice(0,10),
   candidates:findDimensions(text),suggestedRole:views.length===1?views[0].type:"Multiple drawing views",
   suggestedViews:views,scaleLabels,
   note:"Suggested from PDF selectable text. Confirm view labels and actual scale before taking off."
  });
 }
 let facts=null,scopeProposal=null;
 try{facts=extractPlanFacts(factPages);scopeProposal=proposeScopeFromFacts(facts);}catch{facts=null;scopeProposal=null;}
 return {
  facts,scopeProposal,factsVersion:2,
  scannedPages:sheets.length,readablePages:sheets.filter(x=>x.status==="text_found").length,sheets,
  method:"pdfjs_text_layer_position_aware",
  limit:"Text-based view suggestions are not verified floorplan interpretation. Printed scale is informational; calibrate each view independently."
 };
}
