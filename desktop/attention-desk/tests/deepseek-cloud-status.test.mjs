import test from 'node:test';
import assert from 'node:assert/strict';
import {createHandler} from '../artifacts/deepseek-cloud-continuous-20261108/api-candidate/eric-app-api/handler.ts';
import {hash} from '../artifacts/deepseek-cloud-continuous-20261108/api-candidate/eric-app-api/store.ts';

const workspace='0d40643f-64b3-44d6-8244-f0a79a4d3f8a',processor='11f5743d-d900-45a1-a1ea-e64f1968e699';
const epoch='a4730c33-b6d2-4e29-a151-419971858135',token='ework_'+'a'.repeat(43),base='https://fixture.supabase.co';
async function rig({revoked=false,enabled=true,rows=[{publisher_epoch:epoch,source_generation:1}]}={}) {
 const calls=[],expected=await hash(token);
 const fetcher=async (input,options)=>{
  const url=new URL(input);calls.push({path:url.pathname,method:options?.method||'GET',query:url.searchParams});
  assert.equal(url.origin,base);
  assert.equal(options?.method||'GET','GET');
  if(url.pathname==='/rest/v1/eric_app_processors')return Response.json(!revoked&&url.searchParams.get('key_hash')==='eq.'+expected?[{id:processor,workspace_id:workspace}]:[]);
  if(url.pathname==='/rest/v1/eric_app_workspaces')return Response.json([{id:workspace}]);
  if(url.pathname==='/rest/v1/eric_app_workers'){
   assert.equal(url.searchParams.get('workspace_id'),'eq.'+workspace);
   assert.equal(url.searchParams.get('select'),'publisher_epoch,source_generation');
   return Response.json(rows);
  }
  throw Error('Unexpected metadata endpoint');
 };
 const options={base,secret:'synthetic',fetcher};
 if(enabled)Object.assign(options,{guardedWechatPublish:async()=>{},guardedWechatReconcile:async()=>{},guardedWechatSession:async()=>{}});
 const handler=createHandler(options);
 return {calls,request:(key=token)=>handler(new Request(base+'/functions/v1/eric-app-api/worker/wechat-status?workspace_id=other',
  {headers:key?{'X-Eric-Worker-Key':key}:{}}))};
}

test('status is authenticated, read-only and uses the actual worker workspace',async()=>{
 const r=await rig(),response=await r.request();
 assert.equal(response.status,200);
 assert.deepEqual(await response.json(),{protocol:'wechat-receipt-fence-v1',workspace_id:workspace,processor_id:processor,epoch,generation:1});
 assert.ok(r.calls.every(call=>call.method==='GET'));
});
test('anonymous and revoked clients cannot read publishing identity',async()=>{
 assert.equal((await (await rig()).request(null)).status,401);
 const r=await rig({revoked:true});assert.equal((await r.request()).status,401);
 assert.equal(r.calls.filter(call=>call.path.endsWith('/eric_app_workers')).length,0);
});
test('missing safety handlers cannot advertise readiness',async()=>{
 const r=await rig({enabled:false});assert.equal((await r.request()).status,503);
 assert.equal(r.calls.filter(call=>call.path.endsWith('/eric_app_workers')).length,0);
});
for(const rows of [[],[{publisher_epoch:null,source_generation:1}],[{publisher_epoch:epoch,source_generation:-1}],
 [{publisher_epoch:epoch,source_generation:null}],[{publisher_epoch:epoch,source_generation:true}],[{publisher_epoch:epoch,source_generation:'1'}],
 [{publisher_epoch:epoch,source_generation:9007199254740992}],[{publisher_epoch:epoch,source_generation:1},{publisher_epoch:epoch,source_generation:1}]]) {
 test('invalid or ambiguous publishing state fails closed: '+JSON.stringify(rows),async()=>{
  assert.equal((await (await rig({rows})).request()).status,503);
 });
}
