// PDF.js 5.7.284 straight stroked path endpoint assistance.
// Not semantic wall recognition. Unsupported geometry fails closed.
const identity=()=>[1,0,0,1,0,0];
const validMatrix=m=>m?.length===6&&Array.from(m).every(Number.isFinite);
const multiply=(a,b)=>[a[0]*b[0]+a[2]*b[1],a[1]*b[0]+a[3]*b[1],a[0]*b[2]+a[2]*b[3],a[1]*b[2]+a[3]*b[3],a[0]*b[4]+a[2]*b[5]+a[4],a[1]*b[4]+a[3]*b[5]+a[5]];
const transform=(m,x,y)=>({x:m[0]*x+m[2]*y+m[4],y:m[1]*x+m[3]*y+m[5]});
// Recognize only a single closed axis-aligned rectangle in pinned DrawOPS encoding.
// Do not infer the shape from the path bounding box, which may enclose a nonrectangular clip.
function rectangleClip(args,OPS,matrix){
 if(!Number.isInteger(OPS.endPath)||args?.[0]!==OPS.endPath||!Array.isArray(args?.[1])||args[1].length!==1)return null;
 const raw=args?.[1]?.[0];
 if(!(Array.isArray(raw)||ArrayBuffer.isView(raw))||![13,15].includes(raw.length))return null;
 const points=[];
 for(let i=0;i<12;i+=3){
  if(raw[i]!== (i===0?0:1)||!Number.isFinite(raw[i+1])||!Number.isFinite(raw[i+2]))return null;
  points.push(transform(matrix,raw[i+1],raw[i+2]));
 }
 if(raw.length===13?raw[12]!==4:raw[12]!==1||raw[13]!==raw[1]||raw[14]!==raw[2])return null;
 if(points.some(p=>!Number.isFinite(p.x)||!Number.isFinite(p.y)))return null;
 const xs=[...new Set(points.map(p=>p.x))],ys=[...new Set(points.map(p=>p.y))];
 if(xs.length!==2||ys.length!==2||new Set(points.map(p=>p.x+','+p.y)).size!==4)return null;
 for(let i=0;i<4;i++){const p=points[i],q=points[(i+1)%4];if((p.x===q.x)===(p.y===q.y))return null;}
 return [Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)];
}
const intersect=(a,b)=>a?[Math.max(a[0],b[0]),Math.max(a[1],b[1]),Math.min(a[2],b[2]),Math.min(a[3],b[3])]:b;
export function extractStrokeEndpoints(operatorList,OPS,{maxPoints=10000,maxOperators=100000,view=null}={}){
 const f=operatorList?.fnArray,a=operatorList?.argsArray;
 const empty=reason=>({points:[],reason});
 if(!Array.isArray(f)||!Array.isArray(a)||f.length!==a.length||!Number.isInteger(OPS?.constructPath)||!Number.isInteger(OPS?.stroke)||f.length>maxOperators)return empty('unsupported_operator_list');
 let matrix=identity(),blocked=false,clipRect=null;const stack=[],out=new Map(),complex=[];
 let skippedComplex=0,unsupportedClips=0;
 const add=p=>{
  if(!Number.isFinite(p.x)||!Number.isFinite(p.y))return;
  if(view&&(p.x<view[0]||p.x>view[2]||p.y<view[1]||p.y>view[3]))return;
  if(clipRect&&(p.x<clipRect[0]||p.x>clipRect[2]||p.y<clipRect[1]||p.y>clipRect[3]))return;
  const key=p.x.toFixed(5)+','+p.y.toFixed(5);
  if(!out.has(key))out.set(key,p);
 };
 for(let i=0;i<f.length;i++){
  const op=f[i],args=a[i];
  // Form and transparency-group graphics states are isolated in PDF.js.
  // Do not extract their internal geometry; retain only supported outer strokes.
  // Malformed, mismatched, or unclosed scopes still invalidate all results.
  if(op===OPS.paintFormXObjectBegin||op===OPS.beginGroup){
   if(complex.length>=64)return empty('complex_graphics_depth');
   complex.push(op===OPS.paintFormXObjectBegin?OPS.paintFormXObjectEnd:OPS.endGroup);
   skippedComplex++;continue;
  }
  if(op===OPS.paintFormXObjectEnd||op===OPS.endGroup){
   if(!complex.length||complex.pop()!==op)return empty('unbalanced_complex_graphics');
   continue;
  }
  if(complex.length)continue;
  if(op===OPS.save){stack.push({matrix:[...matrix],blocked,clipRect});continue;}
  if(op===OPS.restore){const prior=stack.pop();if(!prior)return empty('unbalanced_graphics_state');({matrix,blocked,clipRect}=prior);continue;}
  if(op===OPS.transform){if(!validMatrix(args))return empty('invalid_transform');matrix=multiply(matrix,args);if(!validMatrix(matrix))return empty('invalid_transform');continue;}
  if(op===OPS.clip||op===OPS.eoClip){
   const rect=f[i+1]===OPS.constructPath?rectangleClip(a[i+1],OPS,matrix):null;
   if(!rect){blocked=true;unsupportedClips++;continue;}
   clipRect=intersect(clipRect,rect);
   if(clipRect[0]>=clipRect[2]||clipRect[1]>=clipRect[3])blocked=true;
   i++;continue;
  }
  if(blocked||op!==OPS.constructPath||args?.[0]!==OPS.stroke)continue;
  // Pinned PDF.js format: [paintOp,[Float32Array commands],bounds].
  // DrawOPS: 0 move, 1 line, 4 close. Curves/fills/unknown encodings are skipped.
  const raw=args?.[1]?.[0];
  if(!(Array.isArray(raw)||ArrayBuffer.isView(raw))||raw.length>maxPoints*8)continue;
  const pending=[];let current=null,start=null,ok=true;
  for(let j=0;j<raw.length;){
   const code=raw[j++];
   if(code===4){if(!start||!current){ok=false;break;}current=start;continue;}
   if(code!==0&&code!==1||j+1>=raw.length){ok=false;break;}
   const x=raw[j++],y=raw[j++];
   if(!Number.isFinite(x)||!Number.isFinite(y)){ok=false;break;}
   const p=transform(matrix,x,y);
   if(code===0){start=p;current=p;}
   else{if(!current){ok=false;break;}pending.push(current,p);current=p;}
  }
  if(ok)for(const p of pending){add(p);if(out.size>maxPoints)return empty('point_limit');}
 }
 if(stack.length||complex.length)return empty('unbalanced_graphics_state');
 return {points:[...out.values()],reason:out.size?(skippedComplex||unsupportedClips?'partial_outer_strokes':'available'):'no_supported_strokes',skippedComplex,unsupportedClips};
}
// Screen-space tolerance stays constant at every zoom. Ambiguous candidates
// within 1.5 CSS pixels of equal distance cause a raw-click fallback.
export function chooseSnap(points,viewport,x,y,{radius=8,ambiguity=1.5}={}){
 if(!Array.isArray(points)||!viewport?.convertToViewportPoint||!Number.isFinite(x)||!Number.isFinite(y)||!Number.isFinite(radius)||radius<=0)return null;
 let first=null,second=null;
 for(const p of points){
  const [sx,sy]=viewport.convertToViewportPoint(p.x,p.y);
  const distance=Math.hypot(sx-x,sy-y);
  if(!Number.isFinite(distance)||distance>radius)continue;
  const c={point:p,x:sx,y:sy,distance};
  if(!first||distance<first.distance){second=first;first=c;}else if(!second||distance<second.distance)second=c;
 }
 if(!first||second&&second.distance-first.distance<=ambiguity)return null;
 return first;
}
