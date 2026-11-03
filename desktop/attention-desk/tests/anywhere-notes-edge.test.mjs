import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {build,transform} from 'esbuild';

const source=await readFile(new URL('../supabase/functions/eric-notes-ingest/handler.ts',import.meta.url),'utf8');
const compiled=await transform(source,{loader:'ts',format:'esm',target:'es2022'});
const {createHandler,hash}=await import('data:text/javascript;base64,'+Buffer.from(compiled.code).toString('base64'));
const BASE='https://fixture.supabase.co',INSTALL='11111111-1111-4111-8111-111111111111';
const UPLOAD='enote_'+'u'.repeat(43),SYNC='esync_'+'s'.repeat(43),SECRET='server-only-fixture-secret';
const PDF=new TextEncoder().encode('%PDF-1.7\nfixture transport, not real learning evidence\n%%EOF');
const root=BASE+'/functions/v1/eric-notes-ingest';

test('Public installer serves only the generic signed template; uploads still require their own capability',async()=>{
  let entry;
  const original=globalThis.Deno;
  globalThis.Deno={env:{get:name=>name==='SUPABASE_URL'?BASE:name==='SUPABASE_SERVICE_ROLE_KEY'?SECRET:undefined},serve:handler=>{entry=handler;}};
  try {
    const bundled=await build({entryPoints:[fileURLToPath(new URL('../supabase/functions/eric-notes-ingest/index.ts',import.meta.url))],bundle:true,write:false,format:'esm',platform:'neutral',target:'es2022'});
    await import('data:text/javascript;base64,'+Buffer.from(bundled.outputFiles[0].contents).toString('base64'));
  }finally{globalThis.Deno=original;}
  const res=await entry(new Request(root+'/shortcut'));
  assert.equal(res.status,200);assert.equal(res.headers.get('content-type'),'application/x-apple-shortcut');
  const data=new Uint8Array(await res.arrayBuffer());
  assert.equal(new TextDecoder().decode(data.subarray(0,4)),'AEA1');
  assert.equal(await hash(data),'fc1530b9bc4e5814997ffad7d0aa49d35bd2df06f668c065aeabe9d6934f88be');
  const cleanup=await entry(new Request(root+'/shortcut-cleanup'));
  assert.equal(cleanup.status,200);
  assert.equal(await hash(new Uint8Array(await cleanup.arrayBuffer())),'e4d47d6e2ce8f7c00c85ce148ab868f4a702ffe43278f2ea5759bbaa22052321');
  assert.match(res.headers.get('content-disposition'),/attachment/);
  assert.equal((await entry(new Request(root+'/upload',{method:'POST',body:PDF}))).status,401);
  assert.equal((await entry(new Request(root+'/pending'))).status,401);
});

