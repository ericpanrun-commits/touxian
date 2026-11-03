import {sha256} from '@noble/hashes/sha2.js';
import {UPLOAD_PART_BYTES} from './config';
export const BINARY_PART_BYTES=UPLOAD_PART_BYTES;
const hex=value=>Array.from(value,byte=>byte.toString(16).padStart(2,'0')).join('');
let persistenceRequested=false;
export async function requestPersistentStorage(){
  if(persistenceRequested||typeof navigator==='undefined'||!navigator.storage?.persist)return;
  persistenceRequested=true;
  try{if(!await navigator.storage.persisted())await navigator.storage.persist();}catch{/* Storage still commits normally if the browser declines persistent mode. */}
}

// Bytes survive WebKit contexts where retaining a File/Blob reference is not
// supported. Keep binary payloads out of list/cache/draft metadata reads.
export async function storeFile(db,owner,file,id=crypto.randomUUID()){
  await requestPersistentStorage();
  const count=Math.ceil(file.size/BINARY_PART_BYTES),whole=sha256.create();
  const value={id,owner,filename:file.name,mime:file.type||(/\.pdf$/i.test(file.name)?'application/pdf':''),bytes:file.size,partCount:count,state:'importing',createdAt:new Date().toISOString()};
  await db.files.add(value);
  for(let index=0;index<count;index++){
    const bytes=new Uint8Array(await file.slice(index*BINARY_PART_BYTES,Math.min(file.size,(index+1)*BINARY_PART_BYTES)).arrayBuffer());
    const expected=Math.min(BINARY_PART_BYTES,file.size-index*BINARY_PART_BYTES);
    if(bytes.byteLength!==expected)throw new Error('local_file_incomplete');
    whole.update(bytes);
    await db.fileParts.add({fileId:id,index,bytes,sha256:hex(sha256(bytes))});
  }
  const complete={...value,state:'complete',sha256:hex(whole.digest())};
  await db.files.put(complete);return complete;
}

export function fileReference(value){return {fileId:value.id,filename:value.filename,mime:value.mime,bytes:value.bytes,sha256:value.sha256,state:value.state};}

export async function requireFile(db,owner,reference){
  const value=await db.files.get(reference.fileId);
  if(!value||value.owner!==owner||value.state!=='complete'||value.bytes!==reference.bytes||value.filename!==reference.filename||!value.sha256||
     await db.fileParts.where('fileId').equals(value.id).count()!==value.partCount)throw new Error('local_file_incomplete');
  return fileReference(value);
}

export async function readFilePart(db,owner,reference,index){
  const file=await db.files.get(reference.fileId);
  if(!file||file.owner!==owner||file.state!=='complete'||file.sha256!==reference.sha256)throw new Error('local_file_incomplete');
  const part=await db.fileParts.get([file.id,index]);
  const expected=Math.min(BINARY_PART_BYTES,file.bytes-index*BINARY_PART_BYTES);
  if(!part||expected<=0||part.bytes.byteLength!==expected||hex(sha256(part.bytes))!==part.sha256)throw new Error('local_file_incomplete');
  return part.bytes;
}

// Only caller-authorized removal or confirmed-copy retention calls this.
// An active draft or any outbox reference protects the same file across tabs.
export async function releaseUnusedFile(db,owner,id){
  await db.transaction('rw',db.files,db.fileParts,db.drafts,db.outbox,async()=>{
    const file=await db.files.get(id);if(!file||file.owner!==owner)return;
    const references=value=>value.files?.some(item=>item.fileId===id);
    if(await db.drafts.where('owner').equals(owner).filter(references).first()||await db.outbox.where('owner').equals(owner).filter(references).first())return;
    await db.fileParts.where('fileId').equals(id).delete();await db.files.delete(id);
  });
}
