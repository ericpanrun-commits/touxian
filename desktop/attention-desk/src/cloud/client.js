import { createClient } from '@supabase/supabase-js';
import Dexie from 'dexie';
import { sha256 } from '@noble/hashes/sha2.js';
import { API, SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, UPLOAD_PART_BYTES } from './config';
import {storeFile,fileReference,requireFile,readFilePart,releaseUnusedFile,requestPersistentStorage} from './binaryStore';

export const auth = createClient(SUPABASE_URL,SUPABASE_PUBLISHABLE_KEY,{auth:{storageKey:'eric-os-auth-v1',persistSession:true,autoRefreshToken:true,detectSessionInUrl:false}});
export const local = new Dexie('eric-os-v1');
local.version(1).stores({cache:'[owner+path],owner,updatedAt',outbox:'id,owner,[owner+state],createdAt',commands:'id,owner,[owner+state],resourceKey,createdAt',identity:'id',leases:'id',drafts:'[owner+form],owner'});
local.version(2).stores({files:'id,owner,state,createdAt',fileParts:'[fileId+index],fileId'});
let identity=null,pumping=false,stopped=true,cursor=null,pollTimer=null,polling=false,syncGeneration=0;
const tickets=new Map();
const listeners=new Set();
export function subscribe(listener){listeners.add(listener);return()=>listeners.delete(listener);}
function changed(){for(const listener of listeners)listener();window.dispatchEvent(new Event('eric-data-changed'));}
export function currentIdentity(){return identity;}
export class CloudError extends Error {constructor(message,code='connection_error',status=0){super(message);this.code=code;this.status=status;}}
function platform(){const ua=navigator.userAgent;return /iPhone/.test(ua)?'iphone':/iPad/.test(ua)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1)?'ipad':/Windows/.test(ua)?'windows':'web';}
function readScope(){return identity?{owner:identity.owner,deviceId:identity.device?.id}:null;}
function checkScope(scope,session){
  if(scope&&(identity?.owner!==scope.owner||identity?.device?.id!==scope.deviceId||session&&session.user.id!==scope.owner))
    throw new CloudError('登录设备已变化，请重新打开这份内容。','identity_changed',409);
}

export async function request(path,{method='GET',body,bytes,signal,retried=false,scope}={}) {
  const {data,error}=await auth.auth.getSession();
  if(error&&(error.name==='AuthRetryableFetchError'||error.status>=500))throw new CloudError('连接暂时中断，恢复后会自动继续。');
  if(error||!data.session)throw new CloudError('请重新登录，已保存的待发送内容会保留。','sign_in_required',401);
  checkScope(scope,data.session);
  const controller=new AbortController(),forward=()=>controller.abort();
  signal?.addEventListener('abort',forward,{once:true});if(signal?.aborted)controller.abort();
  const timer=setTimeout(()=>controller.abort(),60_000);
  try {
    const response=await fetch(API+path,{method,cache:'no-store',redirect:'error',signal:controller.signal,
      headers:{Authorization:'Bearer '+data.session.access_token,apikey:SUPABASE_PUBLISHABLE_KEY,...(bytes?{'Content-Type':'application/octet-stream'}:body!==undefined?{'Content-Type':'application/json'}:{})},
      ...(bytes?{body:bytes}:body!==undefined?{body:JSON.stringify(body)}:{})});
    const payload=await response.json();
    if(!response.ok) {
      const detail=payload.detail||{};
      if(response.status===401&&detail.code==='sign_in_required'&&!retried) {
        const refreshed=await auth.auth.refreshSession();
        if(!refreshed.error&&refreshed.data.session)return request(path,{method,body,bytes,signal,retried:true,scope});
      }
      throw new CloudError(detail.message||'暂时无法确认结果，已保存的内容会保留。',detail.code,response.status);
    }
    checkScope(scope);return payload;
  } catch(error) {
    if(error instanceof CloudError||signal?.aborted)throw error;
    throw new CloudError('连接暂时中断，已保存的内容会保留，恢复后会自动继续。');
  } finally {clearTimeout(timer);signal?.removeEventListener('abort',forward);}
}

