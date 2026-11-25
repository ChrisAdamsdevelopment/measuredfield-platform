import test from "node:test";
import assert from "node:assert/strict";
import {createProject,parseProject,serializeProject} from "../app/static/takeoff/src/project.mjs";
import {computeAssembly} from "../app/static/takeoff/src/assemblies.mjs";
import {buildEstimate} from "../app/static/takeoff/src/estimate.mjs";
import {materialChoice,changeMaterialChoice,materialImpactPending,reviewMaterialImpact,remodelDecisionQueue} from "../app/static/takeoff/src/remodel-choices.mjs";
const asOf="2026-11-25";
const line=(id,cls="retail",needs=[])=>({line_id:id,material_key:id,assembly_id:"count_item",description:id,layer:"new",unit:"ea",qty:2,qty_with_waste:2,waste_pct:0,formula:"count",measurement_ids:["m"],price_class:cls,needs});
const estimate=(project,lines)=>buildEstimate({lines,selections:project.selections,quotes:project.quotes,settings:project.settings,asOf});
for(const [scope,assembly,measured,params] of [
 ["bath","tile_wall",{area_sf:100},{}],
 ["kitchen","count_item",{count:8},{description:"Cabinet hardware"}],
 ["interior","wall_drywall",{area_sf:140},{}],
 ["garage","wall_framing",{length_ft:12},{wall_framing:"2x4",height_ft:8}],
 ["deck","structural_member",{count:2},{callout:"Beam per drawings",callout_ref:"S1",member_count:2,member_length_ft:8,price_class:"supplier_quote"}]
]){
 test(scope+" choices derived from existing measured assemblies",()=>{
  const p=createProject({scope});
  const lines=computeAssembly(assembly,measured,params,{measurement_ids:["m"],layer:"new"});
  assert.ok(lines.length);
  const q=remodelDecisionQueue(p,lines,estimate(p,lines));
  assert.deepEqual(new Set(q.map(x=>x.line.line_id)),new Set(lines.map(x=>x.line_id)));
  const next=changeMaterialChoice(p,lines[0],{level:"custom",specification:"Custom dimension or finish",supplier:"Specialty supply house"});
  assert.equal(next.selections[0].mode,"supplier_quote");
  assert.equal(materialChoice(next,lines[0]).supplier,"Specialty supply house");
  assert.equal(estimate(next,lines).lines.find(l=>l.line_id===lines[0].line_id).price_status,"QUOTE_PENDING");
  assert.equal(remodelDecisionQueue(next,lines,estimate(next,lines))[0].group,"quote");
  assert.equal(materialImpactPending(next,lines[0]),true);
 });
}
test("upgrade only reopens targeted cost; quantities and other prices remain",()=>{
 const a=line("a"),b=line("b");
 const p=createProject({scope:"interior",settings:{tax_rate_pct:0,shipping_cents:0}});
 p.selections=[{line_id:"a",mode:"contractor_rate",rate_cents_per_unit:500,rate_unit:"ea",operation_id:"install-a",cost_inclusion:"supply_only"},{line_id:"b",mode:"allowance",allowance_cents:3000}];
 p.labor_scope_reviewed=true;
 const before=structuredClone(p);
 const next=changeMaterialChoice(p,a,{level:"premium",specification:"Selected specialty finish",supplier:"Vendor"});
 assert.deepEqual(p,before);
 assert.equal(next.selections.find(s=>s.line_id==="a").mode,"unpriced");
 assert.equal(next.selections.find(s=>s.line_id==="a").cost_inclusion,null);
 assert.deepEqual(next.selections.find(s=>s.line_id==="b"),p.selections[1]);
 assert.equal(next.labor_scope_reviewed,false);
 assert.equal(estimate(next,[a,b]).totals.missing_lines,1);
 const restored=parseProject(serializeProject(next));assert.equal(materialChoice(restored,a).specification,"Selected specialty finish");
 assert.equal(changeMaterialChoice(next,a,{level:"premium",specification:"Selected specialty finish",supplier:"Vendor"}),next);
});
test("attached quote protected; custom description required",()=>{
 const p=createProject(),a=line("a");p.quotes=[{line_ids:["a"]}];
 assert.throws(()=>changeMaterialChoice(p,a,{level:"custom",specification:"Bespoke"}),/linked supplier quote/);
 assert.throws(()=>changeMaterialChoice(createProject(),a,{level:"custom"}),/Describe/);
});
test("impact review is quantity-bound, not a blanket approval",()=>{
 const a=line("a");let p=changeMaterialChoice(createProject(),a,{level:"premium",specification:"Finish"});
 assert.throws(()=>reviewMaterialImpact(p,a,"ok"),/Record/);
 p=reviewMaterialImpact(p,a,"Check substrate and installation allowance separately.");
 assert.equal(materialImpactPending(p,a),false);
 assert.equal(materialImpactPending(p,{...a,qty:3,qty_with_waste:3}),true);
 p=changeMaterialChoice(p,a,{level:"standard"});
 assert.equal(materialImpactPending(p,a),true);
});
test("all project lines remain retrievable and quote queue is prioritized",()=>{
 const p=createProject(),lines=Array.from({length:24},(_,i)=>line("m"+i,i===23?"supplier_quote":"retail"));
 const q=remodelDecisionQueue(p,lines,estimate(p,lines));assert.equal(q.length,24);
 assert.equal(q[0].line.line_id,"m23");assert.equal(q[0].group,"quote");
 assert.equal(new Set(q.map(x=>x.line.line_id)).size,24);
});

