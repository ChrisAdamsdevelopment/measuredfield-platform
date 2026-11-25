// Private, unencrypted, local backup. SHA-256 detects corruption, not malicious modification/authenticity.
import {parseProject,validateProject} from './project.mjs';
const MAGIC='MF-BACKUP-1\n',enc=new TextEncoder(),dec=new TextDecoder('utf-8',{fatal:true});
const MAX_MANIFEST=25*1024*1024,MAX_BYTES=200*1024*1024,MAX_PDF=90*1024*1024,HEADER=MAGIC.length+4+64;
const digest=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
const isId=s=>typeof s==='string'&&s.length>0&&s.length<=180;
function validateRecord(record){
 if(!record||!isId(record.id)||record.project?.id!==record.id||!Array.isArray(record.documents)||!Array.isArray(record.manuals)||record.documents.length>100||record.manuals.length>10000||!record.scopeChoices||typeof record.scopeChoices!=='object'||Array.isArray(record.scopeChoices))throw Error('Invalid backup project record');
 const project=parseProject(record.project);if(project.id!==record.id||validateProject(project).errors.length)throw Error('Invalid project');
 const docs=new Map();
 for(const d of record.documents){
  if(!isId(d?.id)||docs.has(d.id)||typeof d.name!=='string'||!Number.isInteger(d.pageCount)||d.pageCount<1||d.pageCount>10000)throw Error('Invalid PDF metadata');docs.set(d.id,d);
 }
 for(const s of project.sheets){if(!docs.has(s.file_id)||!Number.isInteger(s.page_index)||s.page_index<0||s.page_index>=docs.get(s.file_id).pageCount)throw Error('Missing original drawing reference');}
 for(const s of project.sheet_register||[]){if(!docs.has(s.file_id)||!Number.isInteger(s.page_index)||s.page_index<0||s.page_index>=docs.get(s.file_id).pageCount)throw Error('Missing register drawing reference');}
 const mids=new Set();
 for(const m of record.manuals){if(!isId(m?.id)||mids.has(m.id)||!isId(m.assemblyId)||!Number.isFinite(m.quantity)||m.quantity<=0||!m.measured||typeof m.measured!=='object'||!m.params||typeof m.params!=='object')throw Error('Invalid field quantity');mids.add(m.id);}
 return {...record,project};
}
async function pdfBytes(blob){
 if(!(blob instanceof Blob)||blob.size<5||blob.size>MAX_PDF)throw Error('Missing or oversized original PDF');
 const bytes=await blob.arrayBuffer();
 if(!new TextDecoder().decode(bytes.slice(0,1024)).includes('%PDF-'))throw Error('Invalid original PDF signature');
 return bytes;
}
export async function createBackup(record,pdfs,{scope=null}={}){
 if(scope!==null&&!/^[a-f0-9]{32}$/.test(scope))throw Error('Invalid backup account scope');
 const project=validateRecord(record);
 if(!Array.isArray(pdfs)||pdfs.length!==project.documents.length)throw Error('Every original PDF must be present');
 const files=[],parts=[],byId=new Map();
 for(const p of pdfs){if(!p||byId.has(p.id))throw Error('Duplicate PDF record');byId.set(p.id,p);}
 let size=0;
 for(const d of project.documents){
  const p=byId.get(d.id);if(!p||p.projectId!==project.id)throw Error('Missing or unrelated PDF: '+d.id);
  const bytes=await pdfBytes(p.blob);size+=bytes.byteLength;if(size>MAX_BYTES)throw Error('Backup exceeds local size limit');
  files.push({id:d.id,filename:d.name,size:bytes.byteLength,sha256:await digest(bytes)});parts.push(p.blob);
 }
 const body=enc.encode(JSON.stringify({schema:'mf.project-backup/1',account_scope:scope,record:project,files}));
 if(body.byteLength>MAX_MANIFEST||size+body.byteLength+HEADER>MAX_BYTES)throw Error('Backup exceeds 200 MiB limit');
 const length=new ArrayBuffer(4);new DataView(length).setUint32(0,body.byteLength,false);
 return new Blob([enc.encode(MAGIC),length,enc.encode(await digest(body)),body,...parts],{type:'application/octet-stream'});
}
export async function readBackup(blob,{scope=null}={}){
 if(!(blob instanceof Blob)||blob.size<HEADER||blob.size>MAX_BYTES)throw Error('Invalid backup size');
 const header=await blob.slice(0,HEADER).arrayBuffer();
 if(dec.decode(header.slice(0,MAGIC.length))!==MAGIC)throw Error('Unsupported backup format');
 const length=new DataView(header).getUint32(MAGIC.length,false);
 if(!length||length>MAX_MANIFEST||HEADER+length>blob.size)throw Error('Invalid backup manifest length');
 const bytes=await blob.slice(HEADER,HEADER+length).arrayBuffer();
 if(await digest(bytes)!==dec.decode(header.slice(MAGIC.length+4)))throw Error('Backup manifest checksum mismatch');
 const manifest=JSON.parse(dec.decode(bytes),(k,v)=>['__proto__','constructor','prototype'].includes(k)?undefined:v);
 if(manifest.schema!=='mf.project-backup/1'||!Array.isArray(manifest.files))throw Error('Unsupported backup manifest');
 if((manifest.account_scope??null)!==scope)throw Error('Backup belongs to another account/workspace or is unbound');
 const record=validateRecord(manifest.record),files=manifest.files,pdfs=[];
 if(files.length!==record.documents.length)throw Error('Missing original PDFs');
 let offset=HEADER+length;
 for(const [i,d] of record.documents.entries()){
  const f=files[i];if(f?.id!==d.id||f.filename!==d.name||!Number.isSafeInteger(f.size)||f.size<5||f.size>MAX_PDF||offset+f.size>blob.size||!/^[a-f0-9]{64}$/.test(f.sha256))throw Error('Invalid original PDF manifest');
  const pdf=blob.slice(offset,offset+f.size,'application/pdf');offset+=f.size;
  if(await digest(await pdfBytes(pdf))!==f.sha256)throw Error('Original PDF checksum mismatch: '+d.id);
  pdfs.push({id:d.id,projectId:record.id,filename:d.name,blob:pdf});
 }
 if(offset!==blob.size)throw Error('Unexpected trailing backup data');
 return {record,pdfs};
}
