import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
import {registerHooks} from 'node:module';

// The assembled API lives outside node_modules; reuse an explicitly selected
// installed dependency directory without downloading or altering that tree.
if(process.env.ERIC_TEST_DEPENDENCIES)registerHooks({resolve(specifier,context,next){
  return next(specifier.startsWith('@noble/hashes/')?pathToFileURL(resolve(process.env.ERIC_TEST_DEPENDENCIES,specifier)).href:specifier,context);
}});

globalThis.fetch=async()=>{throw new Error('Real network forbidden in notification automation regressions');};
const root=process.env.ERIC_AUTOMATION_API_ROOT;
if(!root){test('Pinned v33 API candidate is required for automation protocol tests',{skip:'Run with ERIC_AUTOMATION_API_ROOT after prepare-notification-release.py'},()=>{});}
else {
const moduleAt=name=>import(pathToFileURL(resolve(root,name)).href);
const {createAutomaticTaskEndpoints,validateAutomaticTasks,automationScope}=await moduleAt('guarded_wechat_actions.ts');
const {validateWechatBundle,LOCK_SQL,PROCESSOR_SQL,WORKER_SQL,EXISTING_SQL,INSERT_SQL}=await moduleAt('guarded_wechat_publish.ts');
const {RECONCILE_EXISTING_SQL}=await moduleAt('guarded_wechat_reconcile.ts');
const {hash,stable}=await moduleAt('store.ts');
const {Views,deadlineState}=await moduleAt('views.ts');
const {createHandler}=await moduleAt('handler.ts');
const preview=JSON.parse(await readFile(new URL('./fixtures/notification-actions.json',import.meta.url),'utf8'));
const W='11111111-1111-4111-8111-111111111111',P='22222222-2222-4222-8222-222222222222',E='33333333-3333-4333-8333-333333333333';
const raw=preview.bundle.records.find(r=>r.kind==='raw_item'),review=preview.bundle.records.find(r=>r.kind==='raw_review');
const event=preview.bundle.records.find(r=>r.kind==='event');
const scope={enabled:true,workspace_id:W,processor_id:P,not_before:'2026-11-12T12:00:00+08:00',activation_hash:event.payload.automation.activation_hash,
  source_ids:[raw.payload.source_id,'wechat:synthetic_2','wechat:synthetic_3','wechat:synthetic_4','wechat:synthetic_5']};
function body(){return structuredClone({raw_id:raw.id,snapshot_id:review.id,source_id:raw.payload.source_id,epoch:E,generation:1,records:preview.bundle.records});}
function metadata(b){return {...b,records:b.records.map(({kind,id,source_hash})=>({kind,id,source_hash}))};}
async function rehash(b){for(const r of b.records)r.source_hash=await hash(stable(r.payload));return b;}

function rig(){
  const state={records:new Map(),receipts:new Map(),revision:0,revoked:false,loseResponse:false};let connections=0;
  let tail=Promise.resolve();
  const pool={end:async()=>{},begin(fn){
    const operation=tail.then(async()=>{
      const before=structuredClone(state);
      try{
        const result=await fn({unsafe:async(sql,args)=>{
          if(sql.startsWith('SET LOCAL') || sql===LOCK_SQL)return [];
          if(sql===PROCESSOR_SQL)return state.revoked?[]:[{id:P}];
          if(sql===WORKER_SQL)return [{publisher_epoch:E,source_generation:1}];
          if(sql.startsWith('SELECT request_hash,result FROM public.eric_app_receipts'))return state.receipts.has(args[1])?[state.receipts.get(args[1])]:[];
          if(sql.startsWith('INSERT INTO public.eric_app_receipts')){if(state.receipts.has(args[1]))return [];state.receipts.set(args[1],{request_hash:args[2],result:JSON.parse(args[3])});return [{request_id:args[1]}];}
          if(sql===EXISTING_SQL || sql===RECONCILE_EXISTING_SQL){
            return JSON.parse(args[1]).flatMap(r=>{
              const saved=state.records.get(r.kind+'\0'+r.id);return saved?[{...saved,payload_matches:sql===EXISTING_SQL?stable(saved.payload)===stable(r.payload):false}]:[];
            });
          }
          if(sql===INSERT_SQL){const result=[];for(const r of JSON.parse(args[1])){
            const key=r.kind+'\0'+r.id;if(state.records.has(key))continue;
            state.records.set(key,{...structuredClone(r),deleted:false,revision:++state.revision});result.push({kind:r.kind,id:r.id});
          }return result;}
          if(sql.startsWith('SELECT coalesce(max(revision),0)'))return [{revision:state.revision}];
          throw new Error('Unexpected SQL in isolated transaction');
        }});
        if(state.loseResponse){state.loseResponse=false;throw Object.assign(new Error('lost response after commit'),{committed:true});}
        return result;
      }catch(error){if(!error.committed)Object.assign(state,before);throw error;}
    });tail=operation.catch(()=>{});return operation;
  }};
  const endpoints=createAutomaticTaskEndpoints({databaseUrl:'isolated://only',scope:JSON.stringify(scope),clientFactory:async()=>{connections++;return pool;}});
  return {state,endpoints,connections:()=>connections};
}

test('Actual Python-created task projection passes the separately enabled v2 contract',async()=>{
  assert.equal((await validateAutomaticTasks(body(),scope)).records.length,preview.bundle.records.length);
  assert.throws(()=>automationScope(''),{code:'automation_disabled'});
  await assert.rejects(validateWechatBundle(body()),{code:'invalid_wechat_scope'});
});

for(const change of ['hidden','quote','phone','wrong_parent','wrong_activation'])test('Reject '+change+' before any transaction',async()=>{
  const b=body(),e=b.records.find(r=>r.kind==='event').payload;
  if(change==='hidden')e.raw_output='unapproved private content';
  if(change==='quote')e.evidence[0].quote='不存在的来源';
  if(change==='phone')e.title='请联系13800000000';
  if(change==='wrong_parent')e.raw_item_id=P;
  if(change==='wrong_activation')e.automation.activation_hash='f'.repeat(64);
  const r=rig();await assert.rejects(r.endpoints.publish(W,P,await rehash(b)));
  assert.equal(r.connections(),0);assert.equal(r.state.records.size,0);
});

test('Publish, concurrent replay and existing Eric OS event read show one task',async()=>{
  const r=rig(),b=body();const results=await Promise.all([r.endpoints.publish(W,P,b),r.endpoints.publish(W,P,b)]);
  assert.deepEqual(results.map(x=>x.state).sort(),['already_published','published']);
  assert.equal(r.state.records.size,b.records.length);
  const store={page:async()=>({items:[...r.state.records.values()].filter(x=>x.kind==='event'),total:1,offset:0,limit:20}),
    rows:async()=>[],one:async(_,kind,id)=>r.state.records.get(kind+'\0'+id)};
  const page=await new Views(store,W).read('/events',new URL('https://isolated.test/events?status=active'));
  assert.equal(page.items.length,1);assert.equal(page.items[0].status,'needs_action');assert.equal(page.items[0].task_count,1);
  assert.equal((await new Views(store,W).read('/events/'+event.id,new URL('https://isolated.test/events/'+event.id))).tasks.length,1);
});

test('An existing conflicting historical record is retained and the entire insert rolls back',async()=>{
  const r=rig(),b=body(),t=b.records.find(x=>x.kind==='task');
  const old={...structuredClone(t),payload:{...t.payload,title:'保留的用户历史事项'},deleted:false};
  r.state.records.set(t.kind+'\0'+t.id,old);
  await assert.rejects(r.endpoints.publish(W,P,b),{code:'wechat_target_conflict'});
  assert.equal(r.state.records.size,1);assert.deepEqual(r.state.records.get(t.kind+'\0'+t.id),old);
});

test('A committed request with a lost reply is reconciled without new task creation',async()=>{
  const r=rig(),b=body();r.state.loseResponse=true;
  await assert.rejects(r.endpoints.publish(W,P,b),{code:'scoped_publish_unavailable'});
  assert.equal(r.state.records.size,b.records.length);
  assert.equal((await r.endpoints.reconcile(W,P,metadata(b))).state,'already_published');
  assert.equal(r.state.records.size,b.records.length);
});

test('Absence fence rejects delayed old requests, while the bounded next attempt can publish',async()=>{
  const r=rig(),b=body();const absent=await r.endpoints.reconcile(W,P,metadata(b));
  assert.equal(absent.state,'fenced_absent');
  await assert.rejects(r.endpoints.publish(W,P,b),{code:'stale_publisher'});
  const next={...b,generation:absent.generation};assert.equal((await r.endpoints.publish(W,P,next)).state,'published');
  assert.equal(r.state.records.size,b.records.length);
});

test('Workspace mismatch and credential revocation cannot publish',async()=>{
  const r=rig();await assert.rejects(r.endpoints.publish(P,P,body()),{code:'automation_scope_denied'});
  assert.equal(r.connections(),0);r.state.revoked=true;
  await assert.rejects(r.endpoints.publish(W,P,body()),{code:'worker_revoked'});assert.equal(r.state.records.size,0);
});

test('The assembled API routes status, publish and reconciliation through authenticated v2 hooks',async()=>{
  const seen=[];
  const fetcher=async url=>{
    if(url.includes('/rest/v1/eric_app_processors?'))return Response.json([{id:P,workspace_id:W}]);
    if(url.includes('/rest/v1/eric_app_workspaces?'))return Response.json([{id:W}]);
    if(url.includes('/rest/v1/eric_app_workers?'))return Response.json([{publisher_epoch:E,source_generation:1}]);
    throw new Error('Unexpected metadata access');
  };
  const handler=createHandler({base:'https://isolated.supabase.co',secret:'synthetic',fetcher,
    guardedWechatActionsStatus:async(w,p)=>{assert.equal(w,W);assert.equal(p,P);return {protocol:'wechat-personal-actions-v2',...scope};},
    guardedWechatActions:async(w,p,b)=>{seen.push(['publish',w,p,b]);return {state:'published'};},
    guardedWechatActionsReconcile:async(w,p,b)=>{seen.push(['reconcile',w,p,b]);return {state:'already_published'};}});
  const request=(path,b)=>new Request('https://isolated.test/functions/v1/eric-app-api/worker/'+path,{
    method:b?'POST':'GET',headers:{'X-Eric-Worker-Key':'ework_'+'a'.repeat(43),'Content-Type':'application/json'},...(b?{body:JSON.stringify(b)}:{})});
  const status=await handler(request('wechat-actions-status'));assert.equal(status.status,200);
  assert.equal((await status.json()).activation_hash,scope.activation_hash);
  assert.equal((await handler(request('wechat-actions',body()))).status,200);
  assert.equal((await handler(request('wechat-actions-reconcile',metadata(body())))).status,200);
  assert.deepEqual(seen.map(x=>x.slice(0,3)),[['publish',W,P],['reconcile',W,P]]);
  assert.equal((await handler(new Request('https://isolated.test/worker/wechat-actions-status'))).status,401);
});

test('Source-verified automatic deadlines publish and render at the current date without a human-confirmation claim',async()=>{
  const sample=JSON.parse(await readFile(new URL('./fixtures/notification-actions-dates.json',import.meta.url),'utf8'));
  const b=body();b.records=sample.bundle.records;
  b.raw_id=b.records.find(r=>r.kind==='raw_item').id;b.snapshot_id=b.records.find(r=>r.kind==='raw_review').id;
  const localScope={...scope,activation_hash:b.records.find(r=>r.kind==='event').payload.automation.activation_hash};
  assert.equal((await validateAutomaticTasks(b,localScope)).records.length,7);
  const d=b.records.find(r=>r.kind==='deadline').payload;
  assert.equal(d.confirmed_by_user,false);assert.equal(d.verified_automatically,true);
  assert.equal(deadlineState(d,new Date('2026-11-12T12:00:00+08:00')).days_remaining,1);
  assert.equal(deadlineState(d,new Date('2026-11-14T12:00:00+08:00')).state,'overdue');
  assert.equal(deadlineState({...d,verified_automatically:false},new Date()).state,'needs_confirmation');
});
}
