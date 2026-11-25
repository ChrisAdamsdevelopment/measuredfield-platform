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