export async function restoreIdentity() {
  const saved=await local.identity.get('current');
  const {data,error}=await auth.auth.getSession();
  if(!data.session) {
    if(saved&&(navigator.onLine===false||error?.name==='AuthRetryableFetchError')){identity={...saved,offline:true};return identity;}
    if(error)throw new CloudError('暂时无法恢复登录，联网后会自动重试。');
    identity=null;tickets.clear();return null;
  }
  const owner=data.session.user.id;
  try {
    const p=platform(),value=await request('/session',{method:'POST',body:{platform:p,label:({iphone:'Eric iPhone',ipad:'Eric iPad',windows:'Windows Eric OS',web:'Eric OS 浏览器'})[p]}});
    if(identity?.owner!==owner||identity?.device?.id!==value.device.id)tickets.clear();
    identity={id:'current',owner,device:value.device,email:value.account.email,offline:false};
    await local.identity.put(identity);return identity;
  } catch(error) {
    if((!error.status||error.status>=500)&&saved?.owner===owner){identity={...saved,offline:true};return identity;}
    throw error;
  }
}

export async function read(path,{signal}={}) {
  if(!identity)throw new CloudError('请先登录 Eric OS。','sign_in_required',401);
  const scope=readScope(),owner=scope.owner;
  try {
    const value=await request(path,{signal,scope});
    checkScope(scope);
    await local.cache.put({owner,path,value,updatedAt:Date.now()});
    checkScope(scope);
    return value;
  } catch(error) {
    checkScope(scope);
    if(error.status===401||error.status===403||error.code==='identity_changed'||signal?.aborted)throw error;
    const cached=await local.cache.get([owner,path]);
    checkScope(scope);
    if(!cached)throw error;
    if(cached.value===null)return null;
    return {...cached.value,_offline:{savedAt:cached.updatedAt}};
  }
}

export async function capture({title='',text='',purpose='capture',files=[],fileRefs=[],courseCandidates=[],selectedCourseId=null,requestId}) {
  if(!identity)throw new CloudError('请先登录 Eric OS。','sign_in_required',401);
  if(!text.trim()&&!files.length&&!fileRefs.length)throw new CloudError('先写点内容，或选择要保存的文件。','empty_capture');
  if(files.length>10||files.some(file=>file.size>50_000_000||!file.size))throw new CloudError('一次最多 10 个文件，每个文件不超过 50 MB。','file_too_large');
  if(files.some(file=>!['application/pdf','image/jpeg','image/png','image/webp','image/heic','image/heif','text/plain'].includes(file.type||(/\.pdf$/i.test(file.name)?'application/pdf':''))))throw new CloudError('请选择 PDF、图片或文本文件。','unsupported_file');
  const id=requestId||crypto.randomUUID(),now=new Date().toISOString();
  const owner=identity.owner,deviceId=identity.device.id,persisted=[];
  await requestPersistentStorage();
  try{
    for(const file of files)persisted.push(fileReference(await storeFile(local,owner,file)));
    for(const reference of fileRefs)persisted.push(await requireFile(local,owner,reference));
  }catch{throw new CloudError('文件尚未完整保存在此设备，请保留当前页面并重试。','local_file_incomplete');}
  if(persisted.length>10||persisted.some(file=>!file.bytes||file.bytes>50_000_000||!['application/pdf','image/jpeg','image/png','image/webp','image/heic','image/heif','text/plain'].includes(file.mime)))throw new CloudError('一次最多 10 个文件，请选择 50 MB 以内的 PDF、图片或文本。','unsupported_file');
  const item={id,owner,deviceId,title:(title.trim()||persisted[0]?.filename||text.trim().slice(0,60)).slice(0,240),text,purpose,courseCandidates,
    ...(selectedCourseId?{selectedCourseId}:{}),
    files:persisted,
    createdAt:now,sharedAt:now,state:'local_pending',attempts:0,nextAttempt:0,server:null,error:null};
  if(identity?.owner!==owner||identity.device.id!==deviceId)throw new CloudError('登录设备发生了变化，文件仍保存在原账号的此设备中。','device_changed',409);
  try {await local.transaction('rw',local.outbox,local.drafts,async()=>{await local.outbox.add(item);await local.drafts.delete([owner,'capture']);});}catch(error) {window.dispatchEvent(new CustomEvent('eric-local-save-error',{detail:{type:error.name,innerType:error.inner?.name||null,code:error.code||null}}));throw new CloudError('设备未能保存这次收集。请保留当前内容，检查可用空间后重试。','local_save_failed');}
  changed();void pump();return item;
}