async function rig(options={}){
  const rows=[],objects=new Map(),devices=[
    {id:'22222222-2222-4222-8222-222222222222',installation_id:INSTALL,role:'upload',key_hash:await hash(UPLOAD),revoked_at:null},
    {id:'33333333-3333-4333-8333-333333333333',installation_id:INSTALL,role:'sync',key_hash:await hash(SYNC),revoked_at:null},
  ];
  const calls=[],fail={storage:false,commit:false,cleanup:false};let now=new Date('2026-10-29T01:00:00Z');
  function selected(items,url){return items.filter(row=>[...url.searchParams].every(([key,value])=>{
    if(['select','order','limit'].includes(key))return true;
    if(value==='is.null')return row[key]==null;
    if(value.startsWith('eq.'))return String(row[key])===value.slice(3);
    if(value.startsWith('lt.'))return Date.parse(row[key])<Date.parse(value.slice(3));
    return false;
  })).slice(0,Number(url.searchParams.get('limit')||100));}
  async function fetcher(input,init={}){
    const url=new URL(input),method=init.method||'GET',headers=new Headers(init.headers);
    assert.equal(url.origin,BASE);assert.equal(headers.get('apikey'),SECRET);
    calls.push({method,path:url.pathname});
    const body=init.body&&typeof init.body==='string'?JSON.parse(init.body):null;
    if(url.pathname.endsWith('/rpc/eric_notes_authorize')){
      const device=devices.find(d=>d.key_hash===body.p_hash&&d.role===body.p_role&&!d.revoked_at);
      return device?Response.json([{id:device.id,installation_id:device.installation_id}]):Response.json({code:'28000'},{status:403});
    }
    if(url.pathname.endsWith('/rpc/eric_notes_prepare')){
      let row=rows.find(r=>r.sha256===body.p_sha256&&r.installation_id===body.p_installation);
      if(!row){row={id:crypto.randomUUID(),installation_id:body.p_installation,device_id:body.p_device,sha256:body.p_sha256,
        bytes:body.p_bytes,filename:body.p_filename,shared_at:body.p_shared_at,object_path:body.p_installation+'/'+body.p_sha256+'.pdf',
        state:'uploading',created_at:now.toISOString(),payload_deleted_at:null};rows.push(row);}
      return Response.json([row]);
    }
    if(url.pathname.startsWith('/rest/v1/')){
      const table=url.pathname.endsWith('eric_notes_devices')?devices:rows;
      if(method==='GET')return Response.json(selected(table,url));
      if(method==='POST'){const row={id:crypto.randomUUID(),created_at:now.toISOString(),revoked_at:null,...body};table.push(row);return Response.json([row]);}
      if(method==='PATCH'){
        if(fail.commit&&body.state==='pending')return Response.json({message:'fixture failed commit'},{status:503});
        const matches=selected(table,url);for(const row of matches)Object.assign(row,body);return Response.json(matches);
      }
    }
    const sign='/storage/v1/object/sign/eric-notes-transit/';
    if(url.pathname.startsWith(sign)){
      const key=url.pathname.slice(sign.length);
      return objects.has(key)?Response.json({signedURL:'/object/sign/eric-notes-transit/'+key+'?token=fixture-signed'}):Response.json({error:'missing'},{status:404});
    }
    if(method==='DELETE'){
      if(fail.cleanup)return new Response('',{status:503});
      for(const key of body.prefixes){assert.equal(rows.find(r=>r.object_path===key)?.state,'acked');objects.delete(key);}
      return Response.json([]);
    }
    const prefix='/storage/v1/object/eric-notes-transit/';
    if(url.pathname.startsWith(prefix)&&method==='POST'){
      if(fail.storage)return new Response('',{status:503});
      const key=url.pathname.slice(prefix.length);
      if(objects.has(key))return new Response('',{status:409});
      objects.set(key,new Uint8Array(init.body));return Response.json({Key:key});
    }
    throw Error('Unexpected fixture request: '+url.pathname);
  }
  const handler=createHandler({base:BASE,secret:SECRET,fetcher,clock:()=>now,...options});
  const request=(path,{method='GET',key=SYNC,body}={})=>handler(new Request(root+path,{
    method,headers:key.startsWith('enote_')?{'X-Eric-Notes-Key':key}:{'X-Eric-Sync-Key':key},...(body===undefined?{}:{body:typeof body==='object'&&!(body instanceof Uint8Array)?JSON.stringify(body):body}),
  }));
  const upload=()=>request('/upload',{method:'POST',key:UPLOAD,body:PDF});
  return{rows,objects,devices,calls,fail,request,upload,advance:ms=>{now=new Date(now.getTime()+ms);}};
}

test('received follows durable storage and pending metadata; response reveals no admin credential',async()=>{
  const r=await rig(),res=await r.upload(),value=await res.json();
  assert.equal(res.status,202);assert.equal(value.saved,true);assert.equal(value.message,'已接收');
  assert.equal(r.rows[0].state,'pending');assert.equal(r.objects.size,1);
  assert.ok(!JSON.stringify(value).includes(SECRET));
  const storage=r.calls.findIndex(c=>c.path.includes('/storage/v1/object/eric-notes-transit/'));
  assert.ok(r.calls.slice(storage+1).some(c=>c.method==='PATCH'));
});

test('app archive failure prevents a saved confirmation and transit deletion; retries retain one submission',async()=>{
  let unavailable=true;const archived=[];
  const r=await rig({archive:async value=>{archived.push(value.row.id);if(unavailable)throw new Error('Synthetic archive outage');}});
  assert.equal((await r.upload()).status,503);assert.equal(r.rows[0].state,'uploading');assert.equal(r.objects.size,1);
  unavailable=false;assert.equal((await r.upload()).status,202);const row=r.rows[0];assert.equal(row.state,'pending');assert.equal(r.rows.length,1);
  unavailable=true;const ack={id:row.id,sha256:row.sha256,bytes:row.bytes,local_receipt_id:crypto.randomUUID()};
  assert.equal((await r.request('/ack',{method:'POST',body:ack})).status,503);assert.equal(row.state,'pending');assert.equal(r.objects.size,1);
  unavailable=false;assert.equal((await r.request('/ack',{method:'POST',body:ack})).status,200);assert.equal(row.state,'acked');assert.equal(r.objects.size,0);
  assert.equal(new Set(archived).size,1);
});

test('failed storage never returns received and retains an uncommitted reservation',async()=>{
  const r=await rig();r.fail.storage=true;
  const res=await r.upload();assert.equal(res.status,503);assert.equal((await res.json()).received,false);
  assert.equal(r.objects.size,0);assert.equal(r.rows[0].state,'uploading');
});

