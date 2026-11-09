// Run with ERIC_HEALTHDATA_SOURCE pointing to the separately checked-out HealthData.
// Exercises both real routers and real projection code; only storage/network is fake.
import assert from 'node:assert/strict';
import {createHandler} from '../supabase/functions/eric-app-api/handler.ts';
import {createHealthOverviewReader} from '../supabase/functions/eric-app-api/health_overview.ts';
import {HEALTH_METRICS} from '../supabase/functions/eric-app-api/health_contract.js';
import {pathToFileURL} from 'node:url';

Deno.test('HealthData router → real read-only Edge adapter → authenticated Eric route',async()=>{
  const source=Deno.env.get('ERIC_HEALTHDATA_SOURCE');
  if(!source)throw Error('Set ERIC_HEALTHDATA_SOURCE to the HealthData checkout.');
  const worker=(await import(pathToFileURL(source.replaceAll('\\','/')+'/worker/index.js').href)).default;
  const owner='11111111-1111-4111-8111-111111111111',workspace='22222222-2222-4222-8222-222222222222',session='44444444-4444-4444-8444-444444444444';
  const now=new Date(),end=new Date(now.getTime()+8*3600_000).toISOString().slice(0,10),previous=new Date(Date.parse(end)-86400_000).toISOString().slice(0,10),received=now.toISOString(),cloudToken='C'.repeat(43);
  const summary={metrics:Object.fromEntries(Object.keys(HEALTH_METRICS).map((metric,index)=>[metric,{daily:{[previous]:42+index,[end]:10}}])),server_received_at:received,source_generated_at:received,latest_health_date:end};
  let kvReads=0,statusReads=0,proxyReads=0;
  const env={HEALTH_OVERVIEW_CLOUD_TOKEN:cloudToken,HEALTH_OVERVIEW_DESKTOP_TOKEN:'D'.repeat(43),SUPABASE_URL:'https://healthsynthetic.supabase.co',SUPABASE_SECRET_KEY:'sb_secret_synthetic',HEALTH_USER_ID:owner,HEALTH_DATA:{get:async(key:string)=>{kvReads++;assert.equal(key,'latest_summary_json');return JSON.stringify(summary);},put:()=>{throw Error('Forbidden KV write');},delete:()=>{throw Error('Forbidden KV delete');}}};
  const native=globalThis.fetch;
  globalThis.fetch=async(input,init)=>{
    statusReads++;assert.equal(String(input),'https://healthsynthetic.supabase.co/rest/v1/rpc/get_health_analysis_status');assert.equal(init?.method,'POST');
    assert.deepEqual(JSON.parse(String(init?.body)),{p_user_id:owner});
    return Response.json([{state:'completed',received_at:received,latest_health_date:end,analysis_completed_at:received}]);
  };
  try{
    const reader=createHealthOverviewReader({token:cloudToken,owner,workspace,mode:'trend',fetcher:async(input,init)=>{proxyReads++;return worker.fetch(new Request(String(input),init),env,{});}});
    const app=createHandler({base:'https://ericsynthetic.supabase.co',secret:'sb_secret_eric_synthetic',healthOverview:reader,fetcher:async(input,init)=>{
      assert.ok(!init?.method||init.method==='GET');const path=String(input);
      if(path.includes('/auth/v1/user'))return Response.json({id:owner,email_confirmed_at:received});
      if(path.includes('eric_app_workspaces?'))return Response.json([{id:workspace}]);
      if(path.includes('eric_app_devices?'))return Response.json([{id:'synthetic-device',user_id:owner,revoked_at:null}]);
      throw Error('Unexpected Eric request '+path);
    }});
    const encode=(value:unknown)=>btoa(JSON.stringify(value)).replaceAll('=','').replaceAll('+','-').replaceAll('/','_');
    const jwt=[encode({alg:'HS256'}),encode({sub:owner,role:'authenticated',session_id:session,exp:Math.floor(now.getTime()/1000)+3600}),encode('signature')].join('.');
    const response=await app(new Request('https://ericsynthetic.supabase.co/functions/v1/eric-app-api/health/overview',{headers:{Authorization:'Bearer '+jwt}}));
    assert.equal(response.status,200);const dto=await response.json();assert.equal(dto.metrics.length,6);assert.equal(dto.metrics[0].latest_value,42);assert.equal(dto.window.end,end);assert.equal(dto.analysis.state,'completed');
    assert.deepEqual([kvReads,statusReads,proxyReads],[1,1,1]);
    assert.ok(!JSON.stringify(dto).includes('sb_secret_'));assert.ok(!JSON.stringify(dto).includes(cloudToken));
    assert.equal((await app(new Request('https://ericsynthetic.supabase.co/functions/v1/eric-app-api/health/overview'))).status,401);
    assert.equal(proxyReads,1);
  }finally{globalThis.fetch=native;}
});
