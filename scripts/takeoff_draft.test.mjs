import test from "node:test";
import assert from "node:assert/strict";
import {draftReference,correctFieldQuantity} from "../app/static/takeoff/src/takeoff-draft.mjs";
const manual={id:"field_1",assemblyId:"tile_floor",source:"field_entered",quantity:15,unit:"square feet",
  scope_item_id:"scope_1",measured:{kinds:["polygon"],area_sf:15},params:{waste_pct:10}};
const draft={kind:"field_quantity",section:"field_entered_quantities",target_id:"field_1",
  expected_quantity:15,proposed_quantity:20,unit:"square feet",
  source:{source:"field_entered_unverified",assembly_id:"tile_floor",scope_item_id:"scope_1",quantity:15,unit:"square feet"}};
test("field correction is deterministic, immutable and never changes a trace",()=>{
 const out=correctFieldQuantity([manual],draft);
 assert.equal(out.quantity,20);assert.equal(out.measured.area_sf,20);assert.equal(manual.quantity,15);
 assert.deepEqual(out.params,manual.params);
});
test("source identity, quantity, unit and ambiguous records reject",()=>{
 for(const m of [{...manual,source:"drawing_trace"},{...manual,quantity:16},{...manual,unit:"items"},
   {...manual,scope_item_id:"other"},{...manual,assemblyId:"other"},{...manual,measured:{...manual.measured,perimeter_ft:20}}])
   assert.throws(()=>correctFieldQuantity([m],draft));
 assert.throws(()=>correctFieldQuantity([manual,manual],draft));
 assert.throws(()=>correctFieldQuantity([manual],{...draft,kind:"review_note"}));
});
test("project and content fingerprints differ after edits",async()=>{
 const a=await draftReference("project-a",{quantity:15});
 assert.deepEqual(a,await draftReference("project-a",{quantity:15}));
 assert.notEqual(a.project_ref,(await draftReference("project-b",{quantity:15})).project_ref);
 assert.notEqual(a.client_fingerprint,(await draftReference("project-a",{quantity:20})).client_fingerprint);
});
test("count corrections are whole and length stays deterministic",()=>{
 const m={...manual,unit:"items",measured:{kinds:["count"],count:15}};
 const d={...draft,unit:"items",source:{...draft.source,unit:"items"}};
 assert.throws(()=>correctFieldQuantity([m],{...d,proposed_quantity:1.5}));
 assert.equal(correctFieldQuantity([m],d).measured.count,20);
 const l={...manual,unit:"linear feet",measured:{kinds:["line"],length_ft:15,segments_ft:[15]}};
 const ld={...draft,unit:"linear feet",source:{...draft.source,unit:"linear feet"}};
 assert.deepEqual(correctFieldQuantity([l],ld).measured,{kinds:["line"],length_ft:20,segments_ft:[20]});
});
