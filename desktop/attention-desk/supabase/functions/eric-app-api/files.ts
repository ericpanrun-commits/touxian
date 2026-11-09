import { sha256 } from '@noble/hashes/sha2.js';
import { AppFault, BUCKET, CloudStore, ID, SHA, UUID, hash, stable } from './store.ts';

const encoder = new TextEncoder();
function base64(bytes: Uint8Array): string { return btoa(String.fromCharCode(...bytes)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,''); }
function unbase64(value: string): Uint8Array {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) throw new Error('invalid ticket');
  return Uint8Array.from(atob(value.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));
}
async function key(secret: string) {
  return crypto.subtle.importKey('raw',encoder.encode('eric-app-file-v1:'+secret),{name:'HMAC',hash:'SHA-256'},false,['sign','verify']);
}
export async function ticket(store: CloudStore,principal: any,id: string): Promise<string> {
  if (!ID.test(id)) throw new AppFault(400,'invalid_file','文件链接无效。');
  const payload = base64(encoder.encode(stable({w:principal.workspace.id,d:principal.device.id,r:id,e:Math.floor(store.clock().getTime()/1000)+600})));
  const signature = await crypto.subtle.sign('HMAC',await key(store.secret),encoder.encode(payload));
  return payload+'.'+base64(new Uint8Array(signature));
}
export async function verifyTicket(store: CloudStore,value: string,id: string): Promise<any> {
  try {
    if (value.length>3000) throw new Error('too long');
    const [payload,signature,...extra] = value.split('.');
    if (extra.length || !await crypto.subtle.verify('HMAC',await key(store.secret),unbase64(signature) as BufferSource,encoder.encode(payload))) throw new Error('signature');
    const data = JSON.parse(new TextDecoder().decode(unbase64(payload)));
    if (!UUID.test(data.w) || !UUID.test(data.d) || data.r!==id || !Number.isSafeInteger(data.e) || data.e<=store.clock().getTime()/1000 || data.e>store.clock().getTime()/1000+610) throw new Error('expired');
    const devices = await store.db('eric_app_devices?workspace_id=eq.'+data.w+'&id=eq.'+data.d+'&revoked_at=is.null&select=id&limit=1');
    if (!devices.length) throw new Error('revoked');
    return data;
  } catch { throw new AppFault(401,'file_link_expired','这个文件链接已过期，请在 Eric OS 中重新打开。'); }
}