// Opt-in real Chrome against existing synthetic-owner local server.
if(process.env.MF_TAKEOFF_BROWSER_URL){
 test("real browser: compact kitchen queue, specialty selection, review and reopen",{timeout:60000},async()=>{
  const url=process.env.MF_TAKEOFF_BROWSER_URL;
  assert.match(url,/^http:\/\/127\.0\.0\.1:\d+\/takeoff$/);
  const {createRequire}=await import("node:module");
  const puppeteer=createRequire(import.meta.url)("D:/DevCache/npm-cache/_npx/4b4c857f6efdfb61/node_modules/puppeteer");
  const browser=await puppeteer.launch({executablePath:"C:/Program Files/Google/Chrome/Application/chrome.exe",headless:true,args:["--no-sandbox","--disable-gpu"]});
  try{
   const page=await browser.newPage(),errors=[];
   page.on("pageerror",e=>errors.push(e.message));
   await page.goto(url,{waitUntil:"networkidle0"});await page.click("#new-estimate");
   await page.$eval("#new-name",e=>e.value="Synthetic kitchen selection QA");
   await page.select("#new-type","kitchen");
   await page.click("#start-dimensions");
   await page.waitForSelector("#screen-measure.screen-active");
   await page.select("#measure-assembly","count_item");
   for(let i=0;i<8;i++){
    await page.$eval('[data-param="description"]',(e,i)=>e.value="Kitchen hardware group "+i,i);
    await page.$eval("#manual-quantity",e=>e.value="2");
    await page.click("#add-manual");
   }
   await page.click('.spine button[data-step="materials"]');
   await page.waitForSelector(".material-row");
   assert.equal(await page.$$eval(".material-row",e=>e.length),6);
   assert.match(await page.$eval("#remodel-queue-more",e=>e.textContent),/Showing 6 of 8/);
   await page.click("#remodel-queue-more button");
   assert.equal(await page.$$eval(".material-row",e=>e.length),8);
   await page.select(".material-controls select","contractor_rate");
   await page.$eval(".material-controls input[type=number]",e=>{e.value="25";e.dispatchEvent(new Event("change",{bubbles:true}));});
   await page.waitForFunction(()=>document.querySelectorAll(".material-row .priced").length===1);
   const row=await page.$eval(".material-row .priced",e=>e.closest(".material-row").querySelector(".material-title strong").textContent);
   const findRow=()=>page.evaluateHandle(t=>[...document.querySelectorAll(".material-row")].find(e=>e.querySelector(".material-title strong").textContent===t),row);
   const handle=await findRow();
   await handle.evaluate(e=>e.querySelector("details.remodel-choice-panel").open=true);
   await handle.evaluate(e=>{const p=e.querySelector(".remodel-choice-panel");p.querySelector("select").value="custom";const a=p.querySelectorAll("input");a[0].value="Custom cabinet pull, finish TBD";a[1].value="Specialty cabinet supplier";});
   await (await handle.$(".remodel-choice-panel button")).click();
   await page.waitForFunction(()=>document.querySelector(".material-row .status-chip").textContent.includes("Pending vendor quote"));
   await page.waitForFunction(()=>document.querySelector("#save-status").textContent==="Saved on this computer");
   const record=await page.evaluate(async()=>{const db=await import("/static/takeoff/public/guided/storage.mjs");return (await db.listProjects())[0];});
   assert.equal(record.manuals.length,8);
   assert.equal(record.project.material_choices[0].supplier,"Specialty cabinet supplier");
   assert.equal(record.project.selections.find(s=>s.line_id===record.project.material_choices[0].line_id).mode,"supplier_quote");
   await page.$$eval("#remodel-queue-toolbar button",bs=>bs.find(b=>b.textContent.startsWith("Supplier quotes")).click());
   assert.equal(await page.$$eval(".material-row",e=>e.length),1);
   await page.$eval('.remodel-choice-panel input[aria-label^="Installation impact"]',e=>e.value="Confirm matching screw spacing and extra installation time.");
   await page.$$eval(".remodel-choice-panel button",bs=>bs.find(b=>b.textContent==="Record installation review").click());
   await page.waitForFunction(()=>document.querySelector("#save-status").textContent==="Saved on this computer");
   await page.reload({waitUntil:"networkidle0"});await page.waitForSelector(".project-card");await page.click(".project-card");
   await page.click('.spine button[data-step="materials"]');
   assert.match(await page.$eval(".material-row .remodel-choice-panel summary",e=>e.textContent),/Custom/);
   assert.equal(await page.$eval('.remodel-choice-panel input[aria-label^="Preferred supplier"]',e=>e.value),"Specialty cabinet supplier");
   await page.setViewport({width:390,height:844});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth));
   assert.deepEqual(errors,[]);
   console.log("ADAPTIVE_REMODEL_SELECTION_REAL_CHROME_PASS");
  }finally{await browser.close();}
 });
}
