/* IndexedDB local persistence. Files never leave this browser; explicit backup still recommended. */
// The authenticated MeasuredField website injects an opaque user+business
// partition into its rendered takeoff page. The standalone local workbench
// retains its original database to preserve existing offline projects.
const scope=document.querySelector('meta[name="mf-takeoff-storage-scope"]')?.getAttribute("content")||"";
const DB_NAME=/^[a-f0-9]{32}$/.test(scope)
  ?"measuredfield-remodel-guided-v1-"+scope
  :"measuredfield-remodel-guided-v1";
const DB_VERSION=1;
let dbPromise;
export function openDatabase(){
  if(dbPromise)return dbPromise;
  dbPromise=new Promise((resolve,reject)=>{
    if(!("indexedDB" in window)){reject(Error("This browser does not support IndexedDB"));return;}
    const req=indexedDB.open(DB_NAME,DB_VERSION);
    req.onupgradeneeded=()=>{
      const db=req.result;
      if(!db.objectStoreNames.contains("projects"))db.createObjectStore("projects",{keyPath:"id"});
      if(!db.objectStoreNames.contains("pdfs"))db.createObjectStore("pdfs",{keyPath:"id"});
    };
    req.onsuccess=()=>resolve(req.result);
    req.onerror=()=>reject(req.error||Error("Failed to open browser storage"));
    req.onblocked=()=>reject(Error("Browser storage upgrade was blocked by another tab"));
  }).catch(e=>{dbPromise=null;throw e;});
  return dbPromise;
}
async function read(storeName,key=null){
  const db=await openDatabase();
  return new Promise((resolve,reject)=>{
    const tx=db.transaction(storeName,"readonly"),store=tx.objectStore(storeName);
    const req=key===null?store.getAll():store.get(key);
    req.onsuccess=()=>resolve(req.result);
    req.onerror=()=>reject(req.error||Error("Local read failed"));
  });
}
async function write(storeName,record){
  const db=await openDatabase();
  return new Promise((resolve,reject)=>{
    const tx=db.transaction(storeName,"readwrite");
    tx.objectStore(storeName).put(record);
    tx.oncomplete=()=>resolve(record.id);
    tx.onerror=()=>reject(tx.error||Error("Local save failed"));
    tx.onabort=()=>reject(tx.error||Error("Local save aborted"));
  });
}
export const listProjects=()=>read("projects");
export const getProject=(id)=>read("projects",id);
export const saveProject=(record)=>write("projects",record);
export const getPdf=(id)=>read("pdfs",id);
export const savePdf=(record)=>write("pdfs",record);
export async function deleteProject(id){
  const db=await openDatabase();
  const record=await getProject(id);
  return new Promise((resolve,reject)=>{
    const tx=db.transaction(["projects","pdfs"],"readwrite");
    tx.objectStore("projects").delete(id);
    for(const doc of record?.documents||[])tx.objectStore("pdfs").delete(doc.id);
    tx.oncomplete=()=>resolve();
    tx.onerror=()=>reject(tx.error);
  });
}

/** Atomic, collision-refusing restore; no overwrite of existing projects or PDF records. */
export async function restoreProjectBundle(record,pdfs){
  const db=await openDatabase();
  return new Promise((resolve,reject)=>{
    const tx=db.transaction(["projects","pdfs"],"readwrite");
    tx.oncomplete=()=>resolve(record.id);
    tx.onabort=()=>reject(tx.error||Error("Restore aborted; existing records were preserved"));
    tx.onerror=()=>{}; // IndexedDB aborts the entire transaction on a failed add.
    try{
      tx.objectStore("projects").add(record);
      for(const pdf of pdfs)tx.objectStore("pdfs").add(pdf);
    }catch(e){try{tx.abort()}catch{}reject(e);}
  });
}
