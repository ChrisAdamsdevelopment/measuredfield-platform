"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs"),os=require("node:os"),path=require("node:path");
const puppeteer=require("D:/DevCache/npm-cache/_npx/4b4c857f6efdfb61/node_modules/puppeteer");
const url=process.env.MF_TAKEOFF_BROWSER_URL;
const fixture=process.env.MF_TAKEOFF_SYNTHETIC_FIXTURE ||
    "D:/GrowthEngineeringVenture/17_MEASUREDFIELD_REMODEL_TAKEOFF_20261122/05_TESTS/fixtures/scope_sample.pdf";
if(!/^http:\/\/127\.0\.0\.1:\d+\/takeoff$/.test(url||""))throw Error("Local-only test URL required");
const dir=fs.mkdtempSync(path.join(os.tmpdir(),"mf-takeoff-browser-"));
const wait=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
  const browser=await puppeteer.launch({
    executablePath:"C:/Program Files/Google/Chrome/Application/chrome.exe",
    headless:true,args:["--no-sandbox","--disable-gpu"]
  });
  try{
    const page=await browser.newPage();
    const errors=[];
    const failed=[];
    page.on("pageerror",e=>{errors.push(e.message);console.log("PAGE_ERROR",e.message)});
    page.on("console",m=>{if(m.type()==="error")console.log("BROWSER_CONSOLE_ERROR",m.text())});
    page.on("requestfailed",r=>{failed.push(r.url()+": "+r.failure()?.errorText);console.log("FAILED_RESOURCE",r.url(),r.failure()?.errorText)});
    const session=await page.createCDPSession();
    await session.send("Page.setDownloadBehavior",{behavior:"allow",downloadPath:dir});
    const response=await page.goto(url,{waitUntil:"domcontentloaded",timeout:20000});
    await wait(1200);
    assert.equal(response.status(),200);
    await page.waitForSelector("#new-estimate");
    const identity=await page.$eval(".mf-takeoff-account",el=>el.textContent);
    assert(identity.includes("Synthetic Contractor Business"));
    assert(identity.includes("Andrew Test"));
    const scope=await page.$eval('meta[name="mf-takeoff-storage-scope"]',el=>el.content);
    assert.match(scope,/^[a-f0-9]{32}$/);
    const contentHeader=await page.$eval("#home-title",el=>el.textContent);
    assert(contentHeader.includes("clearer estimate"));
    await page.click("#new-estimate");
    console.log("AFTER_NEW_CLICK",await page.evaluate(()=>({active:[...document.querySelectorAll(".screen.screen-active")].map(el=>el.id),ready:document.readyState})));
    await page.$eval("#new-name",el=>{el.value="Fictional Site Takeoff";});
    await page.click("#start-plans");
    await page.waitForSelector("#screen-plans.screen-active");
    await (await page.$("#pdf-input")).uploadFile(fixture);
    await page.waitForFunction(()=>document.querySelectorAll(".scope-item").length>3,{timeout:30000});
    await page.click('.spine button[data-step="measure"]');
    await page.waitForSelector("#screen-measure.screen-active");
    await page.select("#measure-assembly","tile_floor");
    await page.$eval("#manual-quantity",el=>{el.value="15";});
    await page.click("#add-manual");
    await page.waitForFunction(()=>document.querySelector("#recorded-count").textContent.includes("1 recorded"),{timeout:15000});
    await page.click('.spine button[data-step="review"]');
    await page.waitForSelector("#screen-review.screen-active");
    await page.click("#export-review-packet");
    let filename;
    for(let i=0;i<70;i++){
      filename=fs.readdirSync(dir).find(x=>/^measuredfield-takeoff-review-.*\.json$/.test(x));
      if(filename)break;
      await wait(150);
    }
    assert(filename,"Review packet not downloaded");
    const packet=JSON.parse(fs.readFileSync(path.join(dir,filename),"utf8"));
    assert.equal(packet.schema,"mf.takeoff.review-packet/1");
    assert(packet.scope_items.length>3);
    assert(packet.material_lines.length>0);
    assert(packet.field_entered_quantities.some(x=>x.quantity===15));
    assert(!JSON.stringify(packet).includes("Fictional Site Takeoff"));
    // Wait for actual IndexedDB completion, not elapsed download time.
    try{
      await page.waitForFunction(()=>document.querySelector("#save-status")?.textContent==="Saved on this computer",{timeout:10000});
    }catch(e){
      console.log("SAVE_DIAGNOSTIC",await page.evaluate(()=>({status:document.querySelector("#save-status")?.textContent,notice:document.querySelector("#global-notice")?.textContent})));
      throw e;
    }
    await page.reload({waitUntil:"domcontentloaded",timeout:20000});
    await wait(1200);
    await page.waitForSelector(".project-card",{timeout:12000});
    assert((await page.$eval(".project-card",el=>el.textContent)).includes("Fictional Site Takeoff"),
      "Saved project not present after reload");
    const saved=await page.evaluate(async()=>{const db=await import("/static/takeoff/public/guided/storage.mjs");const [r]=await db.listProjects();return r.manuals.map(m=>[m.quantity,m.measured.area_sf]);});
    assert.deepEqual(saved,[[15,15]],"Latest field quantity must survive reload, not merely the project name");
    // Actual pinned PDF.js operator list, not a mocked encoding.
    const vectorFixture=path.join(path.dirname(fixture),"calibration_sample.pdf");
    const vector=await page.evaluate(async bytes=>{
      const pdfjs=await import("/static/takeoff/public/vendor/pdfjs-5.7.284/pdf.min.mjs");
      const {extractStrokeEndpoints}=await import("/static/takeoff/src/vector-snap.mjs");
      pdfjs.GlobalWorkerOptions.workerSrc="/static/takeoff/public/vendor/pdfjs-5.7.284/pdf.worker.min.mjs";
      const doc=await pdfjs.getDocument({data:Uint8Array.from(bytes)}).promise;
      const p=await doc.getPage(1),ops=await p.getOperatorList(),r=extractStrokeEndpoints(ops,pdfjs.OPS,{view:p.view});
      const first=ops.fnArray.findIndex((v,i)=>v===pdfjs.OPS.constructPath&&ops.argsArray[i]?.[0]===pdfjs.OPS.stroke);
      return {version:pdfjs.version,count:r.points.length,corner:r.points.some(p=>p.x===700&&p.y===300),actualEncoding:first>=0};
    },Array.from(fs.readFileSync(vectorFixture)));
    assert.equal(vector.version,"5.7.284");assert(vector.count>0&&vector.corner&&vector.actualEncoding);
    console.log("PINNED_PDF_VECTOR_PASS",JSON.stringify(vector));
    const clipping=await page.evaluate(async()=>{
      const pdfjs=await import("/static/takeoff/public/vendor/pdfjs-5.7.284/pdf.min.mjs");
      const {extractStrokeEndpoints}=await import("/static/takeoff/src/vector-snap.mjs"),o=pdfjs.OPS;
      const rect=[o.endPath,[[0,5,-1,1,11,-1,1,11,1,1,5,1,4]],null];
      const line=[o.stroke,[[0,0,0,1,10,0,1,10,10]],null];
      const fnArray=[o.save,o.clip,o.constructPath,o.constructPath,o.paintFormXObjectBegin,o.constructPath,o.paintFormXObjectEnd,o.restore];
      const argsArray=[[],[],rect,line,[],line,[],[]];
      return extractStrokeEndpoints({fnArray,argsArray},o);
    });
    assert.deepEqual(clipping.points,[{x:10,y:0}]);assert.equal(clipping.reason,"partial_outer_strokes");
    console.log("RECTANGULAR_CLIP_AND_FORM_ISOLATION_PASS");
    await page.setViewport({width:390,height:844});
    const mobile=await page.evaluate(()=>({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth}));
    assert(mobile.scroll<=mobile.width,"Phone viewport horizontal overflow");
    // Synthetic quantity revision under the same measurement ID, followed by actual UI edits.
    await page.setViewport({width:1440,height:900});
    await page.click(".project-card");
    await page.click('.spine button[data-step="materials"]');
    await page.waitForSelector(".material-row");
    await page.select(".material-controls select","allowance");
    assert.match(await page.$eval(".material-controls label",e=>e.textContent),/Total allowance/);
    await page.$eval(".material-controls input[type=number]",e=>{e.value="500";e.dispatchEvent(new Event("change",{bubbles:true}));});
    await page.waitForFunction(()=>document.querySelector(".material-row .priced")?.textContent.includes("500"));
    await page.waitForFunction(()=>document.querySelector("#save-status")?.textContent==="Saved on this computer");
    await page.evaluate(async()=>{
      const db=await import("/static/takeoff/public/guided/storage.mjs");
      const [record]=await db.listProjects();
      const item=record.manuals[0];item.quantity*=2;item.measured.area_sf*=2;
      await db.saveProject(record);
    });
    await page.reload({waitUntil:"domcontentloaded"});
    await page.waitForSelector(".project-card");
    await page.click(".project-card");
    await page.click('.spine button[data-step="materials"]');
    await page.waitForSelector(".material-row");
    assert.match(await page.$eval(".material-row .status-chip",e=>e.textContent),/Needs a real price/);
    await page.select('.material-controls select[aria-label^="Cost inclusion"]',"supply_only");
    assert.match(await page.$eval(".material-row .status-chip",e=>e.textContent),/Needs a real price/);
    await page.$eval('.material-controls input[aria-label^="Operation ID"]',e=>{e.value="synthetic-op";e.dispatchEvent(new Event("change",{bubbles:true}));});
    assert.match(await page.$eval(".material-row .status-chip",e=>e.textContent),/Needs a real price/);
    await page.$eval(".material-controls input[type=number]",e=>{e.value="1000";e.dispatchEvent(new Event("change",{bubbles:true}));});
    await page.waitForFunction(()=>document.querySelector(".material-row .priced")?.textContent.includes("1,000"));
    console.log("STALE_ALLOWANCE_METADATA_REGRESSION_PASS");
    await page.click("#export-project");
    await page.waitForFunction(()=>document.querySelector("#save-status")?.textContent==="Saved on this computer");
    let backup;
    for(let i=0;i<70;i++){backup=fs.readdirSync(dir).find(x=>x.endsWith(".mfbackup"));if(backup)break;await wait(150);}
    assert(backup,"Complete backup download missing");
    const ctx=await browser.createBrowserContext();
    try{
      const fresh=await ctx.newPage();
      await fresh.goto(url,{waitUntil:"networkidle0"});
      await fresh.click("#new-estimate");
      await fresh.$eval("#new-name",e=>e.value="Unrelated active test project");
      await fresh.click("#start-dimensions");
      await fresh.waitForSelector("#screen-measure.screen-active");
      await (await fresh.$("#restore-project")).uploadFile(path.join(dir,backup));
      await fresh.waitForFunction(async()=>{const db=await import("/static/takeoff/public/guided/storage.mjs");return (await db.listProjects()).some(x=>x.project.name==="Fictional Site Takeoff");},{timeout:10000});
      assert.match(await fresh.$eval("#breadcrumb-project",e=>e.textContent),/Unrelated active test project/);
      assert(await fresh.$("#screen-measure.screen-active"),"Restore must not replace the active screen");
      const restored=await fresh.evaluate(async()=>{const db=await import("/static/takeoff/public/guided/storage.mjs");const r=(await db.listProjects()).find(x=>x.project.name==="Fictional Site Takeoff");return {qty:r.manuals[0].quantity,pdfs:(await Promise.all(r.documents.map(x=>db.getPdf(x.id)))).every(x=>x?.blob instanceof Blob)};});
      assert.deepEqual(restored,{qty:30,pdfs:true});
      console.log("SCOPED_BACKUP_RESTORE_PRESERVES_ACTIVE_PROJECT_PASS");
    }finally{await ctx.close();}
    assert.deepEqual(errors,[]);
    assert.deepEqual(failed,[]);
    console.log("MEASUREDFIELD_SAME_HOST_TAKEOFF_BROWSER_PASS",
      JSON.stringify({scope_items:packet.scope_items.length,materials:packet.material_lines.length,
        scoped_storage:true,persist_after_reload:true,mobile,js_errors:errors.length,failed_requests:failed.length}));
  }finally{
    await browser.close();
    fs.rmSync(dir,{recursive:true,force:true});
  }
})().catch(e=>{console.error("MEASUREDFIELD_SAME_HOST_TAKEOFF_BROWSER_FAIL",e.stack||e);process.exitCode=1;});
