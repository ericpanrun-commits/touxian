import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';

async function moduleAt(path){
  const result=await build({entryPoints:[fileURLToPath(new URL(path,import.meta.url))],bundle:true,write:false,format:'esm',platform:'neutral',target:'es2022'});
  return import('data:text/javascript;base64,'+Buffer.from(result.outputFiles[0].contents).toString('base64'));
}
const {CloudStore}=await moduleAt('../supabase/functions/eric-app-api/store.ts');
const {shortcutArchive}=await moduleAt('../supabase/functions/eric-app-api/archive.ts');
const {visionPayload}=await moduleAt('../supabase/functions/eric-app-api/vision.ts');
const {publicCapture}=await moduleAt('../supabase/functions/eric-app-api/handler.ts');

test('Capture diagnostics expose attempt history without exposing worker lease credentials',()=>{
  const capture={id:'synthetic',state:'failed',attempts:5,next_attempt_at:'2026-11-03T00:00:00Z',last_attempt_at:'2026-11-03T00:00:00Z',completed_at:'2026-11-03T00:00:00Z',error:{code:'retry_exhausted'},lease_token:'private-lease-token',request_hash:'internal-hash'};
  const dto=publicCapture(capture);
  assert.equal(dto.attempts,5);assert.equal(dto.last_attempt_at,capture.last_attempt_at);assert.equal(dto.completed_at,capture.completed_at);
  assert.ok(!('lease_token' in dto)&&!('request_hash' in dto));
});

test('Database errors cannot become successful writes or temporary schema/permission failures',async()=>{
  for(const [code,status,expected] of [['42501',403,'data_access_denied'],['42703',400,'schema_mismatch'],['PGRST202',404,'schema_mismatch'],['23505',409,'duplicate_request']]){
    const store=new CloudStore({base:'https://fixture.supabase.co',secret:'synthetic-server-secret',fetcher:async()=>Response.json({code},{status})});
    await assert.rejects(store.db('eric_app_records','POST',{}),{code:expected});
  }
  for(const body of ['not json','null','[]']){
    const store=new CloudStore({base:'https://fixture.supabase.co',secret:'synthetic-server-secret',fetcher:async()=>new Response(body,{status:503})});
    await assert.rejects(store.db('eric_app_records','POST',{}),{code:'data_unavailable'});
  }
});

test('The restored shortcut archive requires durable commit and resumes one capture identity',async()=>{
  const body=new TextEncoder().encode('%PDF-1.7 synthetic original');
  const sha=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',body)),x=>x.toString(16).padStart(2,'0')).join('');
  const row={id:'11111111-1111-4111-8111-111111111111',device_id:'phone',sha256:sha,bytes:body.length,filename:'note.pdf',received_at:'2026-11-03T00:00:00Z'};
  const calls=[];let committed=false,stored;
  const store={
    async db(path){return path.startsWith('eric_app_workspaces')?[{id:'workspace'}]:stored?[stored]:[];},
    async rpc(name,args){
      calls.push([name,args]);
      if(name==='reserve_capture')stored={...args.p_capture,state:'uploading'};
      if(name==='commit_capture'){
        if(!committed)throw new Error('Synthetic database write failure');
        stored.state='received';return [{received_at:'2026-11-03T00:00:00Z'}];
      }
      return [];
    },
    async putPart(_workspace,id,index,data){calls.push(['part',{id,index,bytes:data.length}]);},
    async resource(_workspace,id){assert.equal(id,'capture:'+row.id+':'+sha);},
  };
  const archive=shortcutArchive(store);
  await assert.rejects(archive({row,installation:'installation',body}),/database write failure/);
  assert.equal(stored.state,'uploading');
  committed=true;await archive({row,installation:'installation',body});
  const before=calls.length;await archive({row,installation:'installation',body});
  assert.equal(calls.length,before);
  assert.equal(stored.id,row.id);
  assert.deepEqual(new Set(calls.filter(([name])=>name==='reserve_capture').map(([,args])=>args.p_capture.request_hash)).size,1);
});

test('Vision ingestion cannot acknowledge empty or contradictory completed output',()=>{
  const id='11111111-1111-4111-8111-111111111111',sha='a'.repeat(64);
  const value={schema:'eric-os-vision-result.v1',capture_id:id,resource_id:'capture:'+id+':'+sha,sha256:sha,
    source:'original_image',analyzer:'synthetic',status:'completed',analyzed_at:'2026-11-03T00:00:00Z'};
  for(const result of [{},{summary:'  '},{key_points:[]},{confidence:'high'}]){
    assert.throws(()=>visionPayload({...value,result}),{code:'invalid_input'});
  }
  assert.throws(()=>visionPayload({...value,result:{summary:'Synthetic'},error:{message:'Provider failed'}}),{code:'invalid_input'});
  assert.equal(visionPayload({...value,status:'failed',error:{message:'Provider failed'}}).status,'failed');
  assert.equal(visionPayload({...value,result:{summary:'Synthetic'}}).status,'completed');
});
