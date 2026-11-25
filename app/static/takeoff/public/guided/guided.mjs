/* MeasuredField Remodel Guided Experience — browser-local contractor-first UI.
   The 16 original assembly engines, PDF geometry, money, revisions and project
   validation remain in src/. This module is the new presentation/adapter layer.
   It never automatically identifies a plan scope or guesses material prices. */
import * as pdfjs from "../vendor/pdfjs-5.7.284/pdf.min.mjs";
import {extractStrokeEndpoints,chooseSnap} from "../../src/vector-snap.mjs";
let snapData=null;
import {createProject,newId,serializeProject,touchProject,validateProject} from "../../src/project.mjs";
import {calibrationFromPoints,calibrationFromPrintedScale,checkCalibration,calibrationStatus,validateMeasurement,measure as measureGeometry} from "../../src/geometry.mjs";
import {listAssemblies,computeAssembly,computeTakeoff} from "../../src/assemblies.mjs";
import {buildEstimate,formatCents} from "../../src/estimate.mjs";
import {listProjects,getProject,saveProject,savePdf,getPdf,openDatabase,restoreProjectBundle} from "./storage.mjs";
import {createBackup,readBackup} from "../../src/project-backup.mjs";
import {quantityReviewItems,reviewedQuantity} from "../../src/quantity-review.mjs";
import {inspectPdf,VIEW_TYPES} from "./pdf-intake.mjs";
import {parsePriceObservations,wholePackageObservationCost} from "./price-observations.mjs";
import {validateAiSuggestion,viewTypeForSuggestion} from "./ai-review.mjs";
import {mergeScope,decideScopeItem,manualScopeItem,scopeProgress,validateAiScope,scopeStageSummary,confirmQuantityComplete,reopenQuantity,resolveNeed} from "../../src/scope-items.mjs";
import {renderJobScope,renderScopeTodo,combineFacts} from "./scope-panel.mjs";
import {syncRegister,duplicateGroups,linkSupersession,markDistinct,clearDecision,confirmIdentity,revisionFilter,pageRecordId,pageStatus,pageLabel} from "../../src/sheet-register.mjs";
import {renderRevisionPanel} from "./revision-panel.mjs";
import {calibrationCandidates,checkCandidates,feetToFeetInches,formatFeetInches,recalibrationImpact,EXCLUSION_REASONS} from "../../src/dimension-assist.mjs";
import {buildReviewPacket} from "../../src/review-packet.mjs";
import {reconcileEstimate,ASSURANCE_LABELS} from "../../src/estimate-assurance.mjs";
import {lineBasis,scopeCoverage} from "../../src/bid-readiness.mjs";
import {checkEstimateTotals} from "../../src/estimate-total-check.mjs";
import {checkOperationCoverage} from "../../src/operation-coverage.mjs";
import {createLaborActivity,effectiveLaborItems} from "../../src/labor-activity.mjs";
import {createFeedback,exportFeedbackPackage} from "../../src/feedback.mjs";
pdfjs.GlobalWorkerOptions.workerSrc="../vendor/pdfjs-5.7.284/pdf.worker.min.mjs";
const $=id=>document.getElementById(id);
const today=()=>{const d=new Date();return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0")};
const cash=n=>Number.isSafeInteger(n)?formatCents(n):"—";
const assemblyDefs=listAssemblies();
const relevant={
  bath:["tile_floor","tile_wall","waterproofing_membrane","cement_board","shower_pan_mortar_bed","linear_drain","wall_framing","window_infill","demolition","wall_drywall","paint","base_trim","count_item","allowance","structural_member","insulation_batt"],
  kitchen:["wall_drywall","wall_framing","tile_floor","tile_wall","demolition","base_trim","paint","count_item","allowance","waterproofing_membrane"],
  interior:["wall_framing","wall_drywall","paint","base_trim","tile_floor","demolition","insulation_batt","count_item","allowance"],
  garage:["wall_framing","wall_drywall","insulation_batt","tile_floor","paint","count_item","demolition","structural_member","allowance"],
  deck:["structural_member","count_item","demolition","allowance","base_trim"]
};
const scopePrompts={
  bath:["Demo, dust protection and disposal","Floor/subfloor and structural changes","Plumbing rough-in and fixtures","Electrical, lighting and ventilation","Shower waterproofing and slope","Tile, grout and sealants","Windows, doors and shower glass","Drywall, trim and painting","Permits, inspections and site protection"],
  kitchen:["Demolition and protection","Cabinets, hardware and installation","Countertop fabrication and delivery","Plumbing, sink and fixtures","Circuits, outlets and lighting","Ventilation and appliances","Flooring, backsplash and finish paint","Trade quotes and site cleanup","Permits and inspections"],
  interior:["Demo and site protection","Framing and structural reviews","Plumbing, electrical and HVAC impacts","Insulation and fire blocking","Drywall, trim and flooring","Painting and cleanup","Permits and inspections"],
  garage:["Existing foundation, drainage and slab","Framing, windows, doors and structure","Insulation and thermal envelope","Fire separation and egress review","Electrical, HVAC and plumbing","Interior finishes and disposal","Plans, permits and inspections"],
  deck:["Footings and excavation","Framing and connectors","Flashing and waterproofing","Deck surfaces, railing and stairs","Fasteners, deliveries and access","Waste and site protection","Permits and design loads"]
};
const state={
  project:null,documents:[],manuals:[],scopeChoices:{},fileDocs:new Map(),
  docId:null,pageIndex:0,sheetId:null,viewKey:null,viewTitle:null,viewAnchor:null,viewRegion:null,focusNextRender:false,viewport:null,zoom:1,renderTask:null,renderSeq:0,
  activeScreen:"home",activeTool:"inspect",pendingTool:null,provisionalScaleSheetId:null,scalePhase:"initial",draft:[],
  takeoff:{lines:[],warnings:[],excluded:[]},estimate:null,
  saveTimer:null,saveBusy:false,persistOkay:true,pdfLoaded:false,
  aiAvailable:false,aiBusy:false,activeScopeItemId:null,
  pageAnchors:[],dimSelected:null,pendingRecal:null,
  revision:{includedIds:new Set(),excluded:[],flagged:[],status:new Map(),resolved:[]}
};
const pdfIds=()=>state.documents.map(d=>d.id);
function say(message,isError=false){
  const el=$("global-notice");el.textContent=message;el.classList.toggle("error",isError);el.hidden=false;
  clearTimeout(say.timer);say.timer=setTimeout(()=>{el.hidden=true},7400);
}
function toast(message){
  const e=$("toast");e.textContent=message;e.hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>{e.hidden=true},3200);
}
function projectName(){return state.project?.name||"New estimate"}
function localMoney(cents){return Number.isInteger(cents)?cash(cents):"—"}
function toCents(input,{zero=false}={}){
  const str=String(input).trim();
  if(!/^(?:0|[1-9]\d{0,8})(?:\.\d{1,2})?$/.test(str))throw Error("Enter a valid dollar amount with up to two decimal places.");
  const [d,cs=""]=str.split(".");
  const amount=Number(d)*100+Number(cs.padEnd(2,"0"));
  if(!Number.isSafeInteger(amount)||(!zero&&amount<=0))throw Error("Price must be greater than zero.");
  return amount;
}
function saveSoon(){
  if(!state.project)return;
  state.project=touchProject(state.project);
  $("save-status").textContent="Saving locally…";
  clearTimeout(state.saveTimer);
  state.saveTimer=setTimeout(()=>persist().catch(err=>say("Could not save this project locally: "+err.message,true)),380);
}
async function persist(){
  if(!state.project)return;
  const result=validateProject(state.project);
  if(result.errors.length){$("save-status").textContent="Needs review";throw Error(result.errors[0]);}
  await saveProject({
    id:state.project.id,project:state.project,documents:state.documents,
    manuals:state.manuals,scopeChoices:state.scopeChoices,updatedAt:Date.now()
  });
  state.persistOkay=true;
  $("save-status").textContent="Saved on this computer";
}
function show(screen){
  const id="screen-"+screen;
  for(const e of document.querySelectorAll(".screen"))e.classList.toggle("screen-active",e.id===id);
  state.activeScreen=screen;
  $("estimate-spine").hidden=!state.project||["home","new","proposal"].includes(screen);
  for(const btn of document.querySelectorAll(".spine button")){
    btn.classList.toggle("active",btn.dataset.step===screen);
    if(btn.classList.contains("active"))btn.setAttribute("aria-current","step");
    else btn.removeAttribute("aria-current");
  }
  $("breadcrumb-project").textContent=state.project?projectName():"New estimate";
  $("export-project").disabled=!state.project;
  $("nav-home").classList.toggle("active",screen==="home");
  $("nav-plan").classList.toggle("active",["plans","measure"].includes(screen));
  $("nav-pricing").classList.toggle("active",["materials","labor","review","proposal"].includes(screen));
  if(screen==="home")renderProjects();
  if(screen==="plans")renderPages();
  if(screen==="measure"){renderMiniPages();refreshMeasureScreen();}
  if(screen==="materials")renderMaterials();
  if(screen==="labor")renderLabor();
  if(screen==="review")renderReview();
  if(screen==="proposal")renderProposal();
  window.scrollTo({top:0,behavior:"instant"});
}
async function renderProjects(){
  const area=$("project-list");area.replaceChildren();
  try{
    const list=(await listProjects()).sort((a,b)=>(b.updatedAt||0)-(a.updatedAt||0));
    if(!list.length){
      const div=document.createElement("div");div.className="empty-projects";
      div.innerHTML='<div class="empty-symbol">▥</div><strong>Your first estimate starts here</strong><span>Create a project to begin.</span>';
      area.append(div);return;
    }
    for(const r of list){
      const btn=document.createElement("button");btn.type="button";btn.className="project-card";
      const strong=document.createElement("strong");strong.textContent=r.project?.name||"Untitled project";
      const kind=document.createElement("small");kind.textContent=(r.project?.scope||"remodel")+" · "+(r.documents?.reduce((n,d)=>n+d.pageCount,0)||0)+" drawing pages";
      const foot=document.createElement("span");foot.className="project-card-footer";foot.textContent="Open saved estimate →";
      btn.append(strong,kind,foot);
      btn.addEventListener("click",()=>openExisting(r.id));
      area.append(btn);
    }
  }catch(err){say("Local project library unavailable: "+err.message,true);}
}
function createNew(mode){
  const name=$("new-name").value.trim()||"Untitled remodel project";
  const type=$("new-type").value;
  state.project=createProject({name,scope:type});
  state.documents=[];state.manuals=[];state.scopeChoices={};state.fileDocs.clear();
  state.docId=null;state.sheetId=null;state.viewKey=null;state.viewTitle=null;state.pageIndex=0;state.draft=[];state.activeTool="inspect";
  $("breadcrumb-project").textContent=name;
  renderAssemblyPicker();
  calculate();
  saveSoon();
  show(mode==="plans"?"plans":"measure");
  if(mode==="plans")$("pdf-input").click();
}
async function openExisting(id){
  try{
    const r=await getProject(id);
    if(!r)throw Error("Project not found on this computer");
    state.project=r.project;state.documents=r.documents||[];state.manuals=r.manuals||[];state.scopeChoices=r.scopeChoices||{};
    state.fileDocs.clear();state.docId=null;state.sheetId=null;state.viewKey=null;state.viewTitle=null;state.pageIndex=0;state.draft=[];
    $("save-status").textContent="Saved on this computer";
    renderAssemblyPicker();
    calculate();
    show(state.documents.length?"plans":"measure");
    toast("Project reopened, including its locally saved drawing files");
  }catch(err){say("Could not reopen project: "+err.message,true);}
}
async function loadPdfDocument(docId){
  if(state.fileDocs.has(docId))return state.fileDocs.get(docId);
  const record=await getPdf(docId);
  if(!record?.blob)throw Error("This PDF is missing from browser storage. Please add the PDF again.");
  const bytes=new Uint8Array(await record.blob.arrayBuffer());
  const pdf=await pdfjs.getDocument({data:bytes,standardFontDataUrl:"../vendor/pdfjs-5.7.284/standard_fonts/",wasmUrl:"../vendor/pdfjs-5.7.284/wasm/"}).promise;
  state.fileDocs.set(docId,pdf);return pdf;
}
async function ingestPdf(file){
  if(!state.project)return say("Create an estimate first.",true);
  if(!file||(!file.name.toLowerCase().endsWith(".pdf")&&file.type!=="application/pdf"))return say("Choose a PDF plan file.",true);
  if(file.size>90*1024*1024)return say("This local demo supports PDFs up to 90 MB.",true);
  let pdf;
  try{
    $("pdf-loaded-title").textContent="Opening "+file.name+"…";
    pdf=await pdfjs.getDocument({data:new Uint8Array(await file.arrayBuffer()),standardFontDataUrl:"../vendor/pdfjs-5.7.284/standard_fonts/",wasmUrl:"../vendor/pdfjs-5.7.284/wasm/"}).promise;
    const id=newId("pdf");
    const record={id,projectId:state.project.id,blob:file,filename:file.name};
    await savePdf(record);
    const meta={id,name:file.name,pageCount:pdf.numPages,uploadedAt:today(),roles:{}};
    try{meta.textScan=await inspectPdf(pdf,{docId:id});}catch(error){meta.textScan={scannedPages:0,readablePages:0,sheets:[],method:"unavailable",limit:"Text scan failed: "+error.message};}
    state.documents.push(meta);state.fileDocs.set(id,pdf);state.docId=id;state.pdfLoaded=true;
    syncScopeFromPlans();
    saveSoon();show("plans");
    toast(pdf.numPages+" of "+pdf.numPages+" pages loaded — your PDF is saved in this browser");
  }catch(err){
    $("pdf-loaded-title").textContent="Upload didn't finish";
    say("Unable to open or save the PDF: "+err.message,true);
  }
}
function currentDocument(){return state.documents.find(d=>d.id===state.docId)||null}
function allPagePairs(){return state.documents.flatMap(d=>Array.from({length:d.pageCount},(_,i)=>({doc:d,index:i})))}
function pageSheets(docId,index){return (state.project?.sheets||[]).filter(s=>s.file_id===docId&&s.page_index===index)}
function sheetFor(docId,index,viewKey){
  return pageSheets(docId,index).find(s=>viewKey==="legacy"?!s.view_key:s.view_key===viewKey)||null;
}
// Suggested views are derived from the user's PDF text, never page-number
// presets. User edits are stored separately from the extraction evidence.
function pageViews(doc,index){
  const inferred=doc.textScan?.sheets?.[index]?.suggestedViews||[];
  const additions=doc.manualViews?.[index]||[];
  const views=[...inferred,...additions].map(v=>({...v,
    title:doc.viewOverrides?.[index+":"+v.key]||v.title,
    type:doc.viewOverrides?.[index+":"+v.key]||v.type}));
  // Existing v0.3 measurements had one sheet per PDF page. Do not silently
  // assign their calibration or quantities to one of several newly found views.
  const legacy=pageSheets(doc.id,index).find(s=>!s.view_key);
  if(legacy&&!views.some(v=>v.key==="legacy"))
    views.unshift({key:"legacy",title:"Earlier whole-page measurements",type:"Other / Unclassified",
      source:"legacy",anchor:null,scaleNote:null});
  if(!views.length)views.push({key:"whole_page",title:"Whole PDF page — choose drawing type",
    type:"Other / Unclassified",source:"unknown",anchor:null,scaleNote:null});
  return views;
}
function ensureSheet(docId,index,view){
  let sheet=sheetFor(docId,index,view.key);
  if(sheet)return sheet;
  const doc=state.documents.find(d=>d.id===docId);
  sheet={id:newId("sheet"),file_id:docId,page_index:index,
    view_key:view.key,sheet_number:"Page "+(index+1),
    title:view.title,revision:0,issued_on:null,accepted_on:null,supersedes:[],
    supersession:"whole_sheet",
    notes:"Drawing view from "+(doc?.name||"uploaded PDF")+
      "; interpretation and calibration require contractor review."};
  state.project.sheets.push(sheet);saveSoon();
  return sheet;
}
async function thumbCanvas(pdf,index,container){
  try{
    const page=await pdf.getPage(index+1);
    const base=page.getViewport({scale:1});
    const v=page.getViewport({scale:Math.min(210/base.width,215/base.height)});
    const canvas=document.createElement("canvas");
    canvas.width=Math.round(v.width);canvas.height=Math.round(v.height);
    await page.render({canvasContext:canvas.getContext("2d"),viewport:v}).promise;
    container.append(canvas);
  }catch{
    const note=document.createElement("span");note.textContent="Preview unavailable — open this page to inspect";note.className="soft-empty";container.append(note);
  }
}
async function renderPages(){
  // Projects saved before multi-view detection have a legacy text scan.
  // Reinspect the ORIGINAL persisted PDF bytes instead of inventing labels.
  for(const doc of state.documents){
    if(!doc.textScan?.sheets?.length ||
       !doc.textScan.sheets.every(s=>Array.isArray(s.suggestedViews)) || (doc.textScan.factsVersion||0)<2){
      try{doc.textScan=await inspectPdf(await loadPdfDocument(doc.id),{docId:doc.id});saveSoon();}
      catch(err){say("This drawing's headings could not be rechecked: "+err.message,true);}
    }
  }
  syncScopeFromPlans();renderScopePanel();
  // New or re-read pages can create or resolve revision conflicts: recompute what counts.
  calculate();renderRevisionPanelUi();
  const total=state.documents.reduce((n,d)=>n+d.pageCount,0);
  $("pdf-loaded-title").textContent=total?total+" of "+total+" pages loaded":"No PDF added yet";
  $("pdf-loaded-description").textContent=total
    ?state.documents.map(d=>d.name).join(" · ")+" · Saved privately in this browser"
    :"Upload any multipage drawing. Every page will be shown individually.";
  $("status-file").innerHTML=total?'<i class="dot dot-sage"></i> PDF opened':'<i class="dot dot-neutral"></i> PDF not loaded';
  $("status-pages").innerHTML=total?'<i class="dot dot-sage"></i> '+total+" pages received":'<i class="dot dot-neutral"></i> Pages: —';
  const textCount=state.documents.reduce((n,d)=>n+(d.textScan?.readablePages||0),0);
  $("status-text").replaceChildren();
  const dot=document.createElement("i");dot.className="dot "+(textCount?"dot-sage":"dot-amber");
  $("status-text").append(dot,document.createTextNode(total?("Selectable text: "+textCount+" of "+total+" pages (not geometry)"):"Text: not examined"));
  $("status-measure").innerHTML='<i class="dot dot-amber"></i> '+state.project?.measurements.length+" traced measurement(s)";
  $("status-pricing").innerHTML='<i class="dot dot-amber"></i> '+(state.estimate?.lines.filter(x=>x.extended_cents!==null).length||0)+" priced line(s)";
  $("page-count-help").textContent=total
    ? "These previews and text indicators come from your uploaded PDF. Text recognition is NOT automatic measurement. Review the page type, then open a drawing to measure its actual geometry."
    : "Upload your own PDF: every page will appear separately, with no preloaded drawings.";
  const intake=$("intake-summary");intake.replaceChildren();intake.hidden=!total;
  if(total){
    const pairs=allPagePairs();
    const scanned=pairs.filter(({doc,index})=>doc.textScan?.sheets?.[index]?.status==="text_found");
    const terms=[...new Set(pairs.flatMap(({doc,index})=>doc.textScan?.sheets?.[index]?.terms||[]))];
    const dims=pairs.filter(({doc,index})=>(doc.textScan?.sheets?.[index]?.candidates||[]).length>0).length;
    const summary=document.createElement("div");summary.className="intake-copy";
    const heading=document.createElement("strong");heading.textContent="Your actual PDF was inspected";
    const detail=document.createElement("p");
    const foundViews=pairs.reduce((n,{doc,index})=>n+pageViews(doc,index).length,0);
    detail.textContent=scanned.length+" of "+total+" pages have selectable text. "+
      "The text suggests "+foundViews+" drawing view(s) across those pages — some pages contain more than one view. "+
      (terms.length?"Keywords: "+terms.slice(0,7).join(", ")+". ":"")+
      (dims?dims+" page(s) contain printed dimension candidates. ":"")+
      "Check each view before measuring. No quantities or prices were inferred.";
    summary.append(heading,detail);intake.append(summary);
    const possibilities=pairs.flatMap(({doc,index})=>pageViews(doc,index).map(view=>({doc,index,view})));
    const likely=possibilities.find(x=>x.view.type==="Proposed Floor Plan")
      ||possibilities.find(x=>x.view.type==="Floor Plan")
      ||possibilities.find(x=>x.view.type==="Site Plan");
    if(likely){
      const btn=document.createElement("button");btn.className="button button-terra";
      btn.textContent="Continue with Page "+(likely.index+1)+" · "+likely.view.title+" →";
      btn.title="Suggested from the PDF text only; verify drawing and scale yourself.";
      btn.onclick=()=>openSheet(likely.doc.id,likely.index,likely.view.key);intake.append(btn);
    }
  }
  const grid=$("pages-grid");grid.replaceChildren();
  if(!total){
    const box=document.createElement("div");box.className="page-empty";
    box.innerHTML='<div class="page-empty-icon">▤</div><h3>See all your drawings at once</h3><p>Every page gets its own preview. Uploading does not measure or invent a price.</p>';
    const btn=document.createElement("button");btn.className="button button-outline";btn.textContent="Choose a PDF";
    btn.onclick=()=>$("pdf-input").click();box.append(btn);grid.append(box);return;
  }
  for(const {doc,index} of allPagePairs()){
    const card=document.createElement("article");card.className="page-card";
    const preview=document.createElement("div");preview.className="page-preview";
    const label=document.createElement("span");label.className="page-index";label.textContent="PAGE "+(index+1)+" / "+doc.pageCount;
    preview.append(label);
    const body=document.createElement("div");body.className="page-body";
    const title=document.createElement("strong");title.textContent="Page "+(index+1);
    const source=document.createElement("small");source.textContent=doc.name;
    const extracted=doc.textScan?.sheets?.[index]||null;
    const status=document.createElement("span");status.className="page-status";
    const views=pageViews(doc,index);
    const saved=pageSheets(doc.id,index);
    const measured=state.project.measurements.filter(m=>saved.some(s=>s.id===m.sheet_id)).length;
    status.textContent=views.length+" drawing view(s) · "+measured+" recorded measurement(s)";
    const viewList=document.createElement("div");viewList.className="page-view-list";
    const heading=document.createElement("strong");heading.className="views-heading";
    heading.textContent=views.length>1?"VIEWS FOUND ON THIS PAGE":"DRAWING VIEW";
    viewList.append(heading);
    for(const view of views){
      const row=document.createElement("div");row.className="page-view-row";
      const title=document.createElement("strong");title.className="view-name";title.textContent=view.title;
      const hint=document.createElement("small");hint.className="view-evidence";
      hint.textContent=view.source==="pdf_text"?"Suggested from your PDF text · confirm below":
        view.source==="legacy"?"Existing whole-page measurements · retain separately":
        view.source==="ai_reviewed"?"AI suggested · contractor classified · verify on drawing":
        "Manually added view";
      const selector=document.createElement("select");selector.className="page-view-type";
      selector.setAttribute("aria-label","Page "+(index+1)+" view type: "+view.title);
      for(const type of VIEW_TYPES)selector.add(new Option(type,type));
      selector.value=VIEW_TYPES.includes(view.type)?view.type:"Other / Unclassified";
      selector.onchange=()=>{
        doc.viewOverrides??={};
        doc.viewOverrides[index+":"+view.key]=selector.value;
        const sheet=sheetFor(doc.id,index,view.key);
        if(sheet)sheet.title=selector.value;
        saveSoon();renderPages();
      };
      const scale=document.createElement("small");scale.className="view-scale";
      const sheet=sheetFor(doc.id,index,view.key);
      const c=state.project.calibrations.find(x=>x.sheet_id===sheet?.id);
      const checks=state.project.calibration_checks.filter(x=>x.sheet_id===sheet?.id);
      const verified=c&&calibrationStatus(c,checks)==="verified";
      scale.textContent=/NOT TO SCALE/i.test(view.scaleNote||"")
        ?"NOT TO SCALE — reference detail, do not trace its graphics"
        :(view.scaleNote?"Printed on drawing: "+view.scaleNote:"Printed scale not confirmed")+
         (verified?" · ✓ Independently verified":" · verify before measuring");
      const button=document.createElement("button");button.className="button button-dark page-action";
      button.type="button";
      button.textContent=/^(General Notes|Reference \/ Details|Other \/ Unclassified)$/.test(view.type)
        ?"Review page reference →":"Open drawing view →";
      button.onclick=()=>openSheet(doc.id,index,view.key);
      row.append(title,hint,selector,scale,button);viewList.append(row);
    }
    const extra=document.createElement("button");extra.className="add-view-button";
    extra.type="button";extra.textContent="+ Add another view on this page";
    extra.onclick=()=>{
      doc.manualViews??={};doc.manualViews[index]??=[];
      if(doc.manualViews[index].length>=12){say("Maximum of 12 manually added views on a page.",true);return;}
      doc.manualViews[index].push({key:newId("view"),title:"Other / Unclassified",
        type:"Other / Unclassified",source:"manual",anchor:null,scaleNote:null});
      saveSoon();renderPages();
    };
    const reading=document.createElement("div");reading.className="pdf-reading";
    if(extracted?.status==="text_found"){
      const summary=document.createElement("strong");
      summary.textContent="PDF text found • "+extracted.readableChars.toLocaleString()+" characters";
      reading.append(summary);
      if(extracted.suggestedRole){
        const guess=document.createElement("small");guess.textContent="Text suggests: "+extracted.suggestedRole+" (confirm sheet type)";
        reading.append(guess);
      }
      if(extracted.terms.length){
        const terms=document.createElement("small");terms.textContent="Found on this page: "+extracted.terms.slice(0,6).join(", ");
        reading.append(terms);
      }
      if(extracted.candidates.length){
        const dims=document.createElement("small");dims.textContent="Printed dimension candidates: "+extracted.candidates.slice(0,4).join(", ")+" — NOT yet measured";
        reading.append(dims);
      }
    }else{
      const summary=document.createElement("strong");summary.textContent=extracted?.status==="image_or_unreadable"?"No readable PDF text detected — manual/OCR review needed":"PDF text has not been analyzed";
      reading.append(summary);
    }
    body.append(title,source,status,viewList,extra,reading);
    if(state.aiAvailable){
      const button=document.createElement("button");button.type="button";
      button.className="button button-outline ai-review-button";
      button.textContent="Analyze this actual page with NVIDIA AI";
      button.title="Opt-in: send this page image and extracted text to NVIDIA for an unverified interpretation";
      button.onclick=()=>analyzePdfPageWithAi(doc,index);
      body.append(button);
    }
    renderAiReview(doc,index,body);
    card.append(preview,body);grid.append(card);
    loadPdfDocument(doc.id).then(pdf=>thumbCanvas(pdf,index,preview)).catch(err=>{
      const msg=document.createElement("span");msg.className="soft-empty";msg.textContent="Preview unavailable. "+err.message;preview.append(msg);
    });
  }
}
async function renderPageForAi(doc,index){
  const pdf=await loadPdfDocument(doc.id),page=await pdf.getPage(index+1);
  const base=page.getViewport({scale:1}),scale=Math.min(2,1800/base.width);
  const v=page.getViewport({scale});
  const canvas=document.createElement("canvas");
  canvas.width=Math.ceil(v.width);canvas.height=Math.ceil(v.height);
  const ctx=canvas.getContext("2d",{alpha:false});
  ctx.fillStyle="#fff";ctx.fillRect(0,0,canvas.width,canvas.height);
  await page.render({canvasContext:ctx,viewport:v}).promise;
  const image=canvas.toDataURL("image/jpeg",.83).split(",")[1];
  const texts=(await page.getTextContent()).items.map(x=>x.str||"").join(" ").slice(0,11900);
  return {image,texts};
}
async function postAi(body){
  const abort=new AbortController();
  const alarm=setTimeout(()=>abort.abort(),85000);
  let res;
  try{
    res=await fetch("/api/interpret-plan-image",{
      method:"POST",headers:{"Content-Type":"application/json"},credentials:"same-origin",
      body:JSON.stringify(body),signal:abort.signal
    });
  }finally{clearTimeout(alarm);}
  if(!res.ok)throw Error("AI provider unavailable or refused this page (HTTP "+res.status+").");
  return res.json();
}
async function analyzeScopeWithAi(doc,index){
  if(!state.aiAvailable){say("Open the optional local AI-review launcher first.",true);return;}
  if(state.aiBusy){say("An AI reading is already in progress.");return;}
  state.aiBusy=true;renderScopePanel();
  try{
    say("The AI is reading the scope on page "+(index+1)+". This sends that page's image and text to NVIDIA.");
    const {image,texts}=await renderPageForAi(doc,index);
    const response=await postAi({image_base64:image,page_text:texts,mode:"scope"});
    const review=validateAiScope(response,{page_index:index,doc_id:doc.id,page_text:texts,at:new Date().toISOString()});
    state.project.ai_scope_reviews??={};
    state.project.ai_scope_reviews[doc.id+"|"+index]=review;
    const before=scopeItems().length;
    state.project.scope_items=mergeScope(scopeItems(),review.items);
    const added=scopeItems().length-before;
    saveSoon();
    say("AI reading finished for page "+(index+1)+": "+review.items.length+" suggestion(s), "+added+" new to your list. Nothing was accepted automatically.");
  }catch(err){say("AI scope reading did not complete: "+err.message,true);}
  finally{state.aiBusy=false;renderScopePanel();}
}
function syncSheetRegister(){
  if(!state.project)return;
  const docs=state.documents.map(d=>{
    const f=d.textScan?.facts;
    const ids=(f?.sheet_identities||[]).map(i=>({...i,
      accepted_on:(f.agency_stamps||[]).filter(s=>s.evidence?.page_index===i.page_index).map(s=>s.date).sort().at(-1)||null}));
    return {id:d.id,name:d.name,pageCount:d.pageCount,identities:ids};
  });
  state.project.sheet_register=syncRegister(state.project.sheet_register||[],docs);
}
function registerChanged(next,message){
  state.project.sheet_register=next;
  calculate();saveSoon();renderRevisionPanelUi();
  if(message)toast(message);
}
function renderRevisionPanelUi(){
  const root=$("revision-panel");if(!root||!state.project)return;
  const records=state.project.sheet_register||[];
  const rf=revisionFilter(records,state.project.sheets,state.project.measurements);
  const at=()=>new Date().toISOString();
  renderRevisionPanel(root,{records,status:rf.status,groups:duplicateGroups(records),excludedCount:rf.excluded.length,
    handlers:{
      link:(ids,currentId,mode)=>{
        let next=records;
        for(const id of ids)if(id!==currentId)next=linkSupersession(next,{currentId,replacedId:id,mode,at:at()});
        registerChanged(next,mode==="whole_sheet"?"Current version confirmed. Quantities on the replaced sheet are no longer counted.":"Clouded revision recorded. Measurements on the older sheet are flagged for review.");
      },
      distinct:ids=>{
        let next=records;
        for(let i=0;i<ids.length;i++)for(let j=i+1;j<ids.length;j++)next=markDistinct(next,ids[i],ids[j],at());
        registerChanged(next,"Recorded as different sheets. Both are counted.");
      },
      undo:(a,b)=>registerChanged(clearDecision(records,a,b),"Decision undone."),
      confirm:(id,fields)=>{
        try{registerChanged(confirmIdentity(records,id,fields,at()),"Sheet details saved.");}
        catch(err){say(err.message,true);}
      }
    }});
}
/** Page-level revision status of the drawing view being measured. */
function currentPageStatus(){
  if(!state.sheetId||!state.project)return null;
  const sheet=currentSheet();if(!sheet)return null;
  const rf=state.revision||{status:new Map()};
  const id=pageRecordId(sheet.file_id,sheet.page_index);
  const resolved=rf.status?.get(id);
  return resolved?{...pageStatus(resolved,state.project.sheet_register||[]),record:(state.project.sheet_register||[]).find(r=>r.id===id)}:null;
}
function renderSheetStatus(){
  const box=$("sheet-status");if(!box)return;
  const st=currentPageStatus();
  box.replaceChildren();
  const visible=st&&["superseded","conflict","partial"].includes(st.code);
  box.hidden=!visible;if(!visible)return;
  box.className="sheet-status sheet-status-"+st.code;
  const strong=document.createElement("strong");
  const p=document.createElement("p");
  if(st.code==="superseded"){strong.textContent="This sheet was replaced — "+st.label+".";p.textContent="Anything measured here is kept for reference but NOT counted. Measure on the current version instead.";}
  else if(st.code==="conflict"){strong.textContent="Two drawings share this sheet number.";p.textContent="Go back to Step 1 and choose which version is current. Until then, quantities from this sheet are not counted.";}
  else{strong.textContent="Clouded changes on a newer revision.";p.textContent=st.label+". Measurements here are counted but flagged — make sure they are outside the revised clouds.";}
  const back=document.createElement("button");back.type="button";back.className="button button-outline compact";back.textContent="Review drawing versions";
  back.addEventListener("click",()=>{show("plans");setTimeout(()=>$("revision-panel")?.scrollIntoView({block:"start"}),50);});
  box.append(strong,p,back);
}
function measuringBlocked(){const st=currentPageStatus();return Boolean(st&&["superseded","conflict"].includes(st.code));}
function scopeItems(){return state.project?.scope_items||[]}
function syncScopeFromPlans(){
  if(!state.project)return;
  let items=scopeItems();
  for(const d of state.documents)items=mergeScope(items,d.textScan?.scopeProposal?.items||[]);
  state.project.scope_items=items;
}
function scopeBoundary(){
  const inS=new Map(),ctx=new Map();
  for(const d of state.documents){
    const p=d.textScan?.scopeProposal;if(!p)continue;
    for(const a of p.in_scope_areas||[])inS.set(a.name,a);
    for(const a of p.context_areas||[])ctx.set(a.name,a);
  }
  for(const r of Object.values(state.project?.ai_scope_reviews||{})){
    for(const a of r.in_scope_areas||[])inS.set(a.name,a);
    for(const a of r.context_areas||[])ctx.set(a.name,a);
  }
  const norm=n=>String(n).toUpperCase().replace(/[^A-Z]/g,"");
  const inNames=new Set([...inS.keys()].map(norm));
  return {in_scope:[...inS.values()],context:[...ctx.values()].filter(c=>!inNames.has(norm(c.name)))};
}
/** Measurements with what the scope stages need: is it counted (revisions) and is its scale verified. */
function allMeasuredForScope(){
  const rf=state.revision||{excluded:[],flagged:[]};
  const plan=(state.project?.measurements||[]).map(m=>{
    const counted=rf.excluded?.some(e=>e.measurement_id===m.id)?"no":rf.flagged?.some(e=>e.measurement_id===m.id)?"flagged":"yes";
    let verified=true;
    if(m.kind!=="count"){
      const cal=state.project.calibrations.find(c=>c.sheet_id===m.sheet_id);
      verified=Boolean(cal)&&calibrationStatus(cal,state.project.calibration_checks.filter(x=>x.sheet_id===m.sheet_id))==="verified";
    }
    return {id:m.id,scope_item_id:m.scope_item_id,assembly:m.assembly,counted,verified,sheet_id:m.sheet_id,layer:m.layer,kind:m.kind,points:m.points};
  });
  // Field-entered quantities were measured on site by the contractor: counted, labelled field-entered.
  const field=state.manuals.map(m=>({id:m.id,scope_item_id:m.scope_item_id,assembly:{id:m.assemblyId},counted:"yes",verified:true}));
  return [...plan,...field];
}
function updateScopeItem(id,fn,message){
  try{
    state.project.scope_items=scopeItems().map(i=>i.id===id?fn(i):i);
    saveSoon();renderScopePanel();renderScopeTodoPanel();
    if(message)toast(message);
  }catch(err){say(err.message,true);}
}
function renderScopePanel(){
  const root=$("job-scope");if(!root||!state.project)return;
  const facts=combineFacts(state.documents.map(d=>d.textScan?.facts));
  const reviews=Object.values(state.project.ai_scope_reviews||{})
    .sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)));
  const last=reviews[0];
  const pairs=allPagePairs();
  const pageOptions=pairs.map(({doc,index})=>{
    const t=pageViews(doc,index).map(v=>v.title).filter(Boolean)[0];
    return {value:doc.id+"|"+index,label:"Page "+(index+1)+(state.documents.length>1?" · "+doc.name:"")+(t?" · "+t:"")};
  });
  const proposed=pairs.find(({doc,index})=>pageViews(doc,index).some(v=>/Proposed/.test(v.type||"")));
  const basis=state.project.approved_plan_basis;
  const measured=allMeasuredForScope();
  const summary=scopeStageSummary(scopeItems(),measured);
  renderJobScope(root,{
    facts,boundary:scopeBoundary(),items:scopeItems(),
    progress:scopeProgress(scopeItems(),measured),summary,
    stages:new Map(summary.rows.map(r=>[r.id,r.stage])),
    codeBasis:{applied:Boolean(basis?.editions&&basis.source==="printed_on_approved_plans"),accepted_on:basis?.accepted_on},
    ai:{available:state.aiAvailable,busy:state.aiBusy,pageOptions,
      defaultPage:proposed?proposed.doc.id+"|"+proposed.index:pageOptions[0]?.value,
      summary:last?.scope_summary||"",selections:last?.selections_needed||[],unknowns:last?.unknowns||[]},
    handlers:{
      decide:(id,decision)=>{
        const at=new Date().toISOString();
        state.project.scope_items=scopeItems().map(i=>i.id===id?decideScopeItem(i,decision,{at}):i);
        if(decision!=="accepted"&&state.activeScopeItemId===id)state.activeScopeItemId=null;
        saveSoon();renderScopePanel();
      },
      acceptAllPlanText:()=>{
        const at=new Date().toISOString();
        state.project.scope_items=scopeItems().map(i=>i.status==="proposed"&&i.sources?.includes("plan_text")?decideScopeItem(i,"accepted",{at}):i);
        saveSoon();renderScopePanel();toast("Plan-note items accepted. Mark any that aren't part of this job.");
      },
      measure:id=>startScopeMeasure(id),
      confirmComplete:id=>updateScopeItem(id,i=>confirmQuantityComplete(i,allMeasuredForScope(),{at:new Date().toISOString()}),"Quantity confirmed complete for this work item."),
      reopen:id=>updateScopeItem(id,i=>reopenQuantity(i),"Work item reopened."),
      resolveNeed:(id,need)=>updateScopeItem(id,i=>resolveNeed(i,need,{at:new Date().toISOString()}),"Marked as resolved."),
      applyCodeBasis:()=>applyPrintedCodeBasis(facts),
      analyzeScope:value=>{
        const cut=String(value).lastIndexOf("|");
        const doc=state.documents.find(d=>d.id===String(value).slice(0,cut));
        if(doc)analyzeScopeWithAi(doc,Number(String(value).slice(cut+1)));
      },
      addManual:(title,work_type)=>{
        const item=manualScopeItem({title,work_type,measure:"polyline",at:new Date().toISOString()});
        state.project.scope_items=[...scopeItems(),item];
        saveSoon();renderScopePanel();toast("Work item added to the job scope.");
      }
    }
  });
}
function applyPrintedCodeBasis(facts){
  const editions={};
  for(const c of facts.code_summary){
    const key=c.family==="IBC"&&c.roles?.includes("design_basis")?"IBC_structural":c.family;
    editions[key]=c.edition;
  }
  const accepted=facts.agency_stamps.filter(s=>s.action==="ACCEPTED"||s.action==="APPROVED").map(s=>s.date).sort()[0]||null;
  state.project.approved_plan_basis={
    editions,accepted_on:accepted,agency:facts.agency_stamps[0]?.agency||null,source:"printed_on_approved_plans",
    revisions:facts.agency_stamps.filter(s=>/^REVISION/.test(s.action)).map(s=>({action:s.action,date:s.date})),
    confirmed_by_user_at:new Date().toISOString(),
    note:"Editions copied from the printed plans at the contractor's request. Confirm with the AHJ; code compliance is not evaluated."
  };
  saveSoon();renderScopePanel();toast("Project code basis set from the approved plans.");
}
function ensureAssemblyOption(id){
  const select=$("measure-assembly");
  if(![...select.options].some(o=>o.value===id)){
    const def=assemblyDefs.find(x=>x.id===id);
    if(def)select.add(new Option(def.label,id));
  }
}
function startScopeMeasure(id){
  const item=scopeItems().find(i=>i.id===id);if(!item)return;
  state.activeScopeItemId=id;
  if(state.activeScreen!=="measure")show("measure");
  ensureAssemblyOption(item.work_type);
  selectAssembly(item.work_type);
  $("measure-layer").value=item.action==="demolish"?"demolish":item.action==="verify"?"field_verify":item.action==="existing_remain"?"existing":"new";
  refreshMeasureScreen();
  const ev=item.evidence?.[0];
  say("Measuring: "+item.title+". "+(item.hint||"")+(ev&&!state.sheetId?" The plans mention it on page "+(ev.page_index+1)+" — open that page or the proposed floor plan to trace it.":""));
}
function renderScopeTodoPanel(){
  const root=$("scope-todo");if(!root||!state.project)return;
  renderScopeTodo(root,{progress:scopeStageSummary(scopeItems(),allMeasuredForScope()),items:scopeItems(),activeId:state.activeScopeItemId,
    onPick:id=>startScopeMeasure(id),onClear:()=>{state.activeScopeItemId=null;renderScopeTodoPanel();}});
}
async function analyzePdfPageWithAi(doc,index){
  if(!state.aiAvailable){say("Open the optional local AI-review launcher first.",true);return;}
  if(state.aiBusy){say("A drawing analysis is already in progress.");return;}
  state.aiBusy=true;
  try{
    say("Analyzing the selected actual drawing page. This sends its rendered image and selectable text to NVIDIA.");
    const {image,texts}=await renderPageForAi(doc,index);
    const response=await postAi({image_base64:image,page_text:texts,mode:"views"});
    const review=validateAiSuggestion(response,{pageIndex:index,sourceId:doc.id,sourceText:texts});
    doc.aiReviews??={};doc.aiReviews[index]=review;
    saveSoon();await renderPages();
    say("Real AI review finished for Page "+(index+1)+". Review the suggested views below; nothing was added automatically.");
  }catch(err){say("AI review did not complete: "+err.message,true);}
  finally{state.aiBusy=false;}
}
function renderAiReview(doc,index,body){
  const review=doc.aiReviews?.[index];
  if(!review)return;
  const box=document.createElement("div");box.className="ai-review-panel";
  const heading=document.createElement("strong");heading.textContent="AI drawing interpretation — requires your review";
  const source=document.createElement("small");source.textContent=
    "Actual "+review.provider+" result · "+review.model+" · "+review.views.length+" candidate view(s) · page "+(index+1);
  const summary=document.createElement("p");summary.textContent=review.page_summary;
  box.append(heading,source,summary);
  for(const v of review.views){
    const row=document.createElement("div");row.className="ai-review-view";
    const title=document.createElement("strong");title.textContent=v.title+" · "+v.state+" · "+v.region;
    const evidence=document.createElement("small");
    evidence.textContent="Visible evidence claimed: "+(v.evidence||"none")+
      (v.source_text_match?" · phrase also appears in PDF text":" · not independently matched to PDF text");
    const decision=document.createElement("div");decision.className="ai-review-actions";
    const status=document.createElement("small");status.textContent=v.review!=="unreviewed"?"Decision: "+v.review:"Not approved or measured";
    decision.append(status);
    if(v.review==="unreviewed"){
      const approve=document.createElement("button");approve.type="button";approve.className="button button-outline compact";
      approve.textContent="Confirm drawing type";
      approve.onclick=()=>{
        const kind=viewTypeForSuggestion(v);
        const matched=pageViews(doc,index).some(x=>x.type===kind);
        if(!matched){
          doc.manualViews??={};doc.manualViews[index]??=[];
          if(doc.manualViews[index].length>=12)return say("Maximum user-added views on this page reached.",true);
          doc.manualViews[index].push({key:newId("ai_view"),title:kind,type:kind,
            source:"ai_reviewed",anchor:null,scaleNote:null,
            evidence:v.evidence,model:v.model,reviewed_at:today()});
        }
        v.review=matched?"confirmed_existing_view":"contractor_confirmed";
        saveSoon();renderPages();
      };
      const reject=document.createElement("button");reject.type="button";reject.className="button button-quiet compact";
      reject.textContent="Reject";reject.onclick=()=>{v.review="rejected";saveSoon();renderPages();};
      decision.append(approve,reject);
    }
    row.append(title,evidence,decision);box.append(row);
  }
  if(review.dimension_labels.length){
    const note=document.createElement("p");note.textContent="Printed labels the model thinks it sees (NOT measurements): "+review.dimension_labels.join(" · ");
    box.append(note);
  }
  if(review.unknowns.length){
    const unknown=document.createElement("p");unknown.textContent="Uncertainties: "+review.unknowns.join("; ");
    box.append(unknown);
  }
  const warning=document.createElement("small");
  warning.textContent="No scale, geometry, quantities, materials, prices or scope are approved by AI analysis.";
  box.append(warning);body.append(box);
}
async function openSheet(docId,index,viewKey=null){
  try{
    const doc=state.documents.find(d=>d.id===docId);
    if(!doc)throw Error("The selected drawing document was not found.");
    const views=pageViews(doc,index);
    const view=views.find(v=>v.key===viewKey)||views[0];
    if(!view)throw Error("This page has no available drawing view.");
    const sheet=ensureSheet(docId,index,view);
    state.docId=docId;state.pageIndex=index;state.sheetId=sheet.id;
    state.viewKey=view.key;state.viewTitle=view.title;state.viewAnchor=view.anchor;
    state.provisionalScaleSheetId=null;state.dimSelected=null;state.pendingRecal=null;
    state.draft=[];state.zoom=pageViews(doc,index).filter(v=>v.anchor).length===2?1.8:1;
    state.focusNextRender=true;state.viewRegion=null;state.viewport=null;state.activeTool="inspect";state.pendingTool=null;
    // Existing/demolition drawings may show both to-remain and removal scope.
    // Never assume everything illustrated is demolished or newly constructed.
    $("measure-layer").value=/^Proposed/.test(view.type)?"new":"field_verify";
    show("measure");await renderPdfPage();refreshMeasureScreen();
    $("measure-work-anchor").scrollIntoView({block:"start",behavior:"instant"});
  }catch(err){say("Unable to open view: "+err.message,true);}
}
function currentSheet(){return state.project?.sheets.find(s=>s.id===state.sheetId)||null}
function currentCalibration(){return state.project?.calibrations.find(c=>c.sheet_id===state.sheetId)||null}
function currentScale(){
  const c=currentCalibration();return c?calibrationStatus(c,state.project.calibration_checks.filter(x=>x.sheet_id===state.sheetId)):"unset";
}
function renderMiniPages(){
  const box=$("mini-pages");box.replaceChildren();
  if(!state.documents.length){const p=document.createElement("p");p.textContent="No PDF needed for field-entered quantities.";box.append(p);return;}
  for(const {doc,index} of allPagePairs()){
    for(const view of pageViews(doc,index)){
      const btn=document.createElement("button");btn.type="button";btn.className="mini-page";
      if(doc.id===state.docId&&index===state.pageIndex&&view.key===state.viewKey)btn.classList.add("active");
      const small=document.createElement("small");small.textContent="P"+(index+1);
      const title=document.createElement("span");title.textContent=view.title;
      btn.title="Page "+(index+1)+" · "+view.title;
      btn.append(small,title);btn.onclick=()=>openSheet(doc.id,index,view.key);box.append(btn);
    }
  }
}
// Derive an approximate focus region from heading coordinates on this PDF.
// It does not crop source pixels, infer scale, or change measured geometry.
function focusRegionForPage(page,viewport){
  const doc=currentDocument();
  if(!doc)return null;
  const views=pageViews(doc,state.pageIndex).filter(v=>v.anchor);
  if(views.length!==2)return null;
  const [left,bottom,right,top]=page.view;
  const markers=views.map(v=>{
    const [x,y]=viewport.convertToViewportPoint(
      left+v.anchor.x*(right-left),bottom+v.anchor.y*(top-bottom));
    return {key:v.key,x:x/viewport.width,y:y/viewport.height};
  });
  const selected=markers.find(v=>v.key===state.viewKey);
  if(!selected)return null;
  const [a,b]=markers;
  if(Math.abs(a.x-b.x)>.24){
    const divider=(a.x+b.x)/2;
    const region=selected.x<divider?{x0:0,x1:divider}:{x0:divider,x1:1};
    return {...region,y0:0,y1:1,axis:"vertical",cx:(region.x0+region.x1)/2,cy:0.5};
  }
  if(Math.abs(a.y-b.y)>.24){
    const divider=(a.y+b.y)/2;
    const region=selected.y<divider?{y0:0,y1:divider}:{y0:divider,y1:1};
    return {...region,x0:0,x1:1,axis:"horizontal",cx:0.5,cy:(region.y0+region.y1)/2};
  }
  return null;
}
async function renderPdfPage(){
  if(!state.docId){$("drawing-empty").hidden=false;$("canvas-wrap").hidden=true;return;}
  const seq=++state.renderSeq;
  snapData=null;
  $("snap-status").textContent=$("vector-snap").checked?"Checking drawing geometry…":"Free click";
  const pdf=await loadPdfDocument(state.docId),page=await pdf.getPage(state.pageIndex+1);
  const holder=$("drawing-holder");
  const old=state.viewport;
  let cx=old?(holder.scrollLeft+holder.clientWidth/2)/old.width:0.5;
  let cy=old?(holder.scrollTop+holder.clientHeight/2)/old.height:0.5;
  const base=page.getViewport({scale:1});
  const scale=Math.max(.2,Math.min(2.25,(holder.clientWidth-25)/base.width*(state.zoom||1)));
  const viewport=page.getViewport({scale});
  const focus=focusRegionForPage(page,viewport);
  state.viewRegion=focus;
  if(state.focusNextRender && focus){cx=focus.cx;cy=focus.cy;}
  const ratio=Math.min(window.devicePixelRatio||1,2);
  const a=$("pdf-canvas"),b=$("overlay-canvas");
  for(const canvas of [a,b]){
    canvas.width=Math.round(viewport.width*ratio);
    canvas.height=Math.round(viewport.height*ratio);
    canvas.style.width=viewport.width+"px";canvas.style.height=viewport.height+"px";
  }
  const ctx=a.getContext("2d",{alpha:false});
  try{
    await page.render({canvasContext:ctx,viewport,transform:ratio===1?null:[ratio,0,0,ratio,0,0]}).promise;
    if(seq!==state.renderSeq)return;
    state.viewport=viewport;
    state.pageAnchors=viewAnchorsDisplay(page,currentDocument(),state.pageIndex);
    $("canvas-wrap").style.width=viewport.width+"px";
    $("canvas-wrap").style.height=viewport.height+"px";
    $("canvas-wrap").hidden=false;$("drawing-empty").hidden=true;
    holder.scrollLeft=Math.max(0,cx*viewport.width-holder.clientWidth/2);
    holder.scrollTop=Math.max(0,cy*viewport.height-holder.clientHeight/2);
    state.focusNextRender=false;
    updateOverlay();updateTool();
    if($("vector-snap").checked){
      const docId=state.docId,pageIndex=state.pageIndex;
      try{
        const result=extractStrokeEndpoints(await page.getOperatorList(),pdfjs.OPS,{view:page.view});
        if(seq===state.renderSeq&&docId===state.docId&&pageIndex===state.pageIndex&&$("vector-snap").checked){
          snapData={...result,docId,pageIndex,seq};
          $("snap-status").textContent=result.points.length?result.points.length+(result.reason==="partial_outer_strokes"?" supported outer endpoints (partial)":" vector endpoints")+" · Shift for free click":"No supported vector endpoints · free click";
        }
      }catch{if(seq===state.renderSeq)$("snap-status").textContent="Vector extraction unavailable · free click";}
    }
  }catch(err){if(seq===state.renderSeq)say("Drawing page could not be shown: "+err.message,true);}
}
function currentPoint(event){
  if(!state.viewport)throw Error("Select a drawing page first.");
  const box=$("overlay-canvas").getBoundingClientRect();
  const x=event.clientX-box.left,y=event.clientY-box.top;
  if(x<0||y<0||x>box.width||y>box.height)throw Error("Outside drawing");
  const [px,py]=state.viewport.convertToPdfPoint(x,y);
  if($("vector-snap").checked&&!event.shiftKey&&["calibrate","check","polygon","line"].includes(state.activeTool)&&snapData?.docId===state.docId&&snapData.pageIndex===state.pageIndex&&snapData.seq===state.renderSeq){
    const hit=chooseSnap(snapData.points,state.viewport,x,y);
    $("snap-status").textContent=hit?"Snapped to vector endpoint · Shift for free click":"No unambiguous endpoint nearby · free click";
    if(hit)return {...hit.point};
  }else if($("vector-snap").checked&&event.shiftKey)$("snap-status").textContent="Free click override";
  return{x:px,y:py};
}
// These values are parsed from the CURRENT uploaded PDF's own scale callout.
function printedScaleForSelectedView(){
  if(!state.docId||state.viewKey==="legacy")return null;
  const doc=currentDocument(),view=pageViews(doc,state.pageIndex).find(x=>x.key===state.viewKey);
  if(!view?.scaleNote||/NOT TO SCALE/i.test(view.scaleNote))return null;
  const label=view.scaleNote.replace(/[“”]/g,'"').replace(/[‘’]/g,"'");
  const m=label.match(/SCALE\s+(\d+(?:\/\d+)?(?:\.\d+)?)\s*"\s*=\s*(\d+)\s*'\s*(?:-?\s*(\d{1,2})\s*")?/i);
  if(!m)return null;
  const paper=m[1].includes("/")?Number(m[1].split("/")[0])/Number(m[1].split("/")[1]):Number(m[1]);
  const real=Number(m[2])+Number(m[3]||0)/12;
  return paper>0&&real>0&&Number.isFinite(paper)&&Number.isFinite(real)?{paper,real,label}:null;
}
function toolHint(){
  const hints={
    inspect:"Choose Measure area, Measure length, or Set scale to begin.",
    calibrate:"Click the two ends of a printed dimension on the drawing. Enter that length above.",
    check:"Click a DIFFERENT printed dimension to verify the scale you set.",
    polygon:"Click the corners of the floor or surface. Select Finish area when done.",
    line:"Click the beginning and end of the wall or trim run.",
    count:"Click a fixture, opening, or item to count it."
  };
  return hints[state.activeTool]||"Choose a measuring tool.";
}
function chooseTool(tool){
  if(["polygon","line","count"].includes(tool)&&!$("measure-assembly").value){
    say("First choose the work item from the menu above the drawing. We will not assume the plan is for floor tile or anything else.",true);
    $("measure-assembly").focus();return;
  }
  if(["polygon","line"].includes(tool)&&state.sheetId&&currentScale()!=="verified"&&
    state.provisionalScaleSheetId!==state.sheetId){
    state.pendingTool=tool;
    if(!currentCalibration()){
      state.activeTool="calibrate";state.scalePhase="initial";
      say("First we must verify drawing scale. You can use the scale printed on the PDF below, then confirm one real dimension.");
    }else{
      state.activeTool="check";state.scalePhase="check";
      say("Scale is not verified yet. Type one labeled dimension from the plan, then click the two ends of that dimension.");
    }
  }else{
    state.pendingTool=null;state.activeTool=tool;
  }
  state.draft=[];

  updateTool();updateOverlay();
  if(["calibrate","check"].includes(state.activeTool))$("scale-guide").scrollIntoView({block:"start",behavior:"instant"});
}
function updateTool(){
  document.querySelectorAll(".measure-tool").forEach(btn=>{
    const selected=btn.dataset.tool===state.activeTool;
    btn.classList.toggle("active",selected);btn.setAttribute("aria-pressed",String(selected));
  });
  const scale=currentScale(),hasSheet=Boolean(state.sheetId);
  const phoneMode=window.matchMedia("(max-width: 600px)").matches;
  const doc=currentDocument();
  const selectedView=hasSheet? pageViews(doc,state.pageIndex).find(v=>v.key===state.viewKey):null;
  const referenceOnly=/NOT TO SCALE/i.test(selectedView?.scaleNote||"")||
    ["General Notes","Reference / Details"].includes(selectedView?.type);
  const revisionBlocked=measuringBlocked();
  for(const button of document.querySelectorAll(".measure-tool"))
    button.disabled=(phoneMode||referenceOnly||revisionBlocked)&&button.dataset.tool!=="inspect";
  $("scale-pill").textContent=scale==="verified"?"✓ Scale verified":scale==="failed"?"Scale check failed":scale==="unverified"?"Scale needs verification":"Scale not set";
  $("scale-pill").classList.toggle("verified",scale==="verified");
  $("zoom-out").disabled=!hasSheet||(state.zoom||1)<=0.75;
  $("zoom-in").disabled=!hasSheet||(state.zoom||1)>=3;
  $("zoom-fit").disabled=!hasSheet;
  $("overlay-canvas").style.cursor=["polygon","line","count","check","calibrate"].includes(state.activeTool)?"crosshair":"grab";
  const calibrationMode=["calibrate","check"].includes(state.activeTool);
  $("scale-guide").hidden=!calibrationMode;
  $("draft-scale-choice").hidden=!calibrationMode||state.activeTool!=="check"||
    !currentCalibration()||currentCalibration().method!=="printed_scale"||
    currentScale()!=="unverified";
  const printed=printedScaleForSelectedView();
  $("printed-scale-option").hidden=!calibrationMode||!printed||state.activeTool==="check";
  if(printed)$("printed-scale-explanation").textContent=
    "Detected on this exact PDF view: "+printed.label+
    ". Applying this as a STARTING point still needs a real dimension check.";
  const hasPrintedDims=calibrationMode&&dimensionCandidatesForView().usable.length>0;
  $("scale-guide-title").textContent=hasPrintedDims
    ?(state.activeTool==="check"
      ?(currentCalibration()?.method==="printed_scale"?"Step 2 of 2 · Verify the printed scale with a printed dimension":"Step 2 of 2 · Verify the scale with a second printed dimension")
      :"Step 1 of 2 · Set the scale from a printed dimension")
    :state.activeTool==="check"
    ?(currentCalibration()?.method==="printed_scale"?"Verify the printed scale with one dimension":"Verify scale with a second dimension")
    :"Choose the printed scale or use a known dimension";
  $("scale-guide-copy").textContent=hasPrintedDims
    ?(state.activeTool==="check"
      ?"Choose a different dimension below, then click both ends of its line. The app compares what you clicked with the printed length."
      :"Choose a dimension printed on this drawing, then click both ends of its line. Prefer a long one — it gives a more accurate scale.")
    :state.activeTool==="check"
    ?"Find any dimension labeled on THIS drawing (for example, 6'-0\"). Type its actual feet/inches below. Then click the two endpoints of that one dimension to check accuracy."
    :"If the printed scale is detected, click the terracotta button below. Otherwise, enter a known printed dimension and click its two endpoints; you will verify a second dimension afterward.";
  $("switch-scale-check").textContent=state.activeTool==="check"?"Start scale again":"Verify another dimension";
  renderDimensionAssist();
  $("finish-area").disabled=!(state.activeTool==="polygon"&&state.draft.length>=3);
  $("drawing-hint").textContent=phoneMode
    ?"Mobile view: inspect pages here, then use a desktop for precise tracing. You can still enter known room dimensions below."
    :(hasSheet?"Page "+(state.pageIndex+1)+": ":"")+
    toolHint()+(state.draft.length?" · "+state.draft.length+" point(s) selected":"")+
    (!hasSheet?" Open a drawing or use the known-quantity form below.":"");
}
function updateOverlay(){
  if(!state.viewport)return;
  const canvas=$("overlay-canvas"),v=state.viewport,ctx=canvas.getContext("2d"),ratio=canvas.width/v.width;
  ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,v.width,v.height);
  function draw(points,kind,color){
    if(!Array.isArray(points)||!points.length)return;
    const coords=points.map(p=>v.convertToViewportPoint(p.x,p.y));
    ctx.beginPath();ctx.moveTo(...coords[0]);
    for(let i=1;i<coords.length;i++)ctx.lineTo(...coords[i]);
    if(kind==="polygon")ctx.closePath();
    if(kind==="polygon"){ctx.globalAlpha=.12;ctx.fillStyle=color;ctx.fill();ctx.globalAlpha=1;}
    ctx.lineWidth=2.7;ctx.strokeStyle=color;ctx.stroke();
    for(const [x,y] of coords){ctx.beginPath();ctx.arc(x,y,4.2,0,2*Math.PI);ctx.fillStyle="#fff";ctx.fill();ctx.lineWidth=2;ctx.strokeStyle=color;ctx.stroke();}
  }
  for(const item of state.project?.measurements||[]){
    if(item.sheet_id!==state.sheetId)continue;
    const color=item.layer==="demolish"?"#dc2626":item.layer==="existing"?"#475569":item.layer==="field_verify"?"#d97706":"#0f766e";
    draw(item.points,item.kind,color);
  }
  draw(state.draft,state.activeTool,"#2563eb");
  const d=state.dimSelected;
  if(d&&d.evidence?.doc_id===state.docId&&d.evidence?.page_index===state.pageIndex){
    const x=d.x*v.width,y=d.y*v.height;
    ctx.beginPath();ctx.arc(x,y,18,0,2*Math.PI);ctx.lineWidth=3;ctx.strokeStyle="#ea580c";ctx.stroke();
    ctx.beginPath();ctx.arc(x,y,26,0,2*Math.PI);ctx.lineWidth=1.5;ctx.setLineDash([4,4]);ctx.stroke();ctx.setLineDash([]);
  }
}
/** View headings converted to display coordinates (rotation applied, y down) for this page. */
function viewAnchorsDisplay(page,doc,index){
  if(!page||!doc)return [];
  const vp=page.getViewport({scale:1});const [l,b,r,t]=page.view;
  return pageViews(doc,index).filter(v=>v.anchor).map(v=>{
    const [dx,dy]=vp.convertToViewportPoint(l+v.anchor.x*(r-l),b+v.anchor.y*(t-b));
    return {key:v.key,x:dx/vp.width,y:dy/vp.height};
  });
}
function selectedView(){
  const doc=currentDocument();
  return state.sheetId&&doc?pageViews(doc,state.pageIndex).find(v=>v.key===state.viewKey)||null:null;
}
function dimensionCandidatesForView(){
  const doc=currentDocument();
  if(!doc||!state.sheetId)return {usable:[],excluded:[],blocked:null};
  const view=selectedView();
  const nts=/NOT TO SCALE|N\.?T\.?S\.?/i.test(view?.scaleNote||"");
  const anchors=state.pageAnchors.length>1?state.pageAnchors:[];
  return calibrationCandidates({dimensions:doc.textScan?.facts?.dimensions||[],docId:doc.id,pageIndex:state.pageIndex,
    viewKey:anchors.length?state.viewKey:null,anchors,nts});
}
/** Source link only when the typed length still equals the chosen printed dimension. */
function sourceDimensionFor(feet){
  const d=state.dimSelected;
  if(!d||Math.abs(d.feet-feet)>1e-9)return null;
  return {id:d.id,text:d.text,label:d.label,feet:d.feet,page_index:d.evidence?.page_index,doc_id:d.evidence?.doc_id};
}
function applyCalibration(c){
  state.project.calibrations=state.project.calibrations.filter(x=>x.sheet_id!==state.sheetId).concat(c);
  state.project.calibration_checks=state.project.calibration_checks.filter(x=>x.sheet_id!==state.sheetId);
  state.activeTool="check";state.dimSelected=null;
  $("scale-check-result").hidden=true;
}
function chooseDimension(c){
  const {feet,inches}=feetToFeetInches(c.feet);
  $("known-feet").value=String(feet);$("known-inches").value=String(inches);
  state.dimSelected=c;state.draft=[];
  if(state.viewport){
    const holder=$("drawing-holder");
    holder.scrollLeft=Math.max(0,c.x*state.viewport.width-holder.clientWidth/2);
    holder.scrollTop=Math.max(0,c.y*state.viewport.height-holder.clientHeight/2);
  }
  updateOverlay();updateTool();renderDimensionAssist();
  say("Click the two ends of the dimension line marked "+c.label+" (circled in orange). The app found the printed number; you confirm where the line starts and stops.");
}
function renderDimensionAssist(){
  const box=$("dimension-assist");if(!box)return;
  box.replaceChildren();
  const mode=state.activeTool;
  const show=["calibrate","check"].includes(mode)&&Boolean(state.sheetId)&&!state.pendingRecal;
  box.hidden=!show;if(!show)return;
  const found=dimensionCandidatesForView();
  const cal=currentCalibration();
  const list=mode==="check"?checkCandidates(found.usable,cal?.source_dimension?.id):found.usable;
  const head=document.createElement("strong");
  head.textContent=mode==="check"?"Check with another printed dimension":"Printed dimensions on this drawing";
  box.append(head);
  const p=document.createElement("p");
  if(found.blocked==="nts_view")p.textContent=EXCLUSION_REASONS.nts_view;
  else if(!list.length)p.textContent=mode==="check"
    ?"No other clear printed dimension was found on this view. Type one you can see below, then click its two ends."
    :"No clear printed dimension was found on this view (scanned plans have no readable text). Type one you can see below, then click its two ends.";
  else p.textContent=mode==="check"
    ?"Pick a different dimension from the one you used. You click its two ends; the app compares the result."
    :"Pick one. You'll click the two ends of its dimension line — the app won't guess where they are.";
  box.append(p);
  if(list.length){
    const row=document.createElement("div");row.className="dimension-choices";
    for(const c of list){
      const b=document.createElement("button");b.type="button";
      b.className="dimension-choice"+(state.dimSelected?.id===c.id?" is-selected":"");
      b.textContent=(mode==="check"?"Check with ":"Use ")+c.label;
      b.setAttribute("aria-pressed",String(state.dimSelected?.id===c.id));
      b.dataset.dimension=c.label;
      b.addEventListener("click",()=>chooseDimension(c));
      row.append(b);
    }
    box.append(row);
  }
  if(found.excluded.length&&!found.blocked){
    const det=document.createElement("details");det.className="dimension-excluded";
    const sum=document.createElement("summary");sum.textContent=found.excluded.length+" printed value(s) not offered — why?";
    const ul=document.createElement("ul");
    for(const e of found.excluded.slice(0,12)){const li=document.createElement("li");li.textContent=e.text+" — "+(EXCLUSION_REASONS[e.reason]||e.reason);ul.append(li);}
    det.append(sum,ul);box.append(det);
  }
}
function showCheckResult(check,feet,source){
  const el=$("scale-check-result");
  const measured=Number(check.measured_ft);
  const diff=Number(check.error_pct);
  el.hidden=false;
  el.className="scale-check-result "+(check.pass&&check.independent?"is-pass":"is-fail");
  el.textContent=(source?"Printed "+source.label:"Expected "+formatFeetInches(feet))+" ("+feet.toFixed(2)+" ft) · you clicked "+
    (Number.isFinite(measured)?measured.toFixed(2)+" ft":"—")+" · difference "+(Number.isFinite(diff)?Math.abs(diff).toFixed(1)+"%":"—")+
    (check.pass&&check.independent?" — within the 1% limit. Scale verified.":check.independent?" — more than 1%. Scale NOT verified.":" — same line as the scale; not an independent check.");
}
function renderRecalImpact(){
  const box=$("recal-impact");if(!box)return;
  box.replaceChildren();
  const p=state.pendingRecal;box.hidden=!p;if(!p)return;
  const h=document.createElement("strong");h.id="recal-title";
  h.textContent="The new scale changes "+p.impact.affected+" measured quantit"+(p.impact.affected===1?"y":"ies")+" on this drawing";
  const intro=document.createElement("p");intro.textContent="Nothing has changed yet. Compare before and after, then choose.";
  const table=document.createElement("table");table.className="recal-table";
  const hr=document.createElement("tr");for(const t of ["Measurement","Before","After","Change"]){const th=document.createElement("th");th.textContent=t;hr.append(th);}
  table.append(hr);
  for(const r of p.impact.rows){
    const tr=document.createElement("tr");
    for(const t of [r.label,r.before===null?"—":r.before.toFixed(2)+" "+r.unit,r.after===null?"—":r.after.toFixed(2)+" "+r.unit,r.change_pct===null?"—":(r.change_pct>0?"+":"")+r.change_pct.toFixed(1)+"%"]){
      const td=document.createElement("td");td.textContent=t;tr.append(td);
    }
    table.append(tr);
  }
  const btns=document.createElement("div");btns.className="recal-buttons";
  const yes=document.createElement("button");yes.type="button";yes.className="button button-terra compact";yes.id="recal-accept";
  yes.textContent="Use the new scale and update these quantities";
  yes.addEventListener("click",()=>{
    const c=state.pendingRecal.calibration;state.pendingRecal=null;
    applyCalibration(c);renderRecalImpact();calculate();saveSoon();updateTool();updateOverlay();
    say("New scale applied and quantities updated. Check it with a second printed dimension.");
  });
  const no=document.createElement("button");no.type="button";no.className="button button-outline compact";no.id="recal-keep";
  no.textContent="Keep the previous scale";
  no.addEventListener("click",()=>{state.pendingRecal=null;renderRecalImpact();updateTool();say("Kept the previous scale. Your quantities did not change.");});
  btns.append(yes,no);
  box.append(h,intro,table,btns);
}
function formParams(){
  const def=assemblyDefs.find(d=>d.id===$("measure-assembly").value),out={};
  if(!def)throw Error("Choose the material or work type first.");
  for(const input of $("assembly-parameters").querySelectorAll("[data-param]")){
    const spec=def.inputs.params[input.dataset.param],val=input.value.trim();
    if(!val){if(def.id==="count_item"&&input.dataset.param==="description"){out.description="Counted item";continue;}if(spec.required)throw Error("Please fill in "+input.dataset.param.replaceAll("_"," "));continue;}
    if(spec.type==="number"||spec.type==="integer"){const n=Number(val);if(!Number.isFinite(n))throw Error("Please enter a valid number");out[input.dataset.param]=n;}
    else if(spec.type==="boolean")out[input.dataset.param]=val==="true";
    else out[input.dataset.param]=val;
  }
  if(def.id==="count_item"&&!out.description)out.description="Counted item";
  return out;
}
function assemblyUnit(){
  const kind=$("manual-kind").value;
  return kind==="line"?"linear feet":kind==="count"?"items":"square feet";
}
function renderManualKindOptions(def){
  const menu=$("manual-kind"),kinds=def?.inputs?.measurement_kinds||[];
  const previous=menu.value;menu.replaceChildren();
  const options=[["polygon","Area (square feet)"],["line","Length (linear feet)"],["count","Count (each)"]];
  for(const [kind,label] of options)if(kinds.includes(kind))menu.add(new Option(label,kind));
  const available=[...menu.options].map(x=>x.value);
  menu.value=available.includes(previous)?previous:available[0]||"polygon";
  $("manual-unit").textContent=assemblyUnit();
}
function selectAssembly(id){
  if([...$("measure-assembly").options].some(o=>o.value===id))$("measure-assembly").value=id;
  renderAssemblyParams();
}
function renderAssemblyPicker(){
  const select=$("measure-assembly"),all=relevant[state.project?.scope||"bath"]||relevant.bath;
  select.replaceChildren();
  select.add(new Option("Choose the work item you are measuring…",""));
  for(const id of all){const def=assemblyDefs.find(x=>x.id===id);if(def)select.add(new Option(def.label,id));}
  select.value="";
  renderAssemblyParams();
}
function renderAssemblyParams(){
  const def=assemblyDefs.find(x=>x.id===$("measure-assembly").value),area=$("assembly-parameters");
  area.replaceChildren();
  if(!def){$("manual-kind").replaceChildren();$("manual-unit").textContent="Choose a work item";return;}
  renderManualKindOptions(def);
  for(const [key,spec] of Object.entries(def.inputs.params)){
    const label=document.createElement("label");label.textContent=key.replaceAll("_"," ")+(spec.unit?" ("+spec.unit+")":"");
    let control;
    if(spec.type==="enum"||spec.type==="boolean"){
      control=document.createElement("select");
      if(spec.type==="enum"){control.add(new Option(spec.required?"Choose one":"Not specified",""));for(const v of spec.options||[])control.add(new Option(v,v));}
      else{control.add(new Option("No","false"));control.add(new Option("Yes","true"));}
    }else{
      control=document.createElement("input");
      control.type=["integer","number"].includes(spec.type)?"number":"text";
      if(control.type==="number")control.step=spec.type==="integer"?"1":"any";
      else control.maxLength=200;
    }
    control.dataset.param=key;control.title=spec.description||"";
    if(spec.default!==null&&spec.default!==undefined)control.value=String(spec.default);
    if(spec.required)control.required=true;
    label.append(control);area.append(label);
  }
}
/** The selected scope item applies only while its work type is still selected. */
function activeScopeFor(assemblyId){
  const item=scopeItems().find(i=>i.id===state.activeScopeItemId&&i.status==="accepted");
  return item&&item.work_type===assemblyId?item.id:null;
}
function recordDrawing(){
  if(!state.sheetId)throw Error("Select a drawing page first.");
  const kind=state.activeTool==="polygon"?"polygon":state.activeTool==="line"?"line":"count";
  const pts=[...state.draft];
  const assemblyId=$("measure-assembly").value;
  const def=assemblyDefs.find(d=>d.id===assemblyId);
  if(!def?.inputs?.measurement_kinds?.includes(kind))throw Error("Choose an assembly compatible with this measurement type.");
  if(kind!=="count"&&!currentCalibration())throw Error("First set scale using a dimension printed on this page.");
  if(kind!=="count"&&currentScale()!=="verified"&&state.provisionalScaleSheetId!==state.sheetId)
    throw Error("Verify drawing scale first, or explicitly continue with an unverified draft scale.");
  const entry={
    id:newId("m"),sheet_id:state.sheetId,kind,points:pts,layer:$("measure-layer").value,
    assembly:{id:assemblyId,params:formParams()},label:def.label,source:"manual",review:"accepted",
    created_at:new Date().toISOString(),updated_at:new Date().toISOString(),
    ...(activeScopeFor(assemblyId)?{scope_item_id:activeScopeFor(assemblyId)}:{})
  };
  validateMeasurement(entry);
  state.project.measurements.push(entry);
  try{calculate();}
  catch(err){state.project.measurements.pop();throw err;}
  state.draft=[];updateOverlay();updateTool();saveSoon();renderScopeTodoPanel();
  toast("Measured quantity added. See your estimate update on the right.");
}
function handleCanvasClick(e){
  if(state.activeTool==="inspect")return;
  try{
    if(!state.sheetId)throw Error("Open a drawing page first.");
    state.draft.push(currentPoint(e));
    if(["calibrate","check"].includes(state.activeTool)&&state.draft.length===2){
      const whole=Number($("known-feet").value),inches=Number($("known-inches").value);
      const feet=whole+inches/12;
      if(!Number.isFinite(whole)||!Number.isFinite(inches)||whole<0||inches<0||inches>=12||feet<=0)throw Error("Enter a dimension from the plan, for example 8 feet, 0 inches.");
      const source=sourceDimensionFor(feet);
      if(state.activeTool==="calibrate"){
        const c=calibrationFromPoints({sheet_id:state.sheetId,p1:state.draft[0],p2:state.draft[1],known_length:feet,known_unit:"ft"});
        if(source)c.source_dimension=source;
        const previous=currentCalibration();
        const onSheet=state.project.measurements.filter(m=>m.sheet_id===state.sheetId&&m.kind!=="count");
        if(previous&&onSheet.length){
          state.pendingRecal={calibration:c,impact:recalibrationImpact(onSheet,previous,c)};
          state.draft=[];state.dimSelected=null;renderRecalImpact();updateTool();updateOverlay();
          say("This drawing already has measurements. Review how the new scale changes them before using it.");
          return;
        }
        applyCalibration(c);
        say("Scale set from "+(source?source.label:"your dimension")+". Now pick a DIFFERENT printed dimension and click its two ends to check the scale.");
      }else{
        const c=currentCalibration();if(!c)throw Error("Set scale before verifying it.");
        const check=checkCalibration(c,{sheet_id:state.sheetId,p1:state.draft[0],p2:state.draft[1],known_length_ft:feet});
        if(source)check.source_dimension=source;
        state.project.calibration_checks.push(check);
        showCheckResult(check,feet,source);
        state.dimSelected=null;
        if(!check.pass||!check.independent)say(check.independent?"That dimension doesn't match the scale. Check you clicked the ends of the right dimension line, or pick another one.":"That was the same line used to set the scale. Pick a different printed dimension to check it.",true);
        else{
          const next=state.pendingTool;state.pendingTool=null;
          state.activeTool=next||"inspect";
          say("✓ Scale verified. "+(next==="polygon"?"Click the corners of the surface. Click Finish area when done.":next==="line"?"Click both ends of the line you want to measure.":"Select Measure area or Measure length to start."));
        }
      }
      state.draft=[];calculate();saveSoon();
    }else if(state.activeTool==="line"&&state.draft.length===2){recordDrawing();}
    else if(state.activeTool==="count"&&state.draft.length===1){recordDrawing();}
  }catch(err){say(err.message,true);state.draft=[];}
  updateTool();updateOverlay();
}
function recordManual(){
  try{
    const quantity=Number($("manual-quantity").value);
    if(!Number.isFinite(quantity)||quantity<=0||quantity>1000000)throw Error("Enter the actual measured quantity, greater than zero.");
    if(assemblyUnit()==="items"&&!Number.isInteger(quantity))throw Error("Enter a whole number for counted items.");
    const id=$("measure-assembly").value,params=formParams();
    const unit=assemblyUnit();
    const measured=unit==="linear feet"?{kinds:["line"],length_ft:quantity,segments_ft:[quantity]}
      :unit==="items"?{kinds:["count"],count:quantity}
      :{kinds:["polygon"],area_sf:quantity}; // Area alone does not establish perimeter or room shape.
    const manual={id:newId("field"),assemblyId:id,quantity,unit,params,measured,layer:$("measure-layer").value,label:assemblyDefs.find(d=>d.id===id).label,source:"field_entered",
      ...(activeScopeFor(id)?{scope_item_id:activeScopeFor(id)}:{})};
    computeAssembly(manual.assemblyId,manual.measured,manual.params,{measurement_ids:[manual.id],layer:$("measure-layer").value});
    state.manuals.push(manual);$("manual-quantity").value="";
    calculate();saveSoon();renderScopeTodoPanel();toast("Known quantity added. Enter a real price to see its cost.");
  }catch(err){say(err.message,true);}
}

