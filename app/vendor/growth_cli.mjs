// Bounded local IPC adapter: Python Studio -> PBC-derived pure growth engine.
// One request per process; no network listener, filesystem reads or credentials.
import {evaluateGrowth} from './growth_engine.mjs';
const LIMIT=128_000;
let raw='';
try {
 for await(const chunk of process.stdin){
  raw+=chunk.toString('utf8');
  if(Buffer.byteLength(raw,'utf8')>LIMIT)throw Error('Request too large');
 }
 const req=JSON.parse(raw);
 if(!req||typeof req!=='object'||Array.isArray(req))throw Error('Invalid request');
 const cap=(x,n)=>Array.isArray(x)?x.slice(0,n):[];
 const t=req.site?.totals||{};
 const safeNum=x=>Number.isFinite(Number(x))&&Number(x)>=0?Math.min(Number(x),1e9):0;
 const totals=Object.fromEntries(['pageViews','leadRequests','observedSessions','contactClicks','engagedSeconds','formStarts','formSubmits'].map(k=>[k,safeNum(t[k])]));
 const site=req.site&&typeof req.site==='object'?{totals,
  pages:cap(req.site.pages,30).filter(x=>x&&typeof x.path==='string').map(x=>({
   path:x.path.slice(0,180),views:safeNum(x.views),engagedSeconds:safeNum(x.engagedSeconds),
   avgEngagedSeconds:safeNum(x.avgEngagedSeconds),contactClicks:safeNum(x.contactClicks)})),
  sources:cap(req.site.sources,10).map(x=>({name:String(x?.name||'unknown').slice(0,90),count:safeNum(x?.count)}))}:null;
 const ai=req.ai&&typeof req.ai==='object'?{
  sampleCount:safeNum(req.ai.sampleCount),rawResults:safeNum(req.ai.rawResults),
  brand:req.ai.brand||null,websites:cap(req.ai.websites,10),
  competitors:cap(req.ai.competitors,10),
  questions:cap(req.ai.questions,40).filter(x=>x&&typeof x==='object').map(q=>({
   id:String(q.id||'').slice(0,30),kind:q.kind==='nonbranded'?'nonbranded':'branded',
   prompt:String(q.prompt||'').slice(0,250),
   results:cap(q.results,15).filter(r=>r&&typeof r==='object').map(r=>({businessMention:r.businessMention===false?false:r.businessMention===true?true:null}))
  }))}:null;
 const sc=req.searchConsole&&typeof req.searchConsole==='object'?{
  status:req.searchConsole.status==='connected'?'connected':'unavailable',
  reason:String(req.searchConsole.reason||'No authorized Search Console data').slice(0,180)
 }:{status:'unavailable',reason:'No authorized Search Console data'};
 const result=evaluateGrowth({site,ai,searchConsole:sc,days:[7,30].includes(Number(req.days))?Number(req.days):30,
  businessName:typeof req.businessName==='string'?req.businessName:'This business'});
 process.stdout.write(JSON.stringify({ok:true,analysis:result,
  evidenceStatus:{site:site?'supplied_analytics_not_independently_verified':'unknown',
    ai:ai?'supplied_samples_not_independently_verified':'unknown',
    searchConsole:sc.status==='connected'?'caller_asserted_connection_not_verified':'unknown'},
  unknowns:['Actual organic traffic without authorized analytics','Customer acquisition channel attribution without tracked data',
    'Conversion rate without valid traffic and lead counts','Revenue or ROI without accounting records']}));
} catch(e) {
 process.stderr.write(String(e.message||e).slice(0,200)+'\n');
 process.exitCode=1;
}
