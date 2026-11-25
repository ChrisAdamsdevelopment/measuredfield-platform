// Work-area selection is a context boundary, never a takeoff quantity.
export function workRegion({docId,pageIndex,points,viewKey=null}){
 if(typeof docId!=="string"||!docId||!Number.isInteger(pageIndex)||pageIndex<0||!Array.isArray(points)||points.length!==2||
 points.some(p=>!Number.isFinite(p?.x)||!Number.isFinite(p?.y)))throw Error("Choose two corners on the drawing");
 const [a,b]=points,x1=Math.min(a.x,b.x),y1=Math.min(a.y,b.y),x2=Math.max(a.x,b.x),y2=Math.max(a.y,b.y);
 if(x2-x1<2||y2-y1<2)throw Error("Choose a larger work area");
 return {docId,pageIndex,viewKey,points:[{x:x1,y:y1},{x:x2,y:y1},{x:x2,y:y2},{x:x1,y:y2}]};
}
export function regionOnPage(region,docId,pageIndex){
 return Boolean(region&&region.docId===docId&&region.pageIndex===pageIndex&&Array.isArray(region.points)&&region.points.length===4&&region.points.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)));
}
export function cropBounds(region,viewport){
 const points=region.points.map(p=>viewport.convertToViewportPoint(p.x,p.y));
 const x=Math.max(0,Math.floor(Math.min(...points.map(p=>p[0])))),y=Math.max(0,Math.floor(Math.min(...points.map(p=>p[1]))));
 const r=Math.min(Math.ceil(viewport.width),Math.ceil(Math.max(...points.map(p=>p[0])))),b=Math.min(Math.ceil(viewport.height),Math.ceil(Math.max(...points.map(p=>p[1]))));
 if(r-x<2||b-y<2)throw Error("The selected work area is outside the drawing");
 return {x,y,width:r-x,height:b-y};
}

// Keep structured data valid, preserve evidence qualifications and explicitly report omissions.
export function assistantEstimateContext(project,region,work,takeoff,estimate){
 const rows=takeoff.lines||[],warnings=takeoff.warnings||[],excluded=takeoff.excluded||[];
 const bounded=(s,n=180)=>String(s??"").slice(0,n);
 const strings=v=>Array.isArray(v)?v.slice(0,8).map(x=>bounded(typeof x==="string"?x:JSON.stringify(x),220)):[];
 const data={jobType:project.scope,selectedPage:region?region.pageIndex+1:null,
  totals:estimate?.totals||null,work:[],measured:[],warnings:[],excluded:[],
  omitted:{work:work.length,measured:rows.length,warnings:warnings.length,excluded:excluded.length},
  qualification:"Draft quantities and prices are not verified by AI. Any omitted rows or warnings require review."};
 if(JSON.stringify(data).length>4000)data.totals=null;
 function append(key,items,projectRow){
  for(const item of items){
   data[key].push(projectRow(item));data.omitted[key]--;
   if(JSON.stringify(data).length>5950){data[key].pop();data.omitted[key]++;break;}
  }
 }
 append("warnings",warnings,x=>({code:bounded(x.code,80),message:bounded(x.message,220)}));
 append("excluded",excluded,x=>({reason:bounded(x.reason,100),explanation:bounded(x.explanation,220)}));
 append("measured",rows,x=>({description:bounded(x.description),qty:x.qty,unit:bounded(x.unit,30),
  needs:strings(x.needs),flags:strings(x.flags),assumptions:strings(x.assumptions),
  review:"Use all qualifications; not proof of verified quantity"}));
 append("work",work,x=>({title:bounded(x.title,100),status:bounded(x.status,30)}));
 return JSON.stringify(data);
}
