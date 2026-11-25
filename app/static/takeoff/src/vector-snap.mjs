// PDF.js 5.7.284 straight stroked path endpoint assistance.
// Not semantic wall recognition. Unsupported geometry fails closed.
const identity=()=>[1,0,0,1,0,0];
const validMatrix=m=>m?.length===6&&Array.from(m).every(Number.isFinite);
const multiply=(a,b)=>[a[0]*b[0]+a[2]*b[1],a[1]*b[0]+a[3]*b[1],a[0]*b[2]+a[2]*b[3],a[1]*b[2]+a[3]*b[3],a[0]*b[4]+a[2]*b[5]+a[4],a[1]*b[4]+a[3]*b[5]+a[5]];
const transform=(m,x,y)=>({x:m[0]*x+m[2]*y+m[4],y:m[1]*x+m[3]*y+m[5]});
export function extractStrokeEndpoints(operatorList,OPS,{maxPoints=10000,maxOperators=100000,view=null}={}){
 const f=operatorList?.fnArray,a=operatorList?.argsArray;
 const empty=reason=>({points:[],reason});
 if(!Array.isArray(f)||!Array.isArray(a)||f.length!==a.length||!Number.isInteger(OPS?.constructPath)||!Number.isInteger(OPS?.stroke)||f.length>maxOperators)return empty('unsupported_operator_list');
 let matrix=identity(),blocked=false;const stack=[],out=new Map();
 const add=p=>{
  if(!Number.isFinite(p.x)||!Number.isFinite(p.y))return;
  if(view&&(p.x<view[0]||p.x>view[2]||p.y<view[1]||p.y>view[3]))return;
  const key=p.x.toFixed(5)+','+p.y.toFixed(5);
  if(!out.has(key))out.set(key,p);
 };
 for(let i=0;i<f.length;i++){
  const op=f[i],args=a[i];
  if(op===OPS.paintFormXObjectBegin||op===OPS.paintFormXObjectEnd||op===OPS.beginGroup||op===OPS.endGroup)return empty('complex_graphics_state');
  if(op===OPS.save){stack.push({matrix:[...matrix],blocked});continue;}
  if(op===OPS.restore){const prior=stack.pop();if(!prior)return empty('unbalanced_graphics_state');({matrix,blocked}=prior);continue;}
  if(op===OPS.transform){if(!validMatrix(args))return empty('invalid_transform');matrix=multiply(matrix,args);if(!validMatrix(matrix))return empty('invalid_transform');continue;}
  if(op===OPS.clip||op===OPS.eoClip){blocked=true;continue;}
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
 if(stack.length)return empty('unbalanced_graphics_state');
 return {points:[...out.values()],reason:out.size?'available':'no_supported_strokes'};
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
