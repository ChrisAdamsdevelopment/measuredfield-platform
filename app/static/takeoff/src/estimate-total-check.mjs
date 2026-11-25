// Re-add already calculated line and labor contributions, including lump-sum quotes once.
// Does not authenticate a snapshot or establish the correctness of source rates.
import {applyPercent} from './estimate.mjs';
const cents=n=>Number.isSafeInteger(n)&&n>=0;
export function checkEstimateTotals(estimate){
 const t=estimate?.totals,lines=estimate?.lines,labor=estimate?.labor;
 if(!t||!Array.isArray(lines)||!Array.isArray(labor))return ['TOTAL_EVIDENCE_MISSING'];
 const errors=[],bad=k=>errors.push(k),expected={confirmed_cents:0,provisional_cents:0,allowance_cents:0,labor_cents:0,taxable_base_cents:0};
 const buckets=['confirmed','provisional','allowance'],quotes=new Map();
 for(const l of lines){
  if(l.extended_cents!==null){
   if(!cents(l.extended_cents)||!buckets.includes(l.price_bucket)){bad('LINE_CONTRIBUTION_INVALID');continue;}
   expected[l.price_bucket+'_cents']+=l.extended_cents;
   if(l.taxable===true)expected.taxable_base_cents+=l.extended_cents;
  }
  if(l.quote_id&&['QUOTE_APPROVED','QUOTE_PROVISIONAL'].includes(l.price_status)){
   const group=quotes.get(l.quote_id)||[];group.push(l);quotes.set(l.quote_id,group);
  }
 }
 for(const group of quotes.values()){
  if(group.filter(l=>l.quote_allocation==='primary'&&cents(l.extended_cents)).length!==1||group.some(l=>l.quote_allocation==='included'&&l.extended_cents!==null))bad('QUOTE_ALLOCATION_INVALID');
 }
 for(const r of labor){if(r.amount_cents!==null){if(!cents(r.amount_cents))bad('LABOR_CONTRIBUTION_INVALID');else expected.labor_cents+=r.amount_cents;}}
 if(!Object.values(expected).every(cents))return [...errors,'TOTAL_OVERFLOW'];
 if(t.shipping_cents!==null&&!cents(t.shipping_cents))bad('SHIPPING_INVALID');
 try{
  expected.cost_subtotal_known_cents=expected.confirmed_cents+expected.provisional_cents+expected.allowance_cents+expected.labor_cents+(t.shipping_cents??0);
  expected.tax_cents=t.tax_rate_pct===null?null:applyPercent(expected.taxable_base_cents,t.tax_rate_pct);
  expected.percentage_base_cents=expected.cost_subtotal_known_cents+(expected.tax_cents??0);
  expected.markup_cents=applyPercent(expected.percentage_base_cents,t.markup_pct);
  expected.contingency_cents=applyPercent(expected.percentage_base_cents,t.contingency_pct);
  expected.grand_total_known_cents=expected.percentage_base_cents+expected.markup_cents+expected.contingency_cents;
  for(const [k,v] of Object.entries(expected))if((v!==null&&!cents(v))||t[k]!==v)bad('TOTAL_MISMATCH:'+k);
 }catch{bad('TOTAL_EVIDENCE_INVALID');}
 return [...new Set(errors)];
}