async function checksum(blob) {
  const state=sha256.create();
  for(let offset=0;offset<blob.size;offset+=UPLOAD_PART_BYTES){state.update(new Uint8Array(await blob.slice(offset,offset+UPLOAD_PART_BYTES).arrayBuffer()));await new Promise(resolve=>setTimeout(resolve,0));}
  return Array.from(state.digest(),b=>b.toString(16).padStart(2,'0')).join('');
}
async function sendCapture(item) {
  if(item.deviceId!==identity.device.id)throw new CloudError('这次收集来自此前的登录设备，原件仍保存在这里。请先在最近上传核对接收情况。','previous_device',409);
  for(const file of item.files) {
    if(!file.sha256)file.sha256=await checksum(file.blob);
    file.resource_id='capture:'+item.id+':'+file.sha256;file.part_count=Math.ceil(file.bytes/UPLOAD_PART_BYTES);
  }
  item.files=item.files.filter((file,index,files)=>files.findIndex(other=>other.sha256===file.sha256&&other.bytes===file.bytes)===index);
  await local.outbox.put({...item,state:'uploading',error:null});changed();
  const files=item.files.map(({blob,fileId,state,...file})=>file);
  let receipt=await request('/captures',{method:'POST',body:{id:item.id,title:item.title,text_content:item.text,purpose:item.purpose,files,course_candidates:item.courseCandidates,shared_at:item.sharedAt,
    ...(item.selectedCourseId?{selected_course_id:item.selectedCourseId}:{})}});
  if(receipt.state==='uploading') {
    for(const file of item.files) {
      const saved=await request('/files/'+encodeURIComponent(file.resource_id)+'/parts');
      if(saved.complete)continue;
      const accepted=new Set(saved.items.map(part=>part.part_index));
      for(let index=0;index<file.part_count;index++)if(!accepted.has(index)){
        let bytes;
        try{bytes=file.fileId?await readFilePart(local,item.owner,file,index):file.blob.slice(index*UPLOAD_PART_BYTES,Math.min(file.bytes,(index+1)*UPLOAD_PART_BYTES));}
        catch(error){if(error.message==='local_file_incomplete')throw new CloudError('本地文件尚不完整，请保留原文件并重新核对这次收集。','local_file_incomplete',409);throw new CloudError('设备正在恢复已保存的文件，会自动继续发送。');}
        await request('/files/'+encodeURIComponent(file.resource_id)+'/parts/'+index,{method:'PUT',bytes});
      }
      await request('/files/'+encodeURIComponent(file.resource_id)+'/finish',{method:'POST',body:{}});
    }
    receipt=await request('/captures/'+item.id+'/commit',{method:'POST',body:{}});
  }
  if(receipt.id!==item.id||!receipt.received_at||receipt.state==='uploading'||files.some(file=>!receipt.files?.some(saved=>saved.resource_id===file.resource_id&&saved.sha256===file.sha256&&saved.bytes===file.bytes)))throw new CloudError('尚未确认云端接收，原件会保留并继续发送。');
  // Keep local originals through the cloud receipt. Space can be released only after this point.
  await local.outbox.put({...item,state:receipt.state,server:receipt,error:null,confirmedAt:Date.now()});changed();
}