function calculate(){
  if(!state.project)return;
  let plan={lines:[],warnings:[],excluded:[]};
  try{
    syncSheetRegister();
    const rf=revisionFilter(state.project.sheet_register,state.project.sheets,state.project.measurements);
    state.revision=rf;
    const active=state.project.measurements.filter(m=>rf.includedIds.has(m.id));
    if(state.project.sheets.length){
      // Views on one page are not revisions of each other: give each view a unique number for
      // the engine; page-level revisions were already applied above by revisionFilter().
      plan=computeTakeoff({
        sheets:state.project.sheets.map(s=>({...s,sheet_number:s.id,supersedes:[],supersession:"whole_sheet"})),
        measurements:active,
        calibrations:state.project.calibrations,calibration_checks:state.project.calibration_checks
      });
      plan.excluded=[...(plan.excluded||[]),...rf.excluded];
      plan.warnings=[...(plan.warnings||[]),...rf.flagged.map(f=>({code:"REVISION_REVIEW",message:f.explanation}))];
    }
  }catch(err){say("Drawing quantity issue: "+err.message,true);}
  const manuallyEntered=[];
  for(const m of state.manuals){
    try{
      const lines=computeAssembly(m.assemblyId,m.measured,m.params,{
        measurement_ids:[m.id],layer:m.layer||"new",sheet_ids:[],flags:["field_entered"]
      });
      manuallyEntered.push(...lines);
    }catch(err){plan.warnings.push({code:"MANUAL_INPUT_INVALID",message:m.label+": "+err.message});}
  }
  state.takeoff={...plan,lines:[...plan.lines,...manuallyEntered]};
  try{
    state.estimate=buildEstimate({
      lines:state.takeoff.lines,selections:state.project.selections,
      catalog:state.project.catalog,quotes:state.project.quotes,settings:{...state.project.settings,labor:effectiveLaborItems(state.project.settings.labor,state.takeoff.lines)},
      asOf:today()
    });
  }catch(err){state.estimate=null;say("Estimate calculations need attention: "+err.message,true);}
  renderRail();
  if(state.activeScreen==="materials")renderMaterials();
  if(state.activeScreen==="review")renderReview();
}
function selectedPrice(line){return state.project.selections.find(x=>x.line_id===line.line_id)||null}
function estimateLine(line){return state.estimate?.lines.find(x=>x.line_id===line.line_id)||null}
function pricedCount(){return(state.estimate?.lines||[]).filter(l=>l.extended_cents!==null&&l.price_status!=="BY_OWNER").length}
function pendingCount(){return state.estimate?.totals?.missing_lines??state.takeoff.lines.length}
function renderRail(){
  const result=state.estimate,total=result?.totals;
  const known=pricedCount()>0||Boolean((total?.labor_cents||0)>0);
  const display=Number.isInteger(total?.grand_total_known_cents)?cash(total.grand_total_known_cents):"—";
  $("measure-cost").textContent=known?display:"—";
  $("measure-pending").textContent=String(pendingCount());
  const box=$("measure-lines");box.replaceChildren();
  if(!state.takeoff.lines.length){
    const p=document.createElement("p");p.textContent="Measure a drawing or enter a known quantity to see materials appear here.";box.append(p);
  }else{
    for(const line of state.takeoff.lines.slice(0,14)){
      const row=document.createElement("div");row.className="rail-line";
      const title=document.createElement("span");title.textContent=line.description;
      const qty=document.createElement("b");qty.textContent=Number(line.qty_with_waste).toLocaleString(undefined,{maximumFractionDigits:2})+" "+line.unit;
      if((line.flags||[]).includes("calibration_unverified")){
        const caveat=document.createElement("small");caveat.className="unverified-qty-warning";
        caveat.textContent="DRAFT — scale not verified";
        row.append(title,qty,caveat);
      }else row.append(title,qty);
      box.append(row);
    }
    if(state.takeoff.lines.length>14){
      const note=document.createElement("p");note.textContent="+"+(state.takeoff.lines.length-14)+" more material lines";box.append(note);
    }
  }
  $("recorded-count").textContent=((state.project?.measurements.length||0)+state.manuals.length)+
     " recorded measurements / field inputs";
  renderRecorded();
}
/** Raw measured value of a traced measurement (SF, LF or count) using its own view's scale. */
function measuredValue(m){
  if(m.kind==="count")return m.points.length;
  const cal=state.project.calibrations.find(c=>c.sheet_id===m.sheet_id);
  if(!cal)return null;
  try{const r=measureGeometry(m,cal);return Math.round((m.kind==="polygon"?r.area_sf:r.length_ft)*100)/100;}catch{return null;}
}
function revisionNote(m){
  const ex=state.revision?.excluded?.find(e=>e.measurement_id===m.id);
  if(ex)return " · "+ex.explanation;
  const fl=state.revision?.flagged?.find(e=>e.measurement_id===m.id);
  return fl?" · "+fl.explanation:"";
}
function measuredText(m){
  const v=measuredValue(m);
  if(v===null)return " · no scale set";
  const unit=m.kind==="polygon"?" SF":m.kind==="count"?" counted":" LF";
  if(m.kind==="count")return " · "+v+unit;
  const cal=state.project.calibrations.find(c=>c.sheet_id===m.sheet_id);
  const status=calibrationStatus(cal,state.project.calibration_checks.filter(x=>x.sheet_id===m.sheet_id));
  return " · "+v.toFixed(2)+unit+(status==="verified"?" · scale verified":" · DRAFT: scale not verified");
}
function renderRecorded(){
  const area=$("recorded-items");area.replaceChildren();
  const items=[
    ...(state.project?.measurements||[]).map(m=>{
      const sheet=state.project.sheets.find(s=>s.id===m.sheet_id);
      return {id:m.id,title:m.label,
        source:"Page "+((sheet?.page_index??-1)+1)+" · "+(sheet?.title||"Unclassified view")+measuredText(m)+revisionNote(m),
        kind:"plan",measured:measuredValue(m),
        counted:state.revision?.excluded?.some(e=>e.measurement_id===m.id)?"no":state.revision?.flagged?.some(e=>e.measurement_id===m.id)?"flagged":"yes"};
    }),
    ...state.manuals.map(m=>({
      id:m.id,title:m.label,source:"Field-entered "+m.quantity+" "+m.unit,kind:"manual"
    }))
  ];
  for(const item of items){
    const row=document.createElement("div");row.className="recorded-item";
    const div=document.createElement("div"),strong=document.createElement("strong"),small=document.createElement("small");
    strong.textContent=item.title;small.textContent=item.source;div.append(strong,small);
    if(item.measured!==undefined&&item.measured!==null)row.dataset.measured=String(item.measured);
    if(item.counted){row.dataset.counted=item.counted;row.classList.toggle("is-not-counted",item.counted==="no");}
    const del=document.createElement("button");del.type="button";del.textContent="Remove";
    del.setAttribute("aria-label","Remove "+item.title);
    del.onclick=()=>{
      if(item.kind==="manual")state.manuals=state.manuals.filter(x=>x.id!==item.id);
      else state.project.measurements=state.project.measurements.filter(x=>x.id!==item.id);
      calculate();updateOverlay();saveSoon();
      toast("Measurement removed and quantities recalculated.");
    };
    row.append(div,del);area.append(row);
  }
  if(!items.length){
    const note=document.createElement("p");note.className="soft-empty";
    note.textContent="After you select a work item and trace an area, line or counted item, the calculated quantity appears here. You do not need to type a separate material list.";area.append(note);
  }
}
function refreshMeasureScreen(){
  const hasPage=Boolean(state.sheetId);
  const doc=currentDocument();
  const view=hasPage?pageViews(doc,state.pageIndex).find(v=>v.key===state.viewKey):null;
  $("selected-view-summary").hidden=!hasPage;
  $("measure-back-to-pages").hidden=!hasPage;
  if(hasPage){
    $("measure-view-label").textContent="Page "+(state.pageIndex+1)+" · "+(view?.title||state.viewTitle||"Drawing")+
      (["General Notes","Reference / Details"].includes(view?.type)
        ?" · Read reference notes, no takeoff required"
        :" · Choose the work you are measuring");
    $("selected-view-title").textContent="Page "+(state.pageIndex+1)+" · "+(view?.title||state.viewTitle||"Selected drawing");
    $("selected-view-note").textContent=/NOT TO SCALE/i.test(view?.scaleNote||"")
      ?"The PDF explicitly marks this detail NOT TO SCALE. Read for construction references; drawing quantities cannot be traced from this not-to-scale detail."
      :["General Notes","Reference / Details"].includes(view?.type)
        ?"This page provides reference notes. Read them for scope and specification requirements; no measuring step is expected here. Choose the appropriate dimensioned plan to take quantities."
        :(view?.scaleNote?"Printed on your PDF: "+view.scaleNote+". This is not automatically calibrated. ":"No confirmed printed scale for this view. ")+
        "Each view has its own scale. Set and verify against a dimension, and choose Existing, Demo or New for each measured item."+(state.viewRegion?" View focus follows the printed heading; use Fit to show the whole page.":"");
  }
  $("measure-caption").textContent=hasPage
    ? "Working on "+(view?.title||state.viewTitle||"drawing")+" from PDF page "+(state.pageIndex+1)+". Measurements from other views stay separate."
    : "No drawing required: choose a material below and enter a dimension you already know.";
  $("drawing-empty").hidden=hasPage;$("canvas-wrap").hidden=!hasPage;
  $("manual-entry-panel").open=!hasPage;
  if(!hasPage)state.viewport=null;
  renderSheetStatus();updateTool();renderRail();renderScopeTodoPanel();
}
function makeMaterialRow(line){
  const estimate=estimateLine(line),selection=selectedPrice(line);
  const row=document.createElement("div");row.className="material-row";
  const summary=document.createElement("div");summary.className="material-title";
  const title=document.createElement("strong");title.textContent=line.description;
  const formula=document.createElement("small");formula.textContent=line.formula||"Recorded quantity";
  summary.append(title,formula);
  const qty=document.createElement("div");qty.className="material-quantity";
  qty.textContent=Number(line.qty_with_waste).toLocaleString(undefined,{maximumFractionDigits:3})+" "+line.unit;
  const status=document.createElement("span");status.className="status-chip";
  status.textContent=estimate?.extended_cents!==null&&estimate?.extended_cents!==undefined?
    (selection?.mode==="allowance"?"Preliminary allowance ":"Priced ")+cash(estimate.extended_cents):
    (estimate?.price_status==="QUOTE_PENDING"?"Pending vendor quote":"Needs a real price");
  if(estimate?.extended_cents!==null&&estimate?.extended_cents!==undefined)status.classList.add("priced");
  qty.append(document.createElement("br"),status);
  const controls=document.createElement("div");controls.className="material-controls";
  const mode=document.createElement("select");mode.setAttribute("aria-label","Price status for "+line.description);
  for(const [val,label] of [
    ["unpriced","Needs price — not $0"],
    ["contractor_rate","Enter unit cost"],
    ["supplier_quote","Waiting on supplier quote"],
    ["allowance","Documented allowance"],
    ["by_owner","Supplied by homeowner"]
  ])mode.add(new Option(label,val));
  mode.value=selection?.mode||"unpriced";
  const rateLabel=document.createElement("label");
  rateLabel.textContent=mode.value==="allowance"?"Total allowance (USD)":"Your entered unit cost / "+line.unit+" (USD)";
  const rate=document.createElement("input");rate.type="number";rate.min=".01";rate.step=".01";
  rate.placeholder=mode.value==="allowance"?"Total allowance ($)":"e.g. 4.25";
  rate.setAttribute("aria-label","Your entered price for "+line.description);
  if(selection?.mode==="contractor_rate"&&Number.isInteger(selection.rate_cents_per_unit))
    rate.value=String(selection.rate_cents_per_unit/100);
  if(selection?.mode==="allowance"&&Number.isInteger(selection.allowance_cents))
    rate.value=String(selection.allowance_cents/100);
  rate.hidden=!["contractor_rate","allowance"].includes(mode.value);
  rateLabel.hidden=rate.hidden;
  const source=document.createElement("input");source.type="text";source.maxLength=180;
  source.className="price-source-input";
  source.placeholder="Price source / quote date (for final bid review)";
  source.setAttribute("aria-label","Price source for "+line.description);
  source.value=selection?.mode==="contractor_rate" && selection.source_ref &&
    !selection.source_ref.startsWith("Contractor-entered")?selection.source_ref:"";
  source.hidden=mode.value!=="contractor_rate";
  function commit(kind="price"){
    try{
      const metadata={operation_id:operation.value.trim()||null,cost_inclusion:inclusion.value||null};
      // Metadata edits must not renew a stale quantity basis or replace source/quote provenance.
      if(kind==="metadata"||kind==="source"){
        const previous=selectedPrice(line);
        const sel={...previous,line_id:line.line_id,mode:previous?.mode||"unpriced",...metadata};
        if(kind==="source"&&sel.mode==="contractor_rate")sel.source_ref=source.value.trim()||"Contractor-entered; source not independently verified.";
        state.project.selections=state.project.selections.filter(x=>x.line_id!==line.line_id).concat(sel);
        calculate();saveSoon();
        return;
      }
      const sel={line_id:line.line_id,mode:mode.value,...metadata};
      if(mode.value==="contractor_rate"){
        sel.rate_unit=line.unit;
        sel.source_ref=source.value.trim() || "Contractor-entered; source not independently verified.";
        if(rate.value.trim())sel.rate_cents_per_unit=toCents(rate.value);
      }else if(mode.value==="allowance"){
        sel.source_ref="Contractor-entered proposed allowance; requires client documentation.";
        sel.allowance_basis="quantity_bound";
        if(rate.value.trim()){sel.allowance_cents=toCents(rate.value);sel.quantity_basis=lineBasis(line);}
      }
      state.project.selections=state.project.selections.filter(x=>x.line_id!==line.line_id).concat(sel);
      calculate();saveSoon();
      toast("Pricing updated. Missing costs remain clearly flagged.");
    }catch(err){say(err.message,true);}
  }
  mode.onchange=()=>{rate.value="";source.value="";commit();};
  rate.onchange=()=>commit("price");
  source.onchange=()=>commit("source");
  const operation=document.createElement("input");operation.placeholder="Operation ID (same as related labor)";operation.maxLength=80;operation.value=selection?.operation_id||"";operation.setAttribute("aria-label","Operation ID for "+line.description);
  const inclusion=document.createElement("select");inclusion.setAttribute("aria-label","Cost inclusion for "+line.description);
  for(const [v,l] of [["","Cost inclusion unreviewed"],["supply_only","Materials only"],["installed","Supply and installation"],["labor_only","Labor only"],["other_nonlabor","Other nonlabor cost"]])inclusion.add(new Option(l,v));
  inclusion.value=selection?.cost_inclusion||"";
  operation.onchange=()=>commit("metadata");inclusion.onchange=()=>commit("metadata");
  controls.append(mode,rateLabel,rate,source,operation,inclusion);
  row.append(summary,qty,controls);
  const products=(state.project.retail_observations||[]).filter(x=>x.assembly_hint===line.assembly_id);
  if(products.length){
    const refs=document.createElement("details");refs.className="retail-line-matches";
    const heading=document.createElement("summary");
    heading.textContent=products.length+" sourced product option(s) to compare · not auto-selected";
    refs.append(heading);
    for(const obs of products){
      const offer=document.createElement("div");offer.className="retail-offer";
      const name=document.createElement("strong");name.textContent=obs.product_name;
      const detail=document.createElement("small");
      detail.textContent=obs.supplier+" · "+cash(Math.round(obs.observed_price_usd*100))+
        " / "+obs.package_qty+" "+obs.package_unit+" · observed "+obs.observed_at+
        " · local checkout price not confirmed";
      const link=document.createElement("a");
      link.href=obs.source_url;link.target="_blank";link.rel="noopener noreferrer";
      link.textContent="View retailer source";
      offer.append(name,detail,link);
      const reference=wholePackageObservationCost(line,obs);
      if(reference){
        const minimum=document.createElement("p");
        minimum.textContent="Minimum "+reference.packages+" whole package(s) × "+
          cash(Math.round(obs.observed_price_usd*100))+" = "+
          cash(reference.amount_cents)+" · excludes extra system materials";
        const allow=document.createElement("button");allow.type="button";
        allow.className="button button-outline compact";
        allow.textContent="Record "+cash(reference.amount_cents)+" as PRELIMINARY allowance";
        allow.onclick=()=>{
          const s={line_id:line.line_id,mode:"allowance",
            allowance_cents:reference.amount_cents,allowance_basis:"quantity_bound",quantity_basis:lineBasis(line),
            source_ref:"PRELIMINARY unconfirmed retail observation "+obs.observed_at+
              " · "+obs.supplier+" · "+obs.source_url};
          state.project.selections=state.project.selections.filter(x=>x.line_id!==line.line_id).concat(s);
          calculate();saveSoon();renderMaterials();
          say("Recorded a planning allowance only. Confirm product system, quantities, store pricing, waste, fasteners, and labor before fixed-price use.");
        };
        offer.append(minimum,allow);
      }else{
        const warning=document.createElement("p");warning.textContent="Package units do not match measured "+
          line.unit+". Confirm manufacturer coverage before using this price.";
        offer.append(warning);
      }
      refs.append(offer);
    }
    row.append(refs);
  }
  return row;
}
function renderMaterials(){
  const obs=state.project.retail_observations||[];
  $("pricebook-summary").textContent=obs.length
    ?obs.length+" dated supplier price observations loaded. These do not prove Prescott checkout prices or product suitability."
    :"No price observations imported. You may still enter a verified contractor price or leave a vendor quote pending.";
  const area=$("materials-list");area.replaceChildren();
  const lines=state.takeoff.lines;
  $("stat-lines").textContent=String(lines.length);
  $("stat-priced").textContent=String(pricedCount());
  $("stat-pending").textContent=String(pendingCount());
  if(!lines.length){
    const p=document.createElement("p");p.className="soft-empty";
    p.textContent="No measurements yet. Go back to Measure, trace an area, or type a known quantity to begin.";area.append(p);return;
  }
  for(const line of lines)area.append(makeMaterialRow(line));
}
function renderLabor(){
  const area=$("labor-list");area.replaceChildren();
  $("tax-rate").value=state.project.settings.tax_rate_pct??"";
  $("shipping").value=Number.isInteger(state.project.settings.shipping_cents)?String(state.project.settings.shipping_cents/100):"";
  $("markup").value=state.project.settings.markup_pct??0;
  $("contingency").value=state.project.settings.contingency_pct??0;
  $("labor-reviewed").checked=state.project.labor_scope_reviewed===true;
  renderLaborActivityInput();
  for(const task of state.project.settings.labor||[]){
    const row=document.createElement("div");row.className="labor-row";
    const label=document.createElement("div");
    const strong=document.createElement("strong");strong.textContent=task.description;
    const detail=document.createElement("small");
    const effective=effectiveLaborItems([task],state.takeoff.lines)[0];
    detail.textContent=task.activity_basis
      ? effective.activity_stale?" · Quantity or direct cost unresolved — remove and re-review activity":" · "+effective.hours+" paid person-hours · "+task.activity_basis.active_workdays+" active workdays · "+cash(effective.amount_cents)+" direct cost"
      : " · "+task.hours+" hr × "+cash(task.rate_cents_per_hour)+" = "+cash(Math.round(task.hours*task.rate_cents_per_hour));
    label.append(strong,detail);
    const button=document.createElement("button");button.textContent="Remove";button.type="button";
    button.onclick=()=>{
      state.project.settings.labor=state.project.settings.labor.filter(x=>x.id!==task.id);
      state.project.labor_scope_reviewed=false;
      calculate();renderLabor();saveSoon();
    };
    const operation=document.createElement("input");operation.placeholder="Operation ID (match material or installed cost)";operation.maxLength=80;operation.value=task.operation_id||"";operation.setAttribute("aria-label","Operation ID for labor "+task.description);
    operation.onchange=()=>{task.operation_id=operation.value.trim()||null;state.project.labor_scope_reviewed=false;calculate();saveSoon();};
    row.append(label,operation,button);area.append(row);
  }
  const markup=Number($("markup").value);
  const gross=markup/(100+markup)*100;
  $("margin-note").textContent=Number.isFinite(gross)
   ?markup.toFixed(1)+"% markup on cost = "+gross.toFixed(1)+"% gross margin before overhead and other expenses."
   :"Enter a valid markup percentage.";
}
function renderLaborActivityInput(){
  const picker=$("activity-line"),previous=picker.value;
  picker.replaceChildren(new Option("Select measured activity basis",""));
  for(const l of state.takeoff.lines)if(Number.isFinite(l.qty)&&l.qty>0)picker.add(new Option(l.description+" · "+l.qty+" "+l.unit+" net",l.line_id));
  if([...picker.options].some(o=>o.value===previous))picker.value=previous;
  const l=state.takeoff.lines.find(x=>x.line_id===picker.value);
  $("activity-unit").textContent=l?"Person-hours per "+l.unit:"Person-hours per selected unit";
}
function addReviewedActivity(){
  try{
    const l=state.takeoff.lines.find(x=>x.line_id===$("activity-line").value);
    const value=id=>$(id).value.trim(),n=id=>value(id)===""?NaN:Number(value(id));
    const rate=value("activity-cost")===""?null:{cents:toCents(value("activity-cost")),basis:$("activity-rate-basis").value};
    const crew={people:n("activity-crew"),paid_hours_per_day:n("activity-paid"),productive_hours_per_day:n("activity-productive"),billing_policy:"prorated",cost_rate:rate};
    const row=createLaborActivity(l,{id:newId("labor"),description:value("activity-name"),operation_id:value("activity-operation"),evidence:value("activity-evidence"),reviewed:$("activity-reviewed").checked,person_hours_per_unit:n("activity-productivity"),crew,crew_efficiency:n("activity-efficiency")/100,max_workers:n("activity-capacity")});
    if(state.project.settings.labor.some(x=>x.operation_id===row.operation_id&&x.activity_basis?.line_id===row.activity_basis.line_id))throw Error("Activity already linked to this operation and source. Remove the old one before replacing it.");
    state.project.settings.labor.push(row);state.project.labor_scope_reviewed=false;
    $("activity-reviewed").checked=false;calculate();renderLabor();saveSoon();
    toast("Reviewed activity added. Direct cost only; selling rate is not marked up again.");
  }catch(e){say(e.message,true);}
}
function addLabor(){
  try{
    const desc=$("labor-name").value.trim(),rawHours=$("labor-hours").value.trim();
    const hours=Number(rawHours),rate=toCents($("labor-rate").value);
    if(!desc)throw Error("Name the labor task before adding it.");
    if(!/^\d+(\.\d{1,2})?$/.test(rawHours)||hours<=0||hours>100000)throw Error("Enter positive hours (up to two decimals).");
    state.project.settings.labor.push({id:newId("labor"),description:desc,hours,rate_cents_per_hour:rate});
    state.project.labor_scope_reviewed=false;
    $("labor-name").value="";$("labor-hours").value="";$("labor-rate").value="";
    calculate();renderLabor();saveSoon();
    toast("Labor cost included in the estimate.");
  }catch(err){say(err.message,true);}
}
function saveSettings(){
  try{
    const readPct=id=>{
      const value=$(id).value.trim();
      if(value==="")return id==="tax-rate"?null:0;
      const v=Number(value);
      if(!Number.isFinite(v)||v<0||v>1000||(id==="tax-rate"&&v>100))throw Error("Enter a valid "+id.replace("-"," ")+" percentage.");
      return v;
    };
    state.project.settings.tax_rate_pct=readPct("tax-rate");
    state.project.settings.markup_pct=readPct("markup");
    state.project.settings.contingency_pct=readPct("contingency");
    state.project.settings.shipping_cents=$("shipping").value.trim()===""?null:toCents($("shipping").value,{zero:true});
    calculate();renderLabor();saveSoon();
  }catch(err){say("Pricing setting rejected: "+err.message,true);}
}
function selectedScopeChoices(){
  return scopePrompts[state.project.scope]||scopePrompts.bath;
}
function scopeOpen(){
  return selectedScopeChoices().filter((t,i)=>{
    const s=state.scopeChoices[i];
    return !s||!["included","excluded","allowance","na"].includes(s.status)||
       (["excluded","allowance"].includes(s.status)&&(!s.note||s.note.trim().length<4));
  });
}
function assuranceReview(){
  const fieldIds=new Set(state.manuals.map(m=>m.id));
  const excludedIds=new Set((state.takeoff.excluded||[]).map(e=>e.measurement_id));
  const measurements=allMeasuredForScope().map(m=>({...m,field_entered:fieldIds.has(m.id),counted:excludedIds.has(m.id)?"no":m.counted}));
  return reconcileEstimate({scopeItems:state.project.scope_items||[],measurements,takeoff:state.takeoff,estimate:state.estimate});
}
function reviewBlockers(){
  const blockers=[];
  for(const f of assuranceReview().findings.filter(f=>f.severity==="block")){
    const scope=state.project.scope_items?.find(s=>s.id===f.scope_id);
    const refs=[...f.measurement_ids,...f.line_ids].join(", ");
    blockers.push("Estimate assurance: "+(ASSURANCE_LABELS[f.code]||f.code)+(scope?" — "+scope.title:"")+(refs?" ("+refs+")":""));
  }
  if(!state.takeoff.lines.length)blockers.push("Add at least one measured or field-entered quantity.");
  if(pendingCount())blockers.push(pendingCount()+" material line(s) need a confirmed rate, supplier quote, allowance or explicit exclusion.");
  const missingRateSources=state.project.selections.filter(s=>
    s.mode==="contractor_rate" && (!s.source_ref || s.source_ref.startsWith("Contractor-entered") || s.source_ref.trim().length<4));
  if(missingRateSources.length)blockers.push(missingRateSources.length+" entered material rate(s) need a supplier, receipt or dated price-book source before final bid review.");
  const excludedIds=new Set(state.takeoff.excluded.map(e=>e.measurement_id));
  const measuredSheets=new Set(state.project.measurements.filter(m=>m.kind!=="count"&&!excludedIds.has(m.id)).map(m=>m.sheet_id));
  const unverified=[...measuredSheets].filter(id=>{
    const cal=state.project.calibrations.find(c=>c.sheet_id===id);
    return !cal||calibrationStatus(cal,state.project.calibration_checks.filter(x=>x.sheet_id===id))!=="verified";
  });
  if(unverified.length)blockers.push(unverified.length+" measured drawing(s) still need an independent scale check.");
  for(const item of currentQuantityReviews())if(item.type==="revision"&&!reviewedQuantity(item,state.project.quantity_reviews))blockers.push("Excluded measurement needs review: "+item.label+" — "+item.description);
  if(state.takeoff.warnings.length)blockers.push(state.takeoff.warnings.length+" quantity warning(s) need investigation.");
  if(state.estimate?.errors?.length)blockers.push(state.estimate.errors.length+" pricing or unit error(s) require correction.");
  if(state.project.labor_scope_reviewed!==true)blockers.push("Review labor and subcontractor coverage.");
  if(state.project.settings.tax_rate_pct===null)blockers.push("Confirm the tax rate (0% only if genuinely applicable).");
  if(state.project.settings.shipping_cents===null)blockers.push("Confirm shipping or delivery charges (0 only if none applies).");
  const coverage=scopeCoverage({titles:selectedScopeChoices(),choices:state.scopeChoices,estimate:state.estimate});
  blockers.push(...coverage.issues);
  for(const f of checkOperationCoverage(state.estimate))blockers.push("Cost ownership: "+f.code+" ("+f.ids.join(", ")+")");
  for(const code of checkEstimateTotals(state.estimate))blockers.push("Estimate total reconciliation: "+code);
  for(const s of state.project.selections.filter(s=>s.mode==="allowance"&&!s.quantity_basis))blockers.push("Legacy allowance requires re-entry against current quantity: "+s.line_id);
  if(state.estimate?.totals?.missing_labor_items)blockers.push("Labor cost missing for "+state.estimate.totals.missing_labor_items+" task(s).");
  for(const item of currentQuantityReviews())if(item.type==="field"&&!reviewedQuantity(item,state.project.quantity_reviews))blockers.push("Quantity from field entry needs documented verification: "+item.label);
  return blockers;
}
function currentQuantityReviews(){
  return quantityReviewItems({manuals:state.manuals,excluded:state.takeoff.excluded,register:state.project.sheet_register,measurements:state.project.measurements});
}
function renderQuantityReviews(){
  const area=$("quantity-review-list");area.replaceChildren();
  for(const item of currentQuantityReviews()){
    const row=document.createElement("div");row.className="scope-cost-links";
    const h=document.createElement("strong");h.textContent=item.label+" — "+item.description;row.append(h);
    if(!item.acknowledgeable){const p=document.createElement("p");p.textContent="Resolve the drawing conflict or missing evidence; acknowledgement cannot clear this issue.";row.append(p);}
    else{
      const current=state.project.quantity_reviews?.[item.key];
      const note=document.createElement("input");note.placeholder=item.type==="field"?"Site measurement method, date, and who verified":"Replacement drawing and reviewed scope";note.maxLength=500;note.value=current?.note||"";
      const button=document.createElement("button");button.type="button";button.className="button button-outline compact";button.textContent=reviewedQuantity(item,state.project.quantity_reviews)?"Reconfirm reviewed quantity":"Confirm with evidence";
      button.onclick=()=>{if(note.value.trim().length<8)return say("Enter specific verification evidence first.",true);state.project.quantity_reviews={...state.project.quantity_reviews,[item.key]:{basis:item.basis,note:note.value.trim(),reviewed_on:today()}};saveSoon();renderReview();};
      row.append(note,button);
    }
    area.append(row);
  }
  if(!area.children.length)area.textContent="No field entries or excluded measurements.";
}
function renderReview(){
  const blockers=reviewBlockers(),region=$("review-blockers");region.replaceChildren();
  const status=$("review-state");
  status.textContent=blockers.length?"Needs "+blockers.length+" review item(s)":"Tracked checks complete";
  status.classList.toggle("ready",blockers.length===0);
  if(!blockers.length){
    const row=document.createElement("div");row.className="blocker ready";
    row.innerHTML="<b>✓</b><span>Tracked pricing and scope checks passed. A contractor must still review site conditions, trade quotes, and proposal terms.</span>";
    region.append(row);
  }else for(const item of blockers){
    const row=document.createElement("div");row.className="blocker";
    const icon=document.createElement("b");icon.textContent="!";
    const text=document.createElement("span");text.textContent=item;
    row.append(icon,text);region.append(row);
  }
  $("review-total").textContent=pricedCount()||state.project.settings.labor.length
    ? cash(state.estimate?.totals?.grand_total_known_cents):"—";
  const breakdown=$("review-breakdown");breakdown.replaceChildren();
  for(const [name,value] of [
    ["Material costs confirmed",cash(state.estimate?.totals?.confirmed_cents||0)],
    ["Labor",cash(state.estimate?.totals?.labor_cents||0)],
    ["Allowances",cash(state.estimate?.totals?.allowance_cents||0)],
    ["Unpriced items",String(pendingCount())],
    ["Still to review",String(blockers.length)]
  ]){const row=document.createElement("div");row.textContent=name+" · "+value;breakdown.append(row);}
  renderQuantityReviews();
  const scope=$("scope-list");scope.replaceChildren();
  for(const [i,title] of selectedScopeChoices().entries()){
    const current=state.scopeChoices[i]||{};
    const row=document.createElement("div");row.className="scope-row";
    const strong=document.createElement("strong");strong.textContent=title;
    const choice=document.createElement("select");choice.setAttribute("aria-label","Scope decision: "+title);
    for(const [value,label] of [["","Not reviewed"],["included","Included"],["allowance","Allowance"],["excluded","Excluded"],["na","Not applicable"],["pending","Still pending"]])choice.add(new Option(label,value));
    choice.value=current.status||"";
    const note=document.createElement("input");note.placeholder="Reason for exclusion/allowance (optional otherwise)";note.maxLength=220;
    note.setAttribute("aria-label","Evidence for "+title);note.value=current.note||"";
    function commit(extra={}){
      state.scopeChoices[i]={...state.scopeChoices[i],status:choice.value,note:note.value.trim(),...extra};
      saveSoon();
    }
    choice.onchange=()=>{commit({coverage_basis:null,line_ids:[],labor_ids:[],labor_disposition:null,labor_note:""});renderReview();};
    note.onchange=()=>{commit({coverage_basis:null});renderReview();};
    row.append(strong,choice,note);
    if(["included","allowance"].includes(choice.value)){
      const links=document.createElement("details");links.className="scope-cost-links";
      const h=document.createElement("summary");h.textContent="Link priced work and labor ("+(current.line_ids?.length||0)+" material / "+(current.labor_ids?.length||0)+" labor)";links.append(h);
      const hint=document.createElement("small");hint.textContent="Select all relevant rows. Confirm each category against the current estimate; links alone do not prove full scope.";links.append(hint);
      function picker(label,items,ids,key){
        const group=document.createElement("fieldset"),legend=document.createElement("legend");legend.textContent=label;group.append(legend);
        for(const item of items){
          const tag=document.createElement("label"),check=document.createElement("input");check.type="checkbox";check.value=item.id;check.checked=(ids||[]).includes(item.id);
          check.setAttribute("aria-label",title+" — "+item.label);
          check.onchange=()=>{const next=Array.from(group.querySelectorAll("input:checked")).map(x=>x.value);commit({[key]:next,coverage_basis:null});status.textContent="Coverage changed — reconfirm";status.classList.remove("ready");};
          tag.append(check,document.createTextNode(item.label));group.append(tag);
        }
        links.append(group);
      }
      picker("Material and quote rows",(state.estimate?.lines||[]).map(l=>({id:l.line_id,label:l.description+" · "+l.price_status+" · "+cash(l.extended_cents)})),current.line_ids,"line_ids");
      picker("Labor rows",(state.estimate?.labor||[]).map(l=>({id:l.id,label:l.description+" · "+cash(l.amount_cents)})),current.labor_ids,"labor_ids");
      const disposition=document.createElement("select");disposition.setAttribute("aria-label","Labor disposition: "+title);
      for(const [v,l] of [["","Separate linked labor"],["included_in_quote","Labor included in approved quote"],["owner_or_external","Owner/external responsibility"],["not_required","No labor required"]])disposition.add(new Option(l,v));
      disposition.value=current.labor_disposition||"";
      const reason=document.createElement("input");reason.placeholder="Evidence for labor exception";reason.value=current.labor_note||"";reason.maxLength=220;
      disposition.onchange=()=>{commit({labor_disposition:disposition.value,coverage_basis:null});status.classList.remove("ready");status.textContent="Coverage changed — reconfirm";};
      reason.onchange=()=>{commit({labor_note:reason.value.trim(),coverage_basis:null});status.classList.remove("ready");status.textContent="Coverage changed — reconfirm";};
      const confirm=document.createElement("button");confirm.type="button";confirm.textContent="Confirm current category coverage";confirm.className="button button-outline compact";
      confirm.onclick=()=>{commit({labor_disposition:disposition.value,labor_note:reason.value.trim(),coverage_basis:scopeCoverage({estimate:state.estimate}).basis});renderReview();};
      links.append(disposition,reason,confirm);row.append(links);
    }
    scope.append(row);
  }
}
function renderProposal(){
  const blockers=reviewBlockers();
  const ready=!blockers.length;
  $("proposal-date").textContent=today();
  $("proposal-title").textContent=projectName();
  $("proposal-label").classList.toggle("ready",ready);
  $("proposal-label").textContent=ready?"INTERNAL REVIEW — CONTRACTOR APPROVAL REQUIRED":"PRELIMINARY — NOT A FIXED PRICE — INTERNAL REVIEW ONLY";
  const body=$("proposal-scope");body.replaceChildren();
  const heading=document.createElement("h2");heading.textContent="Project scope and measured items";body.append(heading);
  const lines=document.createElement("ul");
  for(const item of state.takeoff.lines){
    const li=document.createElement("li");
    const p=estimateLine(item);
    const label=p?.price_status==="BY_OWNER"?"OWNER SUPPLIED — installation separate":p?.price_status==="ALLOWANCE"?"ALLOWANCE (not firm) "+cash(p.extended_cents):p?.price_status==="QUOTE_APPROVED"&&p.quote_allocation==="included"?"Included in approved quote":p?.extended_cents==null?"UNPRICED":p.price_status+" "+cash(p.extended_cents);
    li.textContent=item.description+" — "+Number(item.qty_with_waste).toLocaleString(undefined,{maximumFractionDigits:2})+" "+item.unit+" — "+label;
    lines.append(li);
  }
  body.append(lines);
  const total=$("proposal-amount");total.replaceChildren();
  const left=document.createElement("span");left.textContent=ready?"Known estimate total (review before sending)":"Subtotal of known amounts only";
  const value=document.createElement("strong");
  value.textContent=cash(state.estimate?.totals?.grand_total_known_cents);
  total.append(left,value);
  const costs=document.createElement("p");costs.textContent="Confirmed material/quote costs: "+cash(state.estimate?.totals?.confirmed_cents)+" · Allowances: "+cash(state.estimate?.totals?.allowance_cents)+" · Provisional: "+cash(state.estimate?.totals?.provisional_cents)+" · Labor: "+cash(state.estimate?.totals?.labor_cents);body.append(costs);
  const conditions=$("proposal-conditions");conditions.replaceChildren();
  const title=document.createElement("h2");title.textContent="Important terms and unresolved items";conditions.append(title);
  const all=document.createElement("ul");
  if(blockers.length){const li=document.createElement("li");li.textContent="Preliminary only: contractor must confirm outstanding scope, measurements, supplier prices and trade costs before issuing a formal quotation.";all.append(li);}
  for(const issue of blockers){const li=document.createElement("li");li.textContent="Unresolved: "+issue;all.append(li);}
  for(const item of currentQuantityReviews())if(reviewedQuantity(item,state.project.quantity_reviews)){const li=document.createElement("li");li.textContent="Contractor-recorded review: "+item.label+" — "+state.project.quantity_reviews[item.key].note;all.append(li);}
  for(const [i,t] of selectedScopeChoices().entries()){
    const s=state.scopeChoices[i];
    if(s&&["excluded","allowance"].includes(s.status)){
      const li=document.createElement("li");li.textContent=(s.status==="excluded"?"Excluded: ":"Allowance: ")+t+" — "+s.note;all.append(li);
    }
  }
  conditions.append(all);
  if(!blockers.length){
    const p=document.createElement("p");
    p.textContent="This is a print preview only. Confirm all project specifications, client details, payment terms, exclusions and licensing information before issuing a formal signed proposal.";
    conditions.append(p);
  }
}
function downloadText(filename,text,mime="application/json"){
  const url=URL.createObjectURL(new Blob([text],{type:mime}));
  const a=document.createElement("a");a.href=url;a.download=filename;a.style.display="none";
  document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1500);
}
async function exportPilotFeedback(){
  if(!state.project)return say("Open an estimate before exporting feedback.",true);
  try{
    const comment=$("pilot-feedback-comment").value.trim();
    const label=$("pilot-feedback-reviewer").value.trim();
    const kind=$("pilot-feedback-kind").value;
    if(comment.length<5||comment.length>4000)throw Error("Add at least 5 characters (up to 4,000) about what you noticed.");
    if(label.length>80)throw Error("Reviewer label must be 80 characters or fewer.");
    const fb=createFeedback({
      project_id:state.project.id,target:{type:"general",id:null},
      kind,comment,reviewer_label:label||null,
    },{project:state.project});
    const pkg=await exportFeedbackPackage(state.project,[fb],{
      include_project:false,estimate:null,
    });
    downloadText("measuredfield-pilot-feedback-"+today()+".json",JSON.stringify(pkg,null,2));
    toast("Feedback downloaded. Send the JSON file to the team; nothing was uploaded.");
  }catch(err){say("Could not export feedback: "+err.message,true);}
}
function exportChatGptReviewPacket(){
  if(!state.project)return;
  try{
    // Refresh the deterministic app engine before freezing the read-only packet.
    calculate();
    const packet=buildReviewPacket({
      project:state.project,documents:state.documents,manuals:state.manuals,
      takeoff:state.takeoff,estimate:state.estimate,revision:state.revision,asOf:today()
    });
    downloadText("measuredfield-takeoff-review-"+today()+".json",JSON.stringify(packet,null,2));
    toast("Review packet downloaded. Attach it to ChatGPT when ready; no files were uploaded.");
  }catch(err){say("Could not prepare review packet: "+err.message,true);}
}
async function backup(){
  if(!state.project)return;
  try{
    // Snapshot first so asynchronous file reads cannot mix project versions.
    const record=structuredClone({id:state.project.id,project:JSON.parse(serializeProject(state.project)),documents:state.documents,manuals:state.manuals,scopeChoices:state.scopeChoices,updatedAt:Date.now()});
    const pdfs=await Promise.all(record.documents.map(d=>getPdf(d.id)));
    const scope=document.querySelector('meta[name=mf-takeoff-storage-scope]')?.content||null;
    const blob=await createBackup(record,pdfs,{scope});
    const url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="measuredfield-private-backup-"+today()+".mfbackup";document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1500);
    toast("Private, unencrypted backup exported with original PDFs. Keep it secure.");
  }catch(e){say("Backup failed: "+e.message,true);}
}
async function restoreBackup(file){
  if(!file)return;
  try{
    const scope=document.querySelector('meta[name=mf-takeoff-storage-scope]')?.content||null;
    const {record,pdfs}=await readBackup(file,{scope});
    if(state.project?.id===record.id)throw Error("Restore cannot replace the active project");
    await restoreProjectBundle(record,pdfs);
    // Do not navigate or replace active edits after asynchronous validation.
    if(state.activeScreen==="home")await renderProjects();
    toast("Backup restored locally with original PDFs. Open it from Projects; your current work is unchanged.");
  }catch(e){say("Restore refused: "+e.message,true);}
}

