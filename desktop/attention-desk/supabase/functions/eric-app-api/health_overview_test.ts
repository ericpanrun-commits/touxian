import assert from 'node:assert/strict';
import { createHealthOverviewReader } from './health_overview.ts';
import { createHandler } from './handler.ts';
import { healthFixture } from '../../../tests/health-fixture.mjs';
import { AppFault } from './store.ts';

const OWNER='11111111-1111-4111-8111-111111111111', WORKSPACE='22222222-2222-4222-8222-222222222222', DEVICE='33333333-3333-4333-8333-333333333333', SESSION='44444444-4444-4444-8444-444444444444';
const now=new Date('2026-11-09T04:00:00Z');
const token='C'.repeat(43);
const config={token,owner:OWNER,workspace:WORKSPACE,mode:'trend'};

Deno.test('overview reader fixes origin, method and headers, limits output and refuses other owners', async()=>{
  let calls=0;
  const read=createHealthOverviewReader({...config,fetcher:async(input,init)=>{
    calls++;assert.equal(String(input),'https://health-analyzer.healthdash26.workers.dev/api/integrations/eric-os/overview?view=trend');
    assert.equal(init?.method,'GET');assert.equal(init?.redirect,'error');assert.equal(init?.cache,'no-store');assert.equal(init?.credentials,'omit');
    assert.deepEqual(init?.headers,{Authorization:'Bearer '+token});
    const value:any=healthFixture(now);value.private_payload='never forwarded';return Response.json(value);
  }});
  assert.equal((await read(WORKSPACE,OWNER)).metrics.length,6);
  await assert.rejects(()=>read('other',OWNER),(e:any)=>e.status===403);
  await assert.rejects(()=>read(WORKSPACE,'other'),(e:any)=>e.status===403);
  assert.equal(calls,1);
  for(const mode of ['off','bad']) await assert.rejects(()=>createHealthOverviewReader({...config,mode})(WORKSPACE,OWNER),(e:any)=>e.status===503);
});

Deno.test('upstream failures never become Eric auth failures or leak upstream body',async()=>{
  const factories=[()=>new Response('secret',{status:401}),()=>new Response('redirect',{status:302,headers:{location:'https://evil.test'}}),()=>new Response('x'.repeat(33*1024),{headers:{'content-type':'application/json'}}),()=>Response.json({schema:'bad'}),()=>new Response('html',{headers:{'content-type':'text/html'}})];
  for(const make of factories) {
    const read=createHealthOverviewReader({...config,fetcher:async()=>make()});
    await assert.rejects(()=>read(WORKSPACE,OWNER),(e:any)=>e.status===503&&!e.message.includes('secret'));
  }
});

Deno.test('eight-second upstream timeout aborts stalled transport',async()=>{
  let aborted=false;
  const read=createHealthOverviewReader({...config,fetcher:(_input,init)=>new Promise((_resolve,reject)=>{
    init?.signal?.addEventListener('abort',()=>{aborted=true;reject(Error('private transport detail'));},{once:true});
  })});
  const started=Date.now();
  await assert.rejects(()=>read(WORKSPACE,OWNER),(e:unknown)=>e instanceof AppFault&&e.status===503);
  assert.ok(aborted);assert.ok(Date.now()-started<10_000);
});

Deno.test('whole-route auth and device revocation precede Health fetch; /health probe remains public',async()=>{
  let upstream=0,revoked=false,authValid=true,owner=OWNER;
  const claims={sub:OWNER,role:'authenticated',session_id:SESSION,exp:Math.floor(now.getTime()/1000)+3600};
  const encode=(value:string)=>btoa(value).replaceAll('=','').replaceAll('+','-').replaceAll('/','_');
  const jwt=[encode('{"alg":"HS256"}'),encode(JSON.stringify(claims)),encode('test-signature')].join('.');
  const app=createHandler({base:'https://synthetic.supabase.co',secret:'synthetic-service',clock:()=>now,origins:['https://eric.example'],healthOverview:createHealthOverviewReader({...config,fetcher:async()=>{upstream++;return Response.json(healthFixture(now));}}),fetcher:async(input,init)=>{
    assert.ok(!init?.method||init.method==='GET','Overview must not write to Eric OS storage');
    const path=String(input);
    if(path.includes('/auth/v1/user'))return Response.json({id:owner,email:'synthetic@example.test',email_confirmed_at:now.toISOString()},{status:authValid?200:401});
    if(path.includes('eric_app_workspaces?'))return Response.json([{id:WORKSPACE,owner_id:OWNER}]);
    if(path.includes('eric_app_devices?'))return Response.json([{id:DEVICE,user_id:OWNER,revoked_at:revoked?now.toISOString():null}]);
    throw Error('unexpected request '+path);
  }});
  const request=(path='/health/overview',headers:Record<string,string>={Authorization:'Bearer '+jwt})=>new Request('https://synthetic.supabase.co/functions/v1/eric-app-api'+path,{headers});
  assert.equal((await app(request('/health',{}))).status,200);assert.equal(upstream,0);
  assert.equal((await app(request('/health/overview',{}))).status,401);
  const response=await app(request());assert.equal(response.status,200);assert.equal(response.headers.get('Cache-Control'),'private, no-store');assert.equal(upstream,1);
  revoked=true;assert.equal((await app(request())).status,401);assert.equal(upstream,1);revoked=false;
  authValid=false;assert.equal((await app(request())).status,401);assert.equal(upstream,1);authValid=true;
  owner='55555555-5555-4555-8555-555555555555';assert.equal((await app(request())).status,401);assert.equal(upstream,1);
  assert.equal((await app(request('/health/overview',{Authorization:'Bearer '+jwt,Origin:'https://evil.test'}))).status,403);assert.equal(upstream,1);
});