export async function saveCommand(path,body,{method='POST',target,expectedRevision}={}) {
  if(!identity)throw new CloudError('请先登录 Eric OS。','sign_in_required',401);
  const scope=readScope();
  if(!target||!Number.isSafeInteger(expectedRevision))throw new CloudError('请重新读取最新资料后再修改。','missing_revision',409);
  const pending=await local.commands.where('owner').equals(scope.owner).filter(item=>item.resourceKey===target&&['local_pending','pending','processing'].includes(item.state)).first();
  checkScope(scope);
  if(pending)throw new CloudError('上一项修改正在保存，完成后即可继续修改。','command_pending',409);
  const command={id:crypto.randomUUID(),owner:scope.owner,resourceKey:target,path,method,body,expectedRevision,state:'local_pending',createdAt:new Date().toISOString(),attempts:0,nextAttempt:0};
  await local.commands.add(command);checkScope(scope);changed();void pump();return {...command,queued:true};
}

async function acquireLease(token,owner) {
  const id='outbox:'+owner;
  return local.transaction('rw',local.leases,async()=>{const current=await local.leases.get(id);if(current&&current.until>Date.now()&&current.token!==token)return false;await local.leases.put({id,token,until:Date.now()+20_000});return true;});
}
export async function pump() {
  if(pumping||stopped||!identity||navigator.onLine===false)return;
  const owner=identity.owner;
  // The browser releases this lock immediately when its tab/PWA is closed.
  // A terminated sender must not leave a two-minute stale lock after reopening.
  if(navigator.locks?.request){
    try{return await navigator.locks.request('eric-outbox:'+owner,{mode:'exclusive',ifAvailable:true},lock=>lock?drain(owner,true):undefined);}
    catch(error){if(!['SecurityError','NotSupportedError'].includes(error.name)){window.dispatchEvent(new CustomEvent('eric-storage-unavailable',{detail:{type:error.name}}));return;}}
  }
  return drain(owner,false);
}
async function drain(owner,browserLocked) {
  if(pumping||stopped||identity?.owner!==owner)return;
  pumping=true;const token=crypto.randomUUID();
  let renewal;
  try {
    if(!browserLocked){if(!await acquireLease(token,owner))return;renewal=setInterval(()=>void acquireLease(token,owner).catch(()=>{}),5000);}
    const items=await local.outbox.where('owner').equals(owner).filter(item=>['local_pending','uploading'].includes(item.state)&&(!item.nextAttempt||item.nextAttempt<=Date.now())).sortBy('createdAt');
    for(const item of items) {
      if(stopped||identity?.owner!==owner)break;
      try {await sendCapture(item);}catch(error) {
        const retry=!error.status||error.status>=500||error.status===429;
        await local.outbox.update(item.id,{state:retry?'local_pending':'needs_confirmation',error:{message:error.message,code:error.code},attempts:item.attempts+1,nextAttempt:Date.now()+Math.min(300_000,2000*2**Math.min(item.attempts,7))});changed();
        if(error.status===401||error.status===403)break;
      }
    }
    const commands=await local.commands.where('owner').equals(owner).filter(item=>item.state==='local_pending'&&(!item.nextAttempt||item.nextAttempt<=Date.now())).sortBy('createdAt');
    for(const item of commands) {
      if(stopped||identity?.owner!==owner)break;
      try {
        const result=item.type==='progress'?await request(item.path,{method:'PUT',body:item.body}):await request('/commands',{method:'POST',body:{id:item.id,path:item.path,method:item.method,body:item.body,expected_revision:item.expectedRevision}});
        await local.commands.update(item.id,{state:item.type==='progress'?'completed':result.state,server:result,error:null});changed();
      } catch(error) {
        const retry=!error.status||error.status>=500||error.status===429;
        await local.commands.update(item.id,{state:retry?'local_pending':'needs_confirmation',error:{message:error.message,code:error.code},attempts:item.attempts+1,nextAttempt:Date.now()+Math.min(300_000,2000*2**Math.min(item.attempts,7))});changed();
      }
    }
  } catch(error) {
    window.dispatchEvent(new CustomEvent('eric-storage-unavailable',{detail:{type:error.name}}));
  } finally {
    pumping=false;
    clearInterval(renewal);
    await trimConfirmedCopies(owner).catch(()=>{});
    try {const lease=await local.leases.get('outbox:'+owner);if(lease?.token===token)await local.leases.delete('outbox:'+owner);}catch { /* A future lease expires without losing the outbox. */ }
  }
}

