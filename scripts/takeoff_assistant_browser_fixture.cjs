// Offline provider fixture: validates browser request/crop/proposal handling, not model intelligence.
"use strict";
const assert=require("node:assert/strict");
const puppeteer=require("D:/DevCache/npm-cache/_npx/4b4c857f6efdfb61/node_modules/puppeteer");
const url=process.env.MF_TAKEOFF_BROWSER_URL,fixture="D:/GrowthEngineeringVenture/17_MEASUREDFIELD_REMODEL_TAKEOFF_20261122/05_TESTS/fixtures/scope_sample.pdf";
if(!/^http:\/\/127\.0\.0\.1:\d+\/takeoff$/.test(url||""))throw Error("Local fixture only");
(async()=>{
 const browser=await puppeteer.launch({executablePath:"C:/Program Files/Google/Chrome/Application/chrome.exe",headless:true,args:["--no-sandbox","--disable-gpu"]});
 try{
 const p=await browser.newPage(),requests=[],errors=[];
 p.on("pageerror",e=>errors.push(e.message));await p.setRequestInterception(true);
 p.on("request",r=>{
  const u=new URL(r.url());
  if(u.pathname==="/api/capabilities")return r.respond({status:200,contentType:"application/json",body:JSON.stringify({assistant:true})});
  if(u.pathname==="/api/takeoff/assistant"){
   requests.push(JSON.parse(r.postData()));
   return r.respond({status:200,contentType:"application/json",body:JSON.stringify({actual_inference:true,reply:"Synthetic fixture: protect the bathroom.",work_items:[{item:"Protect the work area",work_type:"allowance",action:"new",measure:"count",location:"Bathroom",evidence:"Synthetic fixture",needs:[]}]})});
  }
  return r.continue();
 });
 await p.goto(url,{waitUntil:"networkidle0"});
 await p.click("#new-estimate");await p.$eval("#new-name",e=>e.value="Assistant fixture");
 await p.$eval("#new-brief",e=>e.value="Only remodel the bathroom. Leave bedroom unchanged.");
 await p.click("#start-plans");await (await p.$("#pdf-input")).uploadFile(fixture);
 await p.waitForSelector(".page-action");await p.click(".page-action");
 await p.waitForFunction(()=>!document.querySelector("#canvas-wrap").hidden&&document.querySelector("#overlay-canvas").width>0);
 await p.click("#select-job-area");
 await p.evaluate(()=>{const c=document.querySelector("#overlay-canvas"),r=c.getBoundingClientRect();for(const [x,y] of [[.2,.2],[.5,.5]])c.dispatchEvent(new MouseEvent("click",{bubbles:true,clientX:r.left+r.width*x,clientY:r.top+r.height*y}));});
 await p.waitForFunction(()=>document.querySelector("#job-area-status").textContent.startsWith("Selected area"));
 assert(await p.$eval("#assistant-scope",e=>e.disabled));
 await p.click("#assistant-consent");await p.click("#assistant-scope");
 await p.waitForSelector(".assistant-message.assistant");
 assert.match(await p.$eval(".assistant-message.assistant",e=>e.textContent),/Synthetic fixture/);
 assert.equal(requests.length,1);assert(requests[0].consent);assert.match(requests[0].brief,/Leave bedroom unchanged/);
 assert(Buffer.from(requests[0].image_base64,"base64").length>100);
 const context=JSON.parse(requests[0].context);assert("omitted" in context);assert("qualification" in context);
 const checks=await p.evaluate(async()=>{
  const {assistantEstimateContext}=await import("/static/takeoff/src/job-context.mjs");
  const r={description:"Tile",qty:63,unit:"sf",flags:["scale_unverified"],assumptions:["Printed scale not checked"],needs:["owner_selection"]};
  const str=assistantEstimateContext({scope:"bath"},null,[],{lines:Array.from({length:100},()=>r),warnings:[{code:"REVISION_REVIEW",message:"Confirm revision"}],excluded:[]},null);
  const v=JSON.parse(str);return {length:str.length,flag:v.measured[0].flags[0],warning:v.warnings[0].code,omitted:v.omitted.measured};
 });
 assert(checks.length<=6000&&checks.flag==="scale_unverified"&&checks.warning==="REVISION_REVIEW"&&checks.omitted>0);
 await p.click(".assistant-proposal button");await p.waitForFunction(()=>document.querySelector(".assistant-proposal button").textContent==="Added for review");
 await p.waitForFunction(()=>document.querySelector("#save-status").textContent==="Saved on this computer");
 const record=await p.evaluate(async()=>{const db=await import("/static/takeoff/public/guided/storage.mjs");const [r]=await db.listProjects();return {scope:r.project.scope_items.find(i=>i.title==="Protect the work area"),measurements:r.project.measurements.length};});
 assert.equal(record.scope.status,"proposed");assert.equal(record.measurements,0);assert.deepEqual(errors,[]);
 console.log("ASSISTANT_BROWSER_FIXTURE_PASS",JSON.stringify({cropBytes:Buffer.from(requests[0].image_base64,"base64").length,proposalStatus:record.scope.status,measurements:record.measurements}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
