// MeasuredField Remodel Takeoff — human-reviewable recorded revision delta.
// It compares two deliberately traced, whole-sheet versions. It does NOT infer
// plan differences, approve structural changes, calculate change-order dollars,
// or imply that an unmeasured scope item was actually removed from construction.
import { computeTakeoff } from "./assemblies.mjs";
import { resolveSheets } from "./revisions.mjs";
import { calibrationStatus } from "./geometry.mjs";

export const MODULE_VERSION = "0.2.0";

export class RevisionDeltaError extends Error {
  constructor(code, message) { super(message); this.name = "RevisionDeltaError"; this.code = code; }
}
const fail = (code, message) => { throw new RevisionDeltaError(code,message); };
const round4 = (n) => Math.round((n + Number.EPSILON)*1e4)/1e4;
const n = (x) => typeof x==="number" && Number.isFinite(x) ? x : 0;
function key(line){
  return [line.material_key||"",line.unit||"",line.layer||"",line.description||""].join("\u001f");
}
function side(project,sheet){
  const measurements=project.measurements.filter(m=>m.sheet_id===sheet.id);
  // A single-sheet snapshot intentionally does not inherit the superseding
  // status of the old drawing; the version relationship is checked separately.
  const takeoff=computeTakeoff({
    sheets:[{id:sheet.id,file_id:sheet.file_id,page_index:sheet.page_index,sheet_number:sheet.sheet_number,title:sheet.title,revision:sheet.revision,issued_on:sheet.issued_on,accepted_on:sheet.accepted_on,supersedes:[],supersession:"whole_sheet",notes:sheet.notes}],
    measurements,
    calibrations:project.calibrations.filter(c=>c.sheet_id===sheet.id),
    calibration_checks:project.calibration_checks.filter(c=>c.sheet_id===sheet.id)
  });
  const knownChecks=project.calibration_checks.filter(c=>c.sheet_id===sheet.id);
  const calibrations=project.calibrations.filter(c=>c.sheet_id===sheet.id);
  const hasGeometry=measurements.some(m=>m.kind!=="count");
  const trust=hasGeometry
    ? (calibrations.length===1 ? calibrationStatus(calibrations[0],knownChecks) : "unverified")
    : "not_required_for_count";
  return {
    sheet_id:sheet.id,
    revision:sheet.revision,
    measurement_count:measurements.length,
    lines:takeoff.lines,
    excluded:takeoff.excluded,
    warnings:takeoff.warnings,
    scale_status:trust
  };
}
function grouped(lines){
  const out=new Map();
  for(const line of lines){
    const k=key(line);
    const current=out.get(k)||{
      material_key:line.material_key,description:line.description,unit:line.unit,layer:line.layer,
      qty:0,qty_with_waste:0,ids:[]
    };
    current.qty+=n(line.qty);
    current.qty_with_waste+=n(line.qty_with_waste);
    current.ids.push(line.line_id);
    out.set(k,current);
  }
  for(const item of out.values()){
    item.qty=round4(item.qty);
    item.qty_with_waste=round4(item.qty_with_waste);
  }
  return out;
}
export function compareRecordedSheetRevisions(project,{old_sheet_id,new_sheet_id}={}){
  if(!project || !Array.isArray(project.sheets) || !Array.isArray(project.measurements)
      || !Array.isArray(project.calibrations) || !Array.isArray(project.calibration_checks))
    fail("BAD_PROJECT","Supply a project with sheets, measurements, calibrations and calibration_checks.");
  const resolved=resolveSheets(project.sheets);
  const old=resolved.find(s=>s.id===old_sheet_id);
  const newer=resolved.find(s=>s.id===new_sheet_id);
  if(!old || !newer)fail("MISSING_SHEET","Both recorded sheet ids must exist.");
  if(!newer.supersedes.includes(old.id))fail("NOT_LINKED","New revision must explicitly supersede the selected older sheet.");
  if(newer.supersession!=="whole_sheet")fail("PARTIAL_REVISION","Clouded-only replacement requires a separate manual area-level reconciliation.");
  if(newer.status==="conflict"||old.status==="conflict")fail("SHEET_CONFLICT","Resolve ambiguous sheet-number conflicts first.");
  if(newer.revision<=old.revision)fail("NON_ASCENDING_REVISION","New sheet revision must have a higher verified revision number.");

  const before=side(project,old),after=side(project,newer);
  const oldLines=grouped(before.lines),newLines=grouped(after.lines);
  const keys=[...new Set([...oldLines.keys(),...newLines.keys()])].sort();
  const changes=[];
  for(const k of keys){
    const a=oldLines.get(k),b=newLines.get(k);
    const oldQty=a?.qty_with_waste||0,newQty=b?.qty_with_waste||0;
    const delta=round4(newQty-oldQty);
    if(Math.abs(delta)<1e-4)continue;
    changes.push({
      material_key:(b||a).material_key,
      description:(b||a).description,
      unit:(b||a).unit,layer:(b||a).layer,
      old_qty:oldQty,new_qty:newQty,delta_qty:delta,
      old_base_qty:a?.qty||0,new_base_qty:b?.qty||0,
      old_line_ids:a?.ids||[],new_line_ids:b?.ids||[],
      classification:!a?"newly_recorded":!b?"not_recorded_on_revision":"quantity_changed"
    });
  }
  const changedOldIds=new Set(changes.flatMap(c=>c.old_line_ids));
  const quotes=Array.isArray(project.quotes)?project.quotes:[];
  const affected_quote_ids=quotes.filter(q=>Array.isArray(q.line_ids)&&q.line_ids.some(id=>changedOldIds.has(id))).map(q=>q.id);
  const warnings=[];
  if(before.measurement_count===0)warnings.push("No quantities were traced on the older sheet; removal/addition conclusions cannot be drawn.");
  if(after.measurement_count===0)warnings.push("No quantities were traced on the newer sheet; do NOT interpret absent lines as confirmed deletions.");
  for(const item of [before,after]){
    if(item.scale_status==="failed"||item.scale_status==="unverified")
      warnings.push(item.sheet_id+": scale is not independently verified.");
    if(item.excluded.length)warnings.push(item.sheet_id+": "+item.excluded.length+" measurement(s) excluded from comparison.");
    if(item.warnings.length)warnings.push(item.sheet_id+": "+item.warnings.length+" takeoff warning(s) need review.");
  }
  if(affected_quote_ids.length)warnings.push(affected_quote_ids.length+" existing supplier quote(s) attached to changed former lines must be requoted or explicitly reconfirmed.");
  if(newer.warnings.length)warnings.push(newer.warnings.map(w=>w.message).join(" "));
  return {
    type:"recorded_quantity_revision_delta",old_sheet_id:old.id,new_sheet_id:newer.id,
    sheet_number:newer.sheet_number,old_revision:old.revision,new_revision:newer.revision,
    before,after,changes,affected_quote_ids,warnings,
    needs_contractor_review:true,
    disclosure:"This compares recorded assembly takeoffs on linked whole-sheet revisions only. It does not read differences from PDF images, verify missing work, or establish price/change-order approval."
  };
}