test('failure after object save does not lose file; later sync recovers committed object',async()=>{
  const r=await rig();r.fail.commit=true;
  assert.equal((await r.upload()).status,503);assert.equal(r.objects.size,1);assert.equal(r.rows[0].state,'uploading');
  r.fail.commit=false;r.advance(180000);
  const pending=await(await r.request('/pending')).json();
  assert.equal(r.rows[0].state,'pending');assert.equal(pending.submissions.length,1);
  assert.ok(pending.submissions[0].download_url.startsWith(BASE+'/storage/v1/object/sign/'));
});

test('concurrent identical shares create one transport submission and one immutable object',async()=>{
  const r=await rig(),results=await Promise.all([r.upload(),r.upload()]);
  assert.ok(results.every(x=>[200,202].includes(x.status)));
  assert.equal(r.rows.length,1);assert.equal(r.objects.size,1);
});

test('upload capability cannot list, acknowledge or create devices',async()=>{
  const r=await rig();
  for(const path of ['/pending','/devices'])assert.equal((await r.request(path,{key:UPLOAD})).status,401);
  assert.equal((await r.request('/ack',{method:'POST',key:UPLOAD,body:{}})).status,401);
  assert.equal((await r.request('/upload',{method:'POST',key:SYNC,body:PDF})).status,401);
  assert.equal(r.objects.size,0);
});

test('hash mismatch cannot ack or delete; committed ack precedes cleanup; duplicate never reuploads',async()=>{
  const r=await rig(),receipt=await(await r.upload()).json(),row=r.rows[0];
  const ack={id:receipt.receipt_id,sha256:'0'.repeat(64),bytes:row.bytes,local_receipt_id:crypto.randomUUID()};
  assert.equal((await r.request('/ack',{method:'POST',body:ack})).status,409);assert.equal(r.objects.size,1);
  ack.sha256=row.sha256;
  assert.equal((await r.request('/ack',{method:'POST',body:ack})).status,200);
  assert.equal(row.state,'acked');assert.equal(row.filename,'');assert.equal(row.shared_at,null);assert.equal(r.objects.size,0);
  assert.equal((await r.request('/ack',{method:'POST',body:ack})).status,200);
  const repeated=await(await r.upload()).json();assert.equal(repeated.duplicate,true);assert.equal(r.objects.size,0);assert.equal(r.rows.length,1);
});

test('A historical committed ACK stays idempotent when its old transit bytes are already gone',async()=>{
  let archives=0;const r=await rig({archive:async()=>{archives++;throw new Error('Historical transit already deleted');}});
  const row={id:crypto.randomUUID(),installation_id:INSTALL,device_id:'22222222-2222-4222-8222-222222222222',sha256:await hash(PDF),bytes:PDF.length,state:'acked',filename:'',shared_at:null,payload_deleted_at:'2026-10-28T00:00:00Z'};
  r.rows.push(row);
  const ack={id:row.id,sha256:row.sha256,bytes:row.bytes,local_receipt_id:crypto.randomUUID()};
  for(let i=0;i<2;i++){const response=await r.request('/ack',{method:'POST',body:ack});assert.equal(response.status,200);assert.equal((await response.json()).acked,true);}
  assert.equal(archives,0);assert.equal(r.objects.size,0);assert.equal(row.state,'acked');
});

test('cleanup failure preserves acknowledged payload and next sync retries safely',async()=>{
  const r=await rig();await r.upload();const row=r.rows[0];r.fail.cleanup=true;
  const ack=await r.request('/ack',{method:'POST',body:{id:row.id,sha256:row.sha256,bytes:row.bytes,local_receipt_id:crypto.randomUUID()}});
  assert.equal((await ack.json()).acked,true);assert.equal(row.state,'acked');assert.equal(r.objects.size,1);
  r.fail.cleanup=false;await r.request('/pending');assert.equal(r.objects.size,0);assert.ok(row.payload_deleted_at);
});

test('device revocation takes effect before any new upload body is stored',async()=>{
  const r=await rig();const added=await(await r.request('/devices',{method:'POST',body:{}})).json();
  assert.match(added.upload_key,/^enote_[A-Za-z0-9_-]{43}$/);
  await r.request('/devices/revoke',{method:'POST',body:{id:added.device_id}});
  assert.equal((await r.request('/upload',{method:'POST',key:added.upload_key,body:PDF})).status,401);assert.equal(r.objects.size,0);
});

test('non-PDF upload never creates a submission',async()=>{
  const r=await rig();const res=await r.request('/upload',{method:'POST',key:UPLOAD,body:new TextEncoder().encode('not a PDF')});
  assert.equal(res.status,415);assert.equal(r.rows.length,0);assert.equal(r.objects.size,0);
});
