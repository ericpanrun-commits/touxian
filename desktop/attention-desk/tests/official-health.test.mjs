import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';

async function moduleAt(path){
  const entry=process.env.ERIC_API_TEST_ROOT?resolve(process.env.ERIC_API_TEST_ROOT,path.split('/').at(-1)):fileURLToPath(new URL(path,import.meta.url));
  const compiled=await build({entryPoints:[entry],bundle:true,write:false,
    format:'esm',platform:'neutral',target:'es2022',external:['npm:*']});
  return import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].contents).toString('base64'));
}
const {officialHealth}=await moduleAt('../supabase/functions/eric-app-api/official_health.ts');
const {createOfficialHealthReader}=await moduleAt('../supabase/functions/eric-app-api/official_health_db.ts');
const NOW=new Date('2026-11-04T04:00:00Z'),W='11111111-1111-4111-8111-111111111111',OTHER='22222222-2222-4222-8222-222222222222';
const source={id:'school',name:'School public notices',enabled:true,status:'ok',failure_count:'0',last_new_count:'0',
  last_scan_at:'2026-11-04T03:45:00Z',last_success_at:'2026-11-04T03:46:00Z',polling_interval:'3600',
  item_count:'12',pending_content:'0',unmapped_raw_count:'0',missing_original_count:'0',pending_projection_count:'0'};

test('Healthy empty scan is independent of old article arrival and unverified frontend',()=>{
  const result=officialHealth([{...source,last_notification_at:'2026-10-01T03:00:00Z',original_text:'NEVER_RETURN',config:{secret:'NEVER_RETURN'}}],NOW);
  const row=result.sources[0];
  assert.equal(row.collection_state,'healthy_idle');assert.equal(row.alert_required,false);
  assert.equal(row.frontend_visibility,'unverified');assert.equal(row.sync_state,'synchronized');
  assert.ok(!JSON.stringify(result).includes('NEVER_RETURN'));
});

test('Failed collection, stale coverage, missing integration and pending projection are distinct',()=>{
  const rows=officialHealth([
    {...source,id:'bad',status:'partial',failure_count:'11',error:'http_error'},
    {...source,id:'late',last_success_at:'2026-11-03T00:00:00Z'},
    {...source,id:'unmapped',unmapped_raw_count:'4'},
    {...source,id:'sync',pending_projection_count:'2'},
    {...source,id:'disabled',enabled:false,status:'failed',failure_count:'99'},
  ],NOW).sources;
  assert.equal(rows[0].collection_state,'partial');assert.equal(rows[0].alert_required,true);
  assert.equal(rows[1].collection_state,'stale');assert.equal(rows[1].alert_required,true);
  assert.equal(rows[2].sync_state,'integration_missing');assert.equal(rows[2].pending_sync,0);
  assert.equal(rows[3].sync_state,'pending');assert.equal(rows[3].unmapped_raw_count,0);
  assert.equal(rows[4].collection_state,'disabled');assert.equal(rows[4].alert_required,false);
});

test('An index without an original is not reported synchronized or as an uploadable backlog',()=>{
  const result=officialHealth([{...source,id:'missing-original',pending_content:'1',missing_original_count:'1'}],NOW);
  assert.equal(result.sources[0].sync_state,'original_unavailable');
  assert.equal(result.sources[0].missing_original_count,1);
  assert.equal(result.sources[0].pending_sync,0);
  assert.equal(result.missing_original_count,1);
  assert.equal(result.pending_content,1);
  assert.equal(result.sources[0].frontend_visibility,'unverified');
});

test('Invalid coverage counts stay unknown instead of reducing to zero',()=>{
  const result=officialHealth([{...source,missing_original_count:'not-a-number'}, {...source,id:'known',missing_original_count:'1'}],NOW);
  assert.equal(result.sources[0].sync_state,'unknown');
  assert.equal(result.sources[1].sync_state,'original_unavailable');
  assert.equal(result.missing_original_count,null);
});

test('Absent and malformed counts cannot claim zero or an idle scan',()=>{
  for (const value of [null,undefined,'','  ',true,false,-1,0.5,'1e3','0x10']) {
    const result=officialHealth([{...source,missing_original_count:value}],NOW);
    assert.equal(result.sources[0].missing_original_count,null,`invalid count ${String(value)}`);
    assert.equal(result.sources[0].sync_state,'unknown');
    assert.equal(result.missing_original_count,null);
  }
  const missingFailures=officialHealth([{...source,failure_count:null}],NOW).sources[0];
  assert.equal(missingFailures.consecutive_failures,null);
  assert.equal(missingFailures.collection_state,'unknown');
  const missingNew=officialHealth([{...source,last_new_count:null}],NOW).sources[0];
  assert.equal(missingNew.collection_state,'healthy');
});

test('Read-only transaction, workspace binding, concurrent coalescing and bounded cache',async()=>{
  const queries=[],transactions=[],current={now:NOW};let created=0;
  const tag=async()=>[];
  tag.unsafe=async(sql,params)=>{queries.push({sql,params});return [{...source,unmapped_raw_count:params[0]===W?'1':'3'}];};
  const reader=createOfficialHealthReader({databaseUrl:'postgres://synthetic-only',clock:()=>current.now,
    clientFactory:async()=>{created++;return {begin:async(mode,fn)=>{transactions.push(mode);return fn(tag);}};}});
  const [one,two]=await Promise.all([reader(W),reader(W)]);
  assert.deepEqual(one,two);assert.equal(created,1);assert.deepEqual(transactions,['read only']);
  assert.equal(queries.length,1);assert.deepEqual(queries[0].params,[W]);
  assert.equal(one.sources[0].unmapped_raw_count,1);
  assert.equal((await reader(OTHER)).sources[0].unmapped_raw_count,3);
  assert.deepEqual(queries[1].params,[OTHER]);
  current.now=new Date(NOW.getTime()+61_000);await reader(W);assert.equal(queries.length,3);
});

test('Invalid workspace and missing configuration never open a connection; DB failures reveal no secrets',async()=>{
  let called=0;
  const missing=createOfficialHealthReader({databaseUrl:'',clientFactory:async()=>{called++;}});
  await assert.rejects(missing(W),{code:'source_health_not_configured'});assert.equal(called,0);
  await assert.rejects(missing('foreign-invalid'),{code:'invalid_reference'});assert.equal(called,0);
  const broken=createOfficialHealthReader({databaseUrl:'postgres://synthetic-only',clientFactory:async()=>{throw Error('NEVER_RETURN password, host or SQL');}});
  await assert.rejects(broken(W),error=>error.code==='source_health_unavailable'&&!String(error).includes('NEVER_RETURN'));
});
