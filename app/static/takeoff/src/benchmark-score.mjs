// Local benchmark scorer. Reference rows are frozen independently of predictions.
export function scoreTakeoff(reference,predictions,{tolerance=0.05}={}){
  if(!Array.isArray(reference)||!Array.isArray(predictions)||!reference.length||!Number.isFinite(tolerance)||tolerance<0||tolerance>1)throw TypeError('Invalid benchmark inputs');
  const keys=new Set();
  for(const r of reference){
    if(typeof r.id!=='string'||!r.id||keys.has(r.id)||!['count','measurement'].includes(r.kind)||typeof r.unit!=='string'||!r.unit||!Number.isFinite(r.quantity)||r.quantity<0||(r.kind==='count'&&!Number.isInteger(r.quantity)))throw TypeError('Invalid or duplicate reference row');
    keys.add(r.id);
  }
  const groups=new Map();
  for(const p of predictions){if(!p||typeof p.id!=='string'||!p.id)throw TypeError('Prediction ID required');const g=groups.get(p.id)||[];g.push(p);groups.set(p.id,g);}
  const rows=reference.map(r=>{
    const g=groups.get(r.id)||[],p=g[0];
    let status;
    if(!g.length)status='omitted';
    else if(g.length>1)status='duplicate';
    else if(p.quantity==null)status='declined';
    else if(!Number.isFinite(p.quantity)||p.quantity<0||p.unit!==r.unit||(r.kind==='count'&&!Number.isInteger(p.quantity)))status='invalid';
    else status=(r.kind==='count'||r.quantity===0?p.quantity===r.quantity:Math.abs(p.quantity-r.quantity)<=r.quantity*tolerance+Number.EPSILON*Math.max(1,r.quantity)*4)?'correct':'incorrect';
    return {id:r.id,kind:r.kind,status};
  });
  const summarize=rr=>{const correct=rr.filter(r=>r.status==='correct').length;const attempted=rr.filter(r=>!['omitted','declined'].includes(r.status)).length;return {total:rr.length,correct,attempted,accuracy:rr.length?correct/rr.length:null,attempted_accuracy:attempted?correct/attempted:null};};
  const extras=predictions.filter(p=>!keys.has(p.id)).length;
  const duplicate_excess=[...groups.values()].reduce((n,g)=>n+Math.max(0,g.length-1),0);
  const overall=summarize(rows);
  // Recall alone must never conceal overestimation; predictions with null quantities
  // are abstentions for known IDs, but spurious IDs always count as extra attempts.
  const submitted=predictions.filter(p=>p.quantity!=null||!keys.has(p.id)||(groups.get(p.id)||[]).length>1).length;
  const precision=submitted?overall.correct/submitted:null,recall=overall.accuracy;
  const f1=precision===null?0:precision+recall===0?0:2*precision*recall/(precision+recall);
  const exact_bill=overall.correct===overall.total&&extras===0&&duplicate_excess===0;
  return {schema:'mf.takeoff.benchmark/1',tolerance,overall:{...overall,precision,recall,f1,submitted,exact_bill},counts:summarize(rows.filter(r=>r.kind==='count')),measurements:summarize(rows.filter(r=>r.kind==='measurement')),extra_predictions:extras,duplicate_excess,rows};
}
