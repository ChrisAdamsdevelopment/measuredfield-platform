import test from "node:test";
import assert from "node:assert/strict";
import {OPEN_COST_REFERENCES} from "../app/static/takeoff/src/open-cost-references.mjs";
import {REFERENCE_COUNT,searchReferences,referenceAllowance} from "../app/static/takeoff/src/reference-integration.mjs";
import {computeAssembly} from "../app/static/takeoff/src/assemblies.mjs";
import {buildEstimate} from "../app/static/takeoff/src/estimate.mjs";
import {createProject,serializeProject,parseProject} from "../app/static/takeoff/src/project.mjs";
const line=computeAssembly("count_item",{count:4},{description:"Recessed fixtures"},{measurement_ids:["test"],layer:"new"})[0];
const args={referenceId:"led-recessed-lighting",band:"high",sourceQuantity:4,sourceBasis:"Four fixtures, each, supply only; verify selected model.",confirmed:true,scopeReviewed:true,costInclusion:"supply_only"};
test("all 189 existing open references available, source range preserved",()=>{
 assert.equal(REFERENCE_COUNT,189);assert.equal(OPEN_COST_REFERENCES.filter(r=>r.status==="planning_only").length,188);
 assert.equal(new Set(OPEN_COST_REFERENCES.map(r=>r.id)).size,189);
 assert(OPEN_COST_REFERENCES.every(r=>r.rights==="CC BY 4.0"));
 assert.equal(searchReferences("recessed lighting")[0].price_cents,2500);
 assert.equal(searchReferences("cabinet pulls")[0].id,"cabinet-hardware");
 assert.equal(searchReferences("stainless steel kitchen appliances")[0].status,"unresolved");
 assert.equal(searchReferences("").length,0);
});
test("source quantity and high price calculate allowance, not confirmed cost",()=>{
 const s=referenceAllowance(line,args);assert.equal(s.allowance_cents,20000);
 assert.equal(s.open_reference.source_unit,"each");assert.equal(s.cost_inclusion,"supply_only");
 const p=createProject();p.selections=[s];
 const loaded=parseProject(serializeProject(p));assert.deepEqual(loaded.selections[0],s);
 const estimate=buildEstimate({lines:[line],selections:[s],settings:{tax_rate_pct:0,shipping_cents:0},asOf:"2026-11-25"});
 assert.equal(estimate.lines[0].price_status,"ALLOWANCE");
 const stale=buildEstimate({lines:[{...line,qty:8,qty_with_waste:8}],selections:[s],settings:{tax_rate_pct:0,shipping_cents:0},asOf:"2026-11-25"});
 assert.equal(stale.totals.missing_lines,1);
});
test("reject missing scope, missing quantity, unresolved reference and unconfirmed conversion",()=>{
 for(const a of [{confirmed:false},{scopeReviewed:false},{sourceBasis:""},{sourceQuantity:0},{costInclusion:""},{referenceId:"stainless-steel-appliances"},{band:"luxury"}])assert.throws(()=>referenceAllowance(line,{...args,...a}));
});
if(process.env.MF_TAKEOFF_BROWSER_URL){
 test("real browser: full reference search, sourced allowance, quote lifecycle and persistence",{timeout:60000},async()=>{
  const url=process.env.MF_TAKEOFF_BROWSER_URL;
  assert.match(url,/^http:\/\/127\.0\.0\.1:\d+\/takeoff$/);
  const {createRequire}=await import("node:module");
  const puppeteer=createRequire(import.meta.url)("D:/DevCache/npm-cache/_npx/4b4c857f6efdfb61/node_modules/puppeteer");
  const browser=await puppeteer.launch({executablePath:"C:/Program Files/Google/Chrome/Application/chrome.exe",headless:true,args:["--no-sandbox","--disable-gpu"]});
  try{
   const page=await browser.newPage(),errors=[];page.on("pageerror",e=>errors.push(e.message));
   await page.goto(url,{waitUntil:"networkidle0"});await page.click("#new-estimate");await page.$eval("#new-name",e=>e.value="Synthetic kitchen procurement");
   await page.select("#new-type","kitchen");await page.click("#start-dimensions");await page.waitForSelector("#screen-measure.screen-active");
   await page.select("#measure-assembly","count_item");
   await page.$eval('[data-param="description"]',e=>e.value="Recessed fixtures");
   await page.$eval("#manual-quantity",e=>e.value="4");await page.click("#add-manual");await page.click('.spine button[data-step="materials"]');await page.waitForSelector(".material-row");
   await page.$eval('input[type=search]',e=>{e.closest("details").open=true;e.value="recessed lighting";e.dispatchEvent(new Event("input",{bubbles:true}));});
   assert.match(await page.$eval(".remodel-reference-results",e=>e.textContent),/LED Recessed Lighting/);
   await page.$eval(".remodel-reference-results details",e=>e.open=true);
   await page.select('select[aria-label="Planning price point"]',"high");
   await page.$eval('input[aria-label^="Purchase quantity"]',e=>e.value="4");
   await page.$eval('input[aria-label="Reference quantity and scope basis"]',e=>e.value="Four each, supply only, selected model still unknown.");
   await page.select('select[aria-label="Reference cost coverage"]',"supply_only");
   await page.click(".remodel-reference-results input[type=checkbox]:not([aria-label])");
   await page.click(".remodel-reference-results button");
   await page.waitForFunction(()=>document.querySelector(".material-row .priced")?.textContent.includes("200.00"));
   const openQuote=()=>page.$$eval(".material-row > details",ds=>ds.find(d=>d.querySelector("summary").textContent.includes("quote")).open=true);
   const clickText=t=>page.$$eval(".material-row button",(bs,t)=>{const b=bs.find(b=>b.textContent===t);if(!b)throw Error(t);b.click();},t);
   await openQuote();
   await page.$eval('input[aria-label="Quote supplier"]',e=>e.value="Synthetic specialty supplier");
   await clickText("Prepare quote request (not sent)");
   assert.match(await page.$eval(".material-row .priced",e=>e.textContent),/200.00/);
   await page.$eval('input[aria-label="Confirm replace existing price with pending quote"]',e=>e.checked=true);
   await clickText("Prepare quote request (not sent)");await openQuote();
   await clickText("Record request sent to supplier");await openQuote();
   await page.$eval('input[aria-label="Written quote total"]',e=>e.value="440");
   await page.$eval('input[aria-label="Quote valid through"]',e=>e.value="2099-12-31");
   await page.$eval('input[aria-label="Quote source"]',e=>e.value="Synthetic written offer 001");
   await page.select('select[aria-label="Quote cost coverage"]',"supply_only");
   await clickText("Record written supplier offer");await openQuote();
   await page.$eval('input[aria-label="Quote reviewer"]',e=>e.value="Synthetic reviewer");await clickText("Mark offer reviewed");await openQuote();
   await page.$eval('input[aria-label="Quote reviewer"]',e=>e.value="Synthetic approver");await clickText("Approve reviewed offer for estimate");
   await page.waitForFunction(()=>document.querySelector(".material-row .priced")?.textContent.includes("440.00"));
   await page.waitForFunction(()=>document.querySelector("#save-status").textContent==="Saved on this computer");
   const get=()=>page.evaluate(async()=>{const db=await import("/static/takeoff/public/guided/storage.mjs");return (await db.listProjects())[0];});
   const r=await get();assert.equal(r.project.quotes[0].status,"APPROVED");assert.equal(r.project.quotes[0].amount_cents,44000);assert.equal(r.project.quotes[0].history.length,4);assert.equal(r.project.quotes[0].prior_selection.allowance_cents,20000);
   await page.reload({waitUntil:"networkidle0"});await page.waitForSelector(".project-card");await page.click(".project-card");await page.click('.spine button[data-step="materials"]');
   assert.match(await page.$eval(".material-row .priced",e=>e.textContent),/440.00/);assert.deepEqual(errors,[]);
   console.log("FULL_OPEN_REFERENCE_AND_QUOTE_REAL_CHROME_PASS");
  }finally{await browser.close();}
 });
}
