import {chromium,webkit} from 'playwright/test';
import {build} from 'esbuild';
import assert from 'node:assert/strict';
import {writeFile,mkdir} from 'node:fs/promises';
const base=process.env.ERIC_WEB_PREVIEW||'http://127.0.0.1:4176';
const output=process.env.ERIC_WEB_EVIDENCE||'D:/Eric OS/backups/web-pwa-v1-20261029/binary-storage';
await mkdir(output,{recursive:true});
const compiled=await build({stdin:{resolveDir:process.cwd(),contents:"import Dexie from 'dexie';import{storeFile,fileReference,requireFile,readFilePart,releaseUnusedFile}from'./src/cloud/binaryStore.js';window.binaryTest={Dexie,storeFile,fileReference,requireFile,readFilePart,releaseUnusedFile};"},bundle:true,write:false,format:'iife',platform:'browser'});
const reports=[];
for(const engine of [chromium,webkit]){
 const browser=await engine.launch({headless:true});
 try{
  const context=await browser.newContext({serviceWorkers:'block'}),page=await context.newPage();
  await page.goto(base+'/manifest.webmanifest');await page.addScriptTag({content:compiled.outputFiles[0].text});
  const result=await page.evaluate(async()=>{
   const {Dexie,storeFile,fileReference,requireFile,readFilePart,releaseUnusedFile}=window.binaryTest;
   const name='eric-synthetic-bytes-'+crypto.randomUUID(),owner='synthetic-owner',other='other-owner';
   const v1=new Dexie(name);v1.version(1).stores({outbox:'id,owner',drafts:'[owner+form],owner'});await v1.open();
   await v1.outbox.put({id:'legacy-text',owner,text:'Existing synthetic local pending item',files:[]});v1.close();
   const db=new Dexie(name);db.version(1).stores({outbox:'id,owner',drafts:'[owner+form],owner'});db.version(2).stores({files:'id,owner,state,createdAt',fileParts:'[fileId+index],fileId'});await db.open();
   try{
    const legacyPreserved=(await db.outbox.get('legacy-text')).text==='Existing synthetic local pending item';
    const bytes=new Uint8Array(8_000_123);for(let index=0;index<bytes.length;index++)bytes[index]=index%251;
    const file=new File([bytes],'synthetic-multipart.pdf',{type:'application/pdf'}),stored=await storeFile(db,owner,file),ref=fileReference(stored);
    const hex=value=>Array.from(new Uint8Array(value),byte=>byte.toString(16).padStart(2,'0')).join('');
    const expected=hex(await crypto.subtle.digest('SHA-256',bytes));
    let otherDenied=false;try{await requireFile(db,other,ref);}catch{otherDenied=true;}
    db.close();await db.open();let restored=0;
    for(let index=0;index<stored.partCount;index++){const part=await readFilePart(db,owner,ref,index);for(let at=0;at<part.length;at++)if(part[at]!==bytes[restored+at])throw new Error('Changed original byte');restored+=part.length;}
    await db.drafts.put({owner,form:'capture',files:[ref]});await releaseUnusedFile(db,owner,ref.fileId);const draftProtected=await db.files.count()===1;
    await db.outbox.put({id:'pending-file',owner,state:'local_pending',files:[ref]});await db.drafts.delete([owner,'capture']);await releaseUnusedFile(db,owner,ref.fileId);const pendingProtected=await db.files.count()===1;
    await releaseUnusedFile(db,other,ref.fileId);const ownerProtected=await db.files.count()===1;
    await db.fileParts.update([ref.fileId,1],{bytes:new Uint8Array([1,2])});let corruptionDetected=false;try{await readFilePart(db,owner,ref,1);}catch{corruptionDetected=true;}
    await db.outbox.delete('pending-file');await releaseUnusedFile(db,owner,ref.fileId);
    return {legacyPreserved,hashMatched:stored.sha256===expected,parts:stored.partCount,restoredBytes:restored,otherDenied,draftProtected,pendingProtected,ownerProtected,corruptionDetected,explicitReleaseRemovedOnlyUnusedBytes:await db.files.count()===0&&await db.fileParts.count()===0,legacyStillPresent:!!await db.outbox.get('legacy-text')};
   }finally{await db.delete();}
  });
  assert.equal(result.parts,3);assert.equal(result.restoredBytes,8_000_123);for(const [key,value] of Object.entries(result))if(typeof value==='boolean')assert.equal(value,true,key);
  reports.push({engine:engine.name(),...result});
 }finally{await browser.close();}
}
await writeFile(output+'/report.json',JSON.stringify({passed:true,reports},null,2));console.log(JSON.stringify({passed:true,reports}));
