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
    await page.reload({waitUntil:"domcontentloaded",timeout:20000});
    await wait(1200);
    await page.waitForSelector(".project-card",{timeout:12000});
    assert((await page.$eval(".project-card",el=>el.textContent)).includes("Fictional Site Takeoff"),
      "Saved project not present after reload");
    await page.setViewport({width:390,height:844});
    const mobile=await page.evaluate(()=>({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth}));
    assert(mobile.scroll<=mobile.width,"Phone viewport horizontal overflow");
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
