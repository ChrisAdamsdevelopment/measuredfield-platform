import {OPEN_COST_REFERENCES} from "./open-cost-references.mjs";
import {lineBasis} from "./bid-readiness.mjs";
import {multiplyCents} from "./estimate.mjs";
export const REFERENCE_COUNT=OPEN_COST_REFERENCES.length;
const validInclusions=new Set(["supply_only","installed","other_nonlabor"]);
export function searchReferences(query="",limit=6){
 const terms=String(query).trim().toLowerCase().split(/\s+/).filter(Boolean);
 if(!terms.length)return [];
 return OPEN_COST_REFERENCES.filter(r=>terms.every(t=>(r.name+" "+r.category+" "+r.source_unit).toLowerCase().includes(t))).slice(0,Math.max(1,Math.min(30,limit)));
}
export function referenceAllowance(line,{referenceId,band,sourceQuantity,sourceBasis,confirmed,scopeReviewed,costInclusion}){
 const r=OPEN_COST_REFERENCES.find(x=>x.id===referenceId);
 if(!r||r.status!=="planning_only"||!Number.isSafeInteger(r.price_cents))throw Error("Reference unresolved or unavailable");
 if(!confirmed||!scopeReviewed||!validInclusions.has(costInclusion))throw Error("Confirm product fit, original source units and cost coverage");
 if(!["low","mid","high"].includes(band))throw Error("Select a planning price point");
 if(!Number.isFinite(sourceQuantity)||sourceQuantity<=0||sourceQuantity>1e8)throw Error("Enter the purchase quantity in the displayed source unit");
 if(typeof sourceBasis!=="string"||sourceBasis.trim().length<8||sourceBasis.length>400)throw Error("Explain the quantity conversion and price coverage");
 const rate=r[({low:"low_cents",mid:"price_cents",high:"high_cents"})[band]];
 if(!Number.isSafeInteger(rate)||rate<=0)throw Error("Reference price point unresolved");
 const amount=multiplyCents(sourceQuantity,rate);
 if(!Number.isSafeInteger(amount)||amount<=0)throw Error("Invalid calculated allowance");
 return {line_id:line.line_id,mode:"allowance",allowance_cents:amount,allowance_basis:"quantity_bound",quantity_basis:lineBasis(line),cost_inclusion:costInclusion,source_ref:"Open planning reference: "+r.name+"; "+r.version+"; "+r.url+"; "+sourceBasis.trim(),open_reference:{id:r.id,band,rate_cents:rate,source_unit:r.source_unit,source_quantity:sourceQuantity,source_basis:sourceBasis.trim(),rights:r.rights,attribution:r.attribution,version:r.version}};
}