export async function progress(kind,id,expectedRevision,value) {
  if(!identity)throw new CloudError('请先登录 Eric OS。','sign_in_required',401);
  const resourceKey=kind+'_progress:'+id;
  if(await local.commands.where('owner').equals(identity.owner).filter(item=>item.resourceKey===resourceKey&&item.state==='local_pending').first())throw new CloudError('这项状态正在保存，请稍后继续。','command_pending',409);
  const requestId=crypto.randomUUID(),item={id:requestId,owner:identity.owner,type:'progress',resourceKey,path:'/progress/'+kind+'/'+encodeURIComponent(id),method:'PUT',
    body:{request_id:requestId,expected_revision:expectedRevision,value},state:'local_pending',createdAt:new Date().toISOString(),attempts:0,nextAttempt:0};
  await local.commands.add(item);changed();void pump();return item;
}
export async function fileUrl(resourceId,{force=false}={}) {
  const scope=readScope();
  if(!scope)throw new CloudError('请先登录 Eric OS。','sign_in_required',401);
  const key=scope.owner+':'+scope.deviceId+':'+resourceId,old=tickets.get(key);
  if(!force&&old?.until>Date.now())return old.url;
  const value=await request('/files/'+encodeURIComponent(resourceId)+'/ticket',{method:'POST',body:{},scope});
  checkScope(scope);
  tickets.set(key,{url:value.url,until:Date.now()+480_000});return value.url;
}
export async function editorDraft(form,{action='read',value,context}={}) {
  const scope=readScope();if(!scope){if(context)throw new CloudError('登录设备已变化，校订保留在原账号。','identity_changed',409);return null;}
  const token=JSON.stringify([scope.owner,scope.deviceId]);
  if(action==='context')return token;
  if(context&&context!==token)throw new CloudError('登录设备已变化，校订保留在原账号。','identity_changed',409);
  const key=scope.deviceId+':'+form;checkScope(scope);
  let result;
  if(action==='write')await local.drafts.put({owner:scope.owner,form:key,value,updatedAt:Date.now()});
  else if(action==='remove')await local.drafts.delete([scope.owner,key]);
  else result=await local.drafts.get([scope.owner,key]);
  checkScope(scope);return result?.value||null;
}
export async function captureLocalFile(captureId,index) {
  const owner=identity?.owner,item=await local.outbox.get(captureId);
  if(!owner||item?.owner!==owner)throw new CloudError('请使用保存这份文件的账号。','source_unavailable',403);
  const file=item.files[index];
  if(!file)throw new CloudError('没有找到这份本机副本。','source_unavailable',404);
  if(file.blob)return {blob:file.blob,filename:file.filename};
  try{
    await requireFile(local,owner,file);const parts=[];
    for(let part=0;part<Math.ceil(file.bytes/UPLOAD_PART_BYTES);part++)parts.push(await readFilePart(local,owner,file,part));
    if(identity?.owner!==owner)throw new Error('owner_changed');
    return {blob:new Blob(parts,{type:file.mime}),filename:file.filename};
  }catch{throw new CloudError('本机副本尚不完整，请保留原文件；已收到的内容可从云端打开。','local_file_incomplete',409);}
}
async function trimConfirmedCopies(owner) {
  const items=await local.outbox.where('owner').equals(owner).filter(item=>item.confirmedAt&&item.server?.received_at&&!['local_pending','uploading','needs_confirmation'].includes(item.state)).reverse().sortBy('createdAt');
  let retained=0;
  for(const item of items) {
    const bytes=item.files.reduce((sum,file)=>sum+(file.blob?.size||(file.fileId?file.bytes:0)),0);
    if(!bytes)continue;
    retained+=bytes;
    if(retained>50_000_000||Date.now()-item.confirmedAt>7*86400_000){
      await local.outbox.update(item.id,{files:item.files.map(({blob,fileId,...file})=>file),localCopiesReleasedAt:Date.now()});
      for(const file of item.files)if(file.fileId)await releaseUnusedFile(local,owner,file.fileId);
    }
  }
}
export function sourceResource(path) {
  const url=new URL(path,location.origin);let match;
  if(url.origin!==location.origin||!url.pathname.startsWith('/api/personal/'))return null;
  if((match=/^\/api\/personal\/cloud\/files\/(.+)$/.exec(url.pathname)))return decodeURIComponent(match[1]);
  if((match=/^\/api\/personal\/school\/documents\/([^/]+)\/pages\/(\d+)\/image$/.exec(url.pathname)))return 'page:'+decodeURIComponent(match[1])+':'+match[2]+':image';
  if((match=/^\/api\/personal\/attachments\/([^/]+)\/(original|image)$/.exec(url.pathname)))return url.searchParams.has('page')?'attachment-page:'+decodeURIComponent(match[1])+':'+url.searchParams.get('page')+':image':'attachment:'+decodeURIComponent(match[1])+':original';
  return null;
}

