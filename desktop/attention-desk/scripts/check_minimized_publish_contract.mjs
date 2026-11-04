// Offline only: the downloaded API19 handler receives the exact reviewed batch.
// No real credentials, database, network calls or recovery-control exceptions.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';

process.on('uncaughtException',error=>{console.error(JSON.stringify({PASS:false,error:error.message,code:error.code}));process.exit(1);});

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const folder=join(root,'artifacts/p0-20261104'),review=join(folder,'final-scope-review');
const bundle=JSON.parse(await readFile(join(folder,'wechat-minimized-projection-preview.json'),'utf8'));
const deployed=JSON.parse(await readFile(join(review,'deployed-api-source.json'),'utf8'));
assert.equal(deployed.version,19);
for(const file of deployed.files.filter(f=>/\/(handler|store|files|vision|views)\.ts$/.test(f.name))){
  const saved=await readFile(join(review,'deployed-api19',file.name.split('/').at(-1)),'utf8');
  const digest=text=>createHash('sha256').update(text.trimEnd()).digest('hex');
  assert.equal(digest(saved),digest(file.content),file.name+' differs beyond trailing whitespace');
}
const compiled=await build({entryPoints:[join(review,'deployed-api19/handler.ts')],bundle:true,write:false,format:'esm',platform:'neutral',target:'es2022'});
const {createHandler}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].contents).toString('base64'));
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
assert.equal(bundle.preview_only,true);assert.equal(bundle.upload_authorized,false);
assert.deepEqual(bundle.resources,[]);assert.equal(bundle.records.length,8);
assert.equal(new Set(bundle.records.map(r=>r.kind+':'+r.id)).size,8);
for(const record of bundle.records){
  assert(['raw_item','inbox_item','inbox','raw_review'].includes(record.kind));
  assert.equal(createHash('sha256').update(canonical(record.payload)).digest('hex'),record.source_hash);
}
const BASE='https://fixture.supabase.co',W='11111111-1111-4111-8111-111111111111';
const P='22222222-2222-4222-8222-222222222222',EPOCH='33333333-3333-4333-8333-333333333333';
const KEY='ework_'+'a'.repeat(43),SECRET='synthetic-server-only',NOW=new Date('2026-11-04T08:00:00Z');
const keyHash=createHash('sha256').update(KEY).digest('hex'),calls=[],checks=[];
let active=true,publishFailure=null;
globalThis.fetch=()=>{throw new Error('Network is forbidden in this offline contract check');};
const fetcher=async(input,init={})=>{
  const url=new URL(input),method=init.method||'GET';
  assert.equal(url.origin,BASE);assert.equal(new Headers(init.headers).get('apikey'),SECRET);
  const body=init.body===undefined?null:JSON.parse(init.body);
  calls.push({method,path:url.pathname,query:url.search,body});
  if(method==='GET'&&url.pathname==='/rest/v1/eric_app_processors'){
    assert.equal(url.searchParams.get('key_hash'),'eq.'+keyHash);
    assert.equal(url.searchParams.get('revoked_at'),'is.null');
    return Response.json(active?[{id:P,workspace_id:W}]:[]);
  }
  if(method==='GET'&&url.pathname==='/rest/v1/eric_app_workspaces'){
    assert.equal(url.searchParams.get('id'),'eq.'+W);return Response.json([{id:W}]);
  }
  if(method==='POST'&&url.pathname==='/rest/v1/rpc/eric_app_begin_publish'){
    assert.deepEqual(body,{p_workspace:W,p_epoch:EPOCH});return Response.json(null);
  }
  if(method==='PATCH'&&url.pathname==='/rest/v1/eric_app_processors'){
    assert.equal(url.searchParams.get('id'),'eq.'+P);
    assert.deepEqual(body,{last_seen_at:NOW.toISOString()});return Response.json([{id:P}]);
  }
  if(method==='POST'&&url.pathname==='/rest/v1/rpc/eric_app_publish_records'){
    assert.deepEqual(body,{p_workspace:W,p_records:bundle.records,p_epoch:EPOCH,p_generation:1});
    if(publishFailure)return Response.json({message:publishFailure},{status:500});
    return Response.json(73); // Protocol fixture, not evidence of a database commit.
  }
  throw new Error('Unexpected offline request '+method+' '+url.pathname);
};
const handler=createHandler({base:BASE,secret:SECRET,fetcher,clock:()=>NOW});
const request=(path,body,key=KEY)=>handler(new Request(BASE+'/functions/v1/eric-app-api'+path,{
  method:'POST',headers:{'X-Eric-Worker-Key':key,'Content-Type':'application/json'},body:JSON.stringify(body)}));
const publish={epoch:EPOCH,generation:1,records:bundle.records};
assert.equal((await request('/worker/hello',{epoch:EPOCH})).status,200);
const accepted=await request('/worker/publish',publish);
assert.equal(accepted.status,200);assert.deepEqual(await accepted.json(),{revision:73});
checks.push('Exact eight-record body reaches the original publish RPC unchanged');
const successCalls=structuredClone(calls);
assert.deepEqual(successCalls.filter(c=>c.method!=='GET').map(c=>[c.method,c.path]),[
  ['POST','/rest/v1/rpc/eric_app_begin_publish'],['PATCH','/rest/v1/eric_app_processors'],
  ['POST','/rest/v1/rpc/eric_app_publish_records']]);
checks.push('Hello also updates only the existing processor last_seen_at field');
for(const [name,body,key,enabled,status] of [
  ['invalid worker',publish,'invalid',true,401],['revoked worker',publish,KEY,false,401],
  ['invalid epoch',{...publish,epoch:'invalid'},KEY,true,400],
  ['invalid generation',{...publish,generation:-1},KEY,true,400]
]){
  calls.length=0;active=enabled;
  const response=await request('/worker/publish',body,key);assert.equal(response.status,status,name);
  assert.equal(calls.filter(c=>c.method!=='GET').length,0,name+' must not reach a write');checks.push(name+' rejected before write');
}
active=true;publishFailure='eric_stale_projection';calls.length=0;
const stale=await request('/worker/publish',publish);assert.equal(stale.status,409);
assert.equal((await stale.json()).detail.code,'eric_stale_projection');
assert.equal(calls.filter(c=>c.method!=='GET').length,1);checks.push('Stale publisher returns conflict without retry');
publishFailure='unconfirmed_result';calls.length=0;
assert.equal((await request('/worker/publish',publish)).status,503);
assert.equal(calls.filter(c=>c.method!=='GET').length,1);checks.push('Unknown result remains an error without retry');
const result={PASS:true,offline:true,api_version:19,bundle_hash:bundle.bundle_hash,
  raw_count:2,record_count:8,attachment_count:0,network_requests:0,cloud_writes:0,model_requests:0,
  upload_authorized:false,checks,synthetic_success_calls:successCalls.map(({method,path})=>({method,path})),
  write_tables_after_future_authorization:['eric_app_records','eric_app_changes','eric_app_workers','eric_app_processors'],
  limit:'API protocol only. RPC responses are fixtures; no real SQL, idempotency, upload, owner-session or cloud Inbox acceptance is claimed.'};
await writeFile(join(review,'minimized-publish-contract.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify(result));