/* Wire the entire guided user journey. Every visible control has an action. */
const on=(id,fn,event="click")=>$(id).addEventListener(event,fn);
on("new-estimate",()=>show("new"));
on("new-estimate-small",()=>show("new"));
on("back-projects",()=>show("home"));
on("nav-home",()=>show("home"));
on("nav-plan",()=>state.project?show("plans"):show("new"));
on("nav-pricing",()=>state.project?show("materials"):show("new"));
on("start-plans",()=>createNew("plans"));
on("start-dimensions",()=>{
  createNew("dimensions");
  toast("Start with the dimension you know. Choose a material, type an area, then enter a price.");
});
for(const btn of document.querySelectorAll(".spine button"))
  btn.addEventListener("click",()=>show(btn.dataset.step));
on("export-project",backup);
on("restore-project",e=>{const file=e.target.files?.[0];e.target.value="";restoreBackup(file);},"change");
on("export-review-packet",exportChatGptReviewPacket);
on("export-pilot-feedback",exportPilotFeedback);
on("plans-back",()=>show("home"));
on("plans-next",()=>{
  const options=allPagePairs().flatMap(({doc,index})=>pageViews(doc,index).map(view=>({doc,index,view})));
  const target=options.find(x=>x.view.type==="Proposed Floor Plan")||
    options.find(x=>x.view.type==="Floor Plan")||
    options.find(x=>x.view.type==="Site Plan")||options[0];
  if(target)openSheet(target.doc.id,target.index,target.view.key);
  else show("measure");
});
on("from-plans-manual",()=>{state.sheetId=null;state.docId=null;show("measure");});
on("measure-to-pages",()=>show("plans"));
on("measure-back-to-pages",()=>show("plans"));
on("choose-other-view",()=>show("plans"));
on("continue-draft-scale",()=>{
  try{
    if(!state.sheetId||!currentCalibration()||
      currentCalibration().method!=="printed_scale"||currentScale()!=="unverified")
      throw Error("This option is available only for an unverified printed scale.");
    state.provisionalScaleSheetId=state.sheetId;
    const pending=state.pendingTool;
    state.pendingTool=null;
    state.activeTool=pending||"inspect";
    state.draft=[];updateTool();updateOverlay();
    $("drawing-holder").scrollIntoView({block:"start",behavior:"instant"});
    say("PRELIMINARY ONLY: scale has not been checked. Trace your area to see a draft quantity. Verify a printed dimension before any final bid.");
  }catch(e){say("Cannot continue draft: "+e.message,true);}
});
on("use-printed-scale",()=>{
  try{
    const printed=printedScaleForSelectedView();
    if(!printed)throw Error("No usable printed scale was detected. Calibrate from a known dimension instead.");
    if(!state.sheetId)throw Error("Open a drawing view first.");
    const calibration=calibrationFromPrintedScale({
      sheet_id:state.sheetId,paper_in:printed.paper,real_ft:printed.real
    });
    state.project.calibrations=state.project.calibrations.filter(c=>c.sheet_id!==state.sheetId).concat(calibration);
    state.project.calibration_checks=state.project.calibration_checks.filter(c=>c.sheet_id!==state.sheetId);
    state.activeTool="check";state.draft=[];state.scalePhase="check";state.provisionalScaleSheetId=null;
    saveSoon();calculate();updateTool();updateOverlay();
    $("scale-guide").scrollIntoView({block:"start",behavior:"instant"});
    say("Printed scale applied as a starting point, NOT VERIFIED. Type one real dimension from the drawing, then click its two endpoints.");
  }catch(e){say("Could not set printed scale: "+e.message,true);}
});
function setZoom(next){
  if(!state.sheetId)return;
  state.zoom=Math.max(0.75,Math.min(3,next));
  renderPdfPage().catch(err=>say("Could not zoom this sheet: "+err.message,true));
}
on("zoom-in",()=>setZoom((state.zoom||1)*1.5));
on("zoom-out",()=>setZoom((state.zoom||1)/1.5));
on("zoom-fit",()=>setZoom(1));
on("drawing-add-pdf",()=>show("plans"));
on("materials-back-to-measure",()=>show("measure"));
on("materials-back",()=>show("measure"));
on("materials-next",()=>show("labor"));
on("measure-next",()=>show("materials"));
on("labor-back",()=>show("materials"));
on("labor-next",()=>show("review"));
on("back-to-materials",()=>show("materials"));
on("preview-proposal",()=>show("proposal"));
on("proposal-back",()=>show("review"));
on("proposal-print",()=>{calculate();renderProposal();window.print();});
on("labor-add",addLabor);
on("activity-line",renderLaborActivityInput,"change");
on("activity-add",addReviewedActivity);
on("labor-reviewed",()=>{state.project.labor_scope_reviewed=$("labor-reviewed").checked;saveSoon();});
for(const id of ["tax-rate","shipping","markup","contingency"])on(id,saveSettings,"change");
for(const id of ["pdf-input","pdf-input-alt"]){
  on(id,event=>{const f=event.target.files?.[0];if(f)ingestPdf(f);event.target.value="";},"change");
}
on("retail-price-file",async event=>{
  const input=event.target,selected=input.files?.[0];input.value="";
  if(!selected||!state.project)return;
  try{
    if(selected.size>3_000_000)throw Error("Select a CSV under 3 MB.");
    const rows=parsePriceObservations(await selected.text());
    state.project.retail_observations=rows;
    saveSoon();renderMaterials();
    say("Imported "+rows.length+" supplier price observations from CSV. They remain unconfirmed until the contractor approves a product and checks its current local price.");
  }catch(e){say("Could not import price CSV: "+e.message,true);}
},"change");
on("measure-assembly",renderAssemblyParams,"change");
on("manual-kind",()=>{$("manual-unit").textContent=assemblyUnit();},"change");
on("add-manual",recordManual);
on("overlay-canvas",handleCanvasClick);
on("vector-snap",()=>{
  snapData=null;
  $("snap-status").textContent=$("vector-snap").checked?"Select a drawing to load endpoints":"Free click";
  if($("vector-snap").checked&&state.sheetId)renderPdfPage().catch(()=>{$("snap-status").textContent="Vector extraction unavailable · free click";});
},"change");
on("finish-area",()=>{
  try{
    if(state.activeTool!=="polygon"||state.draft.length<3)throw Error("Click at least three corners before finishing.");
    recordDrawing();
  }catch(err){say(err.message,true);}
});
on("cancel-measure",()=>{state.draft=[];updateTool();updateOverlay();});
on("switch-scale-check",()=>{chooseTool(state.activeTool==="check"?"calibrate":"check");});
for(const btn of document.querySelectorAll(".measure-tool")){
  btn.addEventListener("click",()=>{
    if(!state.sheetId){say("Select a drawing page from the gallery first. Or enter a known quantity below.");return;}
    chooseTool(btn.dataset.tool);
    if(btn.dataset.tool==="calibrate")say("Set scale: enter one real printed dimension in feet, then click the two ends of that dimension.");
  });
}
let resizeWait=null;
window.addEventListener("resize",()=>{
  clearTimeout(resizeWait);
  resizeWait=setTimeout(()=>{
    if(state.activeScreen==="measure"&&state.sheetId)renderPdfPage().catch(err=>say(err.message,true));
  },250);
});
window.addEventListener("keydown",event=>{
  if(state.activeScreen!=="measure")return;
  if(event.key==="Escape"){state.draft=[];updateTool();updateOverlay();}
  if(event.key==="Enter"&&state.activeTool==="polygon"&&state.draft.length>=3&&
    !["INPUT","TEXTAREA","SELECT"].includes(document.activeElement?.tagName)){
      event.preventDefault();
      try{recordDrawing();}catch(err){say(err.message,true);}
    }
});
async function initialize(){
  try{
    await openDatabase();
    $("save-status").textContent="Local storage available";
    await renderProjects();
  }catch(err){
    state.persistOkay=false;
    $("save-status").textContent="Local saving unavailable";
    say("Browser storage is unavailable: "+err.message+". Saving PDFs between visits requires IndexedDB.",true);
  }
  try{
    const res=await fetch("/api/capabilities",{cache:"no-store"});
    const capabilities=res.ok?await res.json():{};
    state.aiAvailable=capabilities.ai_review===true&&capabilities.mode==="real_remote_inference";
  }catch{state.aiAvailable=false;}
  show("home");
}
initialize();
