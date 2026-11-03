import {webkit} from 'playwright/test';
import {mkdir,mkdtemp} from 'node:fs/promises';
const browser=await webkit.launch({headless:true});
let persistent;
try{
 const root='D:/Eric OS/backups/web-pwa-v1-20261029/webkit-profiles';await mkdir(root,{recursive:true});
 const context=process.env.ERIC_WEBKIT_PERSISTENT==='1'?(persistent=await webkit.launchPersistentContext(await mkdtemp(root+'/storage-'),{headless:true,serviceWorkers:'block'})):await browser.newContext({serviceWorkers:'block'}),page=await context.newPage();
 await page.goto('http://127.0.0.1:4176/manifest.webmanifest');
 await page.evaluate(()=>{document.body.innerHTML='<input type="file" aria-label="Synthetic diagnostic file"/>';});
 await page.locator('input').setInputFiles({name:'synthetic.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.7\nSynthetic only\n%%EOF')});
 const result=await page.evaluate(async()=>{
   const open=indexedDB.open('eric-synthetic-storage-probe',1);open.onupgradeneeded=()=>open.result.createObjectStore('files');
   const db=await new Promise((resolve,reject)=>{open.onsuccess=()=>resolve(open.result);open.onerror=()=>reject(open.error);});
   const original=document.querySelector('input').files[0],bytes=new Uint8Array(await original.arrayBuffer()),values={selectedFile:original,newBlob:new Blob([bytes],{type:original.type}),byteArray:bytes},result={};
   for(const [name,value] of Object.entries(values))result[name]=await new Promise(resolve=>{const transaction=db.transaction('files','readwrite');transaction.objectStore('files').put(value,name);transaction.oncomplete=()=>resolve({saved:true});transaction.onerror=()=>resolve({saved:false,name:transaction.error?.name,message:transaction.error?.message});transaction.onabort=()=>resolve({saved:false,name:transaction.error?.name,message:transaction.error?.message});});
   db.close();indexedDB.deleteDatabase('eric-synthetic-storage-probe');return result;
 });
 await page.evaluate(()=>{window.selectedDiagnosticFile=document.querySelector('input').files[0];});
 await context.setOffline(true);
 const offline=await page.evaluate(async()=>{
   const file=window.selectedDiagnosticFile,result={};
   for(const [name,read] of Object.entries({whole:()=>file.arrayBuffer(),slice:()=>file.slice(0,file.size).arrayBuffer(),reader:()=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(reader.error);reader.readAsArrayBuffer(file);})})){
     try{result[name]={bytes:(await read()).byteLength};}catch(error){result[name]={error:error.name,message:error.message};}
   }
   return result;
 });
 console.log(JSON.stringify({storage:result,offline}));
}finally{await persistent?.close();await browser.close();}