async function poll() {
  if(polling)return;
  polling=true;const generation=syncGeneration;
  clearTimeout(pollTimer);
  try {
    if(!identity||stopped)return;
    if(document.visibilityState==='visible'&&navigator.onLine!==false) {
      if(cursor===null){cursor=(await request('/system')).cursor;changed();}
      const change=await request('/changes?after='+cursor);cursor=Math.max(cursor,change.cursor);if(change.items.length)changed();
      const pending=await local.commands.where('owner').equals(identity.owner).filter(item=>['pending','processing'].includes(item.state)).toArray();
      for(const item of pending) {
        const server=await request('/commands/'+item.id);
        if(server.state!==item.state) {await local.commands.update(item.id,{state:server.state,server,error:server.error});changed();}
      }
      await pump();
      window.dispatchEvent(new Event('eric-clock-changed'));
    }
  } catch(error) {if(generation===syncGeneration&&(error.status===401||error.status===403))window.dispatchEvent(new CustomEvent('eric-auth-required',{detail:error}));}
  finally {polling=false;if(!stopped)pollTimer=setTimeout(poll,10_000);}
}
const recover=()=>{if(!stopped){void pump();void poll();}};
export function startSync(){if(!stopped)return;stopped=false;syncGeneration++;window.addEventListener('online',recover);document.addEventListener('visibilitychange',recover);void pump();void poll();}
export function stopSync(){stopped=true;syncGeneration++;clearTimeout(pollTimer);window.removeEventListener('online',recover);document.removeEventListener('visibilitychange',recover);}
export async function signOut({alreadyRevoked=false}={}){
  if(identity&&!alreadyRevoked){try{await request('/devices/revoke',{method:'POST',body:{id:identity.device.id}});}catch(error){if(error.status!==401)throw error;}}
  stopSync();identity=null;cursor=null;tickets.clear();await local.identity.delete('current');await auth.auth.signOut({scope:'local'});
}
