/** Local-only deterministic application of an owner-approved correction. */
async function hash(text){
 const bytes=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(text));
 return Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,"0")).join("");
}
export async function draftReference(projectId,packet){
 if(typeof projectId!=="string"||!projectId||!packet)throw Error("Current project required");
 // Calendar labels alone are not a project change; computed price status and
 // actual findings remain part of the fingerprint and invalidate stale drafts.
 const {as_of,...evidence}=packet;
 if(evidence.local_skill_assurance){
   const {as_of:skillDate,...skill}=evidence.local_skill_assurance;
   evidence.local_skill_assurance=skill;
 }
 return {project_ref:await hash("mf.takeoff.project:"+projectId),client_fingerprint:await hash(JSON.stringify(evidence))};
}
export function correctFieldQuantity(manuals,draft){
 if(draft?.kind!=="field_quantity"||draft.section!=="field_entered_quantities")throw Error("Only field quantity drafts can be applied");
 const matches=manuals.filter(m=>m.id===draft.target_id);
 if(matches.length!==1)throw Error("Source quantity is missing or ambiguous");
 const old=matches[0],qty=draft.proposed_quantity,unit=draft.unit;
 if(old.source!=="field_entered"||draft.source?.source!=="field_entered_unverified"||
    old.assemblyId!==draft.source.assembly_id||old.quantity!==draft.expected_quantity||
    old.unit!==unit||draft.source.unit!==unit||draft.source.quantity!==old.quantity||
    (old.scope_item_id||null)!==(draft.source.scope_item_id||null))throw Error("Source quantity changed; share a new snapshot");
 if(typeof qty!=="number"||!Number.isFinite(qty)||qty<=0||qty>1000000||qty===old.quantity)throw Error("Invalid proposed quantity");
 let measured;
 if(unit==="linear feet"){
   if(old.measured?.length_ft!==old.quantity||old.measured?.kinds?.join()!=="line"||
      old.measured.segments_ft?.length!==1||old.measured.segments_ft[0]!==old.quantity)throw Error("Source length requires manual review");
   measured={kinds:["line"],length_ft:qty,segments_ft:[qty]};
 }else if(unit==="items"){
   if(old.measured?.count!==old.quantity||old.measured?.kinds?.join()!=="count"||!Number.isInteger(qty))throw Error("Count requires a whole number and matching evidence");
   measured={kinds:["count"],count:qty};
 }else if(unit==="square feet"){
   if(old.measured?.area_sf!==old.quantity||old.measured?.kinds?.join()!=="polygon"||
      Object.keys(old.measured).some(k=>!["kinds","area_sf"].includes(k)))throw Error("Source area requires manual review");
   measured={kinds:["polygon"],area_sf:qty};
 }else throw Error("Unknown field quantity unit");
 return {...old,quantity:qty,measured};
}