export function manifest(input: any,maxBytes=512_000_000,trustedPublisher=false): any {
  if (!input || !ID.test(input.id || '') || !SHA.test(input.sha256 || '') || !Number.isSafeInteger(input.bytes) || input.bytes<1 || input.bytes>maxBytes ||
      !Number.isSafeInteger(input.part_count) || input.part_count<1 || input.part_count>128 || input.part_count>input.bytes ||
      ![Math.ceil(input.bytes/4_000_000),Math.ceil(input.bytes/16_000_000)].includes(input.part_count) ||
      typeof input.filename!=='string' || !input.filename.trim() || input.filename.length>240 || /[\x00-\x1f\x7f/\\]/.test(input.filename) ||
      !(trustedPublisher?typeof input.mime==='string'&&/^[a-z0-9!#$&^_.+-]+\/[a-z0-9!#$&^_.+-]+$/.test(input.mime)&&input.mime.length<=100:
        ['application/pdf','image/jpeg','image/png','image/webp','image/heic','image/heif','text/plain'].includes(input.mime))) {
    throw new AppFault(400,'invalid_file','请使用 PDF、图片或文本文件，并保留完整文件名。');
  }
  return {id:input.id,sha256:input.sha256,bytes:input.bytes,part_count:input.part_count,filename:input.filename,mime:input.mime};
}
export async function parts(store: CloudStore,workspace: string,resource: any): Promise<any[]> {
  const rows = await store.db('eric_app_resource_parts?workspace_id=eq.'+workspace+'&resource_id=eq.'+encodeURIComponent(resource.id)+'&order=part_index.asc&limit=128');
  if (rows.length!==resource.part_count || rows.some((p: any,i: number)=>p.part_index!==i || !SHA.test(p.sha256) || p.object_path!==workspace+'/'+p.sha256) ||
      rows.reduce((sum: number,p: any)=>sum+p.bytes,0)!==resource.bytes) throw new AppFault(409,'file_incomplete','文件尚未完整接收，会自动继续发送。');
  return rows;
}

// A read-only PDF view consists of existing private original parts and a small,
// hash-verified standard PDF incremental index. It never creates another book.
export async function readableResource(store: CloudStore,workspace: string,id: string): Promise<{resource:any;original:any;tail:Uint8Array|null}> {
  if(!id.startsWith('pdf-view:')) {const original=await store.resource(workspace,id);return {resource:original,original,tail:null};}
  const match=/^pdf-view:([A-Za-z0-9_.:-]+):([a-f0-9]{64})$/.exec(id);
  const fail=()=>new AppFault(409,'reading_view_unavailable','这份教材的阅读版本正在准备，请稍后重新打开。');
  if(!match)throw fail();
  const [viewRow,documentRow]=await Promise.all([store.one(workspace,'pdf_reading',id.slice(9)),store.one(workspace,'document',match[1])]);
  const view=viewRow?.payload,document=documentRow?.payload;
  if(!view||!document||view.id!==id.slice(9)||view.document_id!==document.id||view.version!=='pdf-reading-tree-1'||
     document.reading_resource_id!==id||view.source_resource_id!=='attachment:'+document.attachment_id+':original'||
     view.source_sha256!==document.sha256||view.page_count!==document.page_count||!SHA.test(view.sha256||'')||
     view.tail_sha256!==match[2]||!Number.isSafeInteger(view.tail_bytes)||view.tail_bytes<1||view.tail_bytes>1_000_000||
     typeof view.tail_base64!=='string'||view.tail_base64.length>1_333_336)throw fail();
  let tail:Uint8Array;
  try {tail=Uint8Array.from(atob(view.tail_base64),c=>c.charCodeAt(0));}catch{throw fail();}
  if(tail.length!==view.tail_bytes||await hash(tail)!==view.tail_sha256)throw fail();
  const original=await store.resource(workspace,view.source_resource_id);
  if(original.mime!=='application/pdf'||original.sha256!==view.source_sha256||original.bytes!==view.source_bytes||view.bytes!==original.bytes+tail.length)throw fail();
  return {resource:{id,filename:original.filename,mime:original.mime,sha256:view.sha256,bytes:view.bytes,complete:true,
    source_resource_id:original.id,source_sha256:original.sha256,source_bytes:original.bytes,page_count:view.page_count},original,tail};
}
export async function verifyResource(store: CloudStore,workspace: string,resource: any): Promise<void> {
  const digest = sha256.create();
  let total=0;
  for (const part of await parts(store,workspace,resource)) {
    const response = await store.fetch('/storage/v1/object/'+BUCKET+'/'+part.object_path);
    if (!response.ok || !response.body) throw new AppFault(503,'file_incomplete','文件尚未确认保存，会自动继续发送。');
    const reader = response.body.getReader();
    let bytes=0;
    try { for (;;) {const chunk=await reader.read();if (chunk.done) break;bytes+=chunk.value.length;digest.update(chunk.value);} }
    finally {reader.releaseLock();}
    if (bytes!==part.bytes) throw new AppFault(503,'file_incomplete','文件尚未完整接收，会自动继续发送。');
    total+=bytes;
  }
  const actual = Array.from(digest.digest(),b=>b.toString(16).padStart(2,'0')).join('');
  if (actual!==resource.sha256 || total!==resource.bytes) throw new AppFault(409,'file_changed','文件校验没有通过，原文件仍保留在设备中，请重新发送。');
}

export function byteRange(value: string|null,total: number): [number,number]|null {
  if (!value) return null;
  const match=/^bytes=(\d*)-(\d*)$/.exec(value);
  if (!match || (!match[1]&&!match[2])) throw new AppFault(416,'invalid_range','文件读取范围无效，请重新打开。');
  const begin=match[1]?Number(match[1]):Math.max(0,total-Number(match[2]));
  const end=match[1]?(match[2]?Math.min(Number(match[2]),total-1):total-1):total-1;
  if (!Number.isSafeInteger(begin) || !Number.isSafeInteger(end) || begin<0 || begin>end || begin>=total) throw new AppFault(416,'invalid_range','文件读取范围无效，请重新打开。');
  return [begin,end];
}

// 50 MiB. Worker sync of a larger object must send Range; ticket and browser reads stay whole-file.
export const FULL_DOWNLOAD_LIMIT = 50 * 1024 * 1024;
export const FILE_CACHE_CONTROL = 'private, max-age=86400, immutable';

export function quotedEtag(sha256: string): string | null {
  return SHA.test(sha256 || '') ? `"${sha256}"` : null;
}

export function ifNoneMatch(header: string | null, etag: string): boolean {
  if (!header || !etag.startsWith('"') || !etag.endsWith('"')) return false;
  const wanted = etag.slice(1, -1);
  return header.split(',').some(raw => {
    const token = raw.trim();
    if (token === '*') return true;
    const bare = token.replace(/^W\//i, '').trim();
    const value = bare.startsWith('"') && bare.endsWith('"') ? bare.slice(1, -1) : bare;
    return value === wanted;
  });
}

function notModified(etag: string): Response {
  return new Response(null, {status: 304, headers: {ETag: etag, 'Cache-Control': FILE_CACHE_CONTROL, 'Accept-Ranges': 'bytes'}});
}

export async function fileResponse(store: CloudStore,req: Request,workspace: string,id: string,workerDownload=false): Promise<Response> {
  const {resource,original,tail}=await readableResource(store,workspace,id);
  const etag=quotedEtag(resource.sha256);
  if (etag && ifNoneMatch(req.headers.get('If-None-Match'), etag)) return notModified(etag);
  const rangeHeader=req.headers.get('Range');
  // Only the worker publisher is refused. Ticket links (SourceLink) and vision keep unranged reads.
  if (workerDownload && resource.bytes > FULL_DOWNLOAD_LIMIT && !(rangeHeader && rangeHeader.trim())) {
    throw new AppFault(416,'range_required','大文件必须按范围读取。Large files must be read in ranges.');
  }
  const manifest=await parts(store,workspace,original);
  if(tail)manifest.push({bytes:tail.length,inline:tail});
  const range=byteRange(rangeHeader,resource.bytes),[begin,end]=range||[0,resource.bytes-1];
  let offset=0;
  const selected: any[]=[];
  for (const part of manifest) {
    if (offset<=end && offset+part.bytes>begin) selected.push({...part,begin:Math.max(0,begin-offset),end:Math.min(part.bytes-1,end-offset)});
    offset+=part.bytes;
  }
  let buffered: Uint8Array|null=null,position=0,partIndex=0;
  const stream=new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        for (;;) {
          if (!buffered) {
            if (partIndex===selected.length) {controller.close();return;}
            const part=selected[partIndex++];
            if(part.inline) {buffered=part.inline.subarray(part.begin,part.end+1);position=0;continue;}
            const response=await store.fetch('/storage/v1/object/'+BUCKET+'/'+part.object_path,{headers:{Range:'bytes='+part.begin+'-'+part.end}});
            if (!response.ok || !response.body || (response.status!==200 && response.status!==206)) throw new Error('source unavailable');
            if (response.status===206 && response.headers.get('Content-Range')!=='bytes '+part.begin+'-'+part.end+'/'+part.bytes) {await response.body.cancel();throw new Error('unexpected range');}
            // Finish the bounded Storage read before serving a slow phone. The
            // origin's 30s request timeout must not time out the downstream reader.
            const expected=response.status===206?part.end-part.begin+1:part.bytes;
            const bytes=new Uint8Array(expected),reader=response.body.getReader();let received=0;
            try {for(;;){const chunk=await reader.read();if(chunk.done)break;if(received+chunk.value.length>expected){await reader.cancel();throw new Error('oversized part');}bytes.set(chunk.value,received);received+=chunk.value.length;}}
            finally{reader.releaseLock();}
            if(received!==expected)throw new Error('truncated file');
            if(response.status===200&&await hash(bytes)!==part.sha256)throw new Error('changed part');
            buffered=response.status===206?bytes:bytes.subarray(part.begin,part.end+1);position=0;
          }
          const output=buffered.slice(position,position+65536);position+=output.length;
          if(position===buffered.length)buffered=null;
          controller.enqueue(output);return;
        }
      } catch (error) {buffered=null;controller.error(error);}
    },
    cancel() {buffered=null;},
  });
  const safeInline=['application/pdf','image/jpeg','image/png','image/webp','image/heic','image/heif','text/plain'].includes(resource.mime);
  const headers: Record<string,string>={'Content-Type':safeInline?resource.mime:'application/octet-stream','Content-Length':String(end-begin+1),'Accept-Ranges':'bytes',
    'Content-Disposition':(!safeInline||new URL(req.url).searchParams.get('download')==='1'?'attachment':'inline')+"; filename*=UTF-8''"+encodeURIComponent(resource.filename),
    'X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; sandbox",'Referrer-Policy':'no-referrer','Cache-Control':FILE_CACHE_CONTROL};
  if(etag) headers.ETag=etag;
  if(range) headers['Content-Range']='bytes '+begin+'-'+end+'/'+resource.bytes;
  if (req.method==='HEAD') {await stream.cancel();return new Response(null,{status:range?206:200,headers});}
  return new Response(stream,{status:range?206:200,headers});
}


