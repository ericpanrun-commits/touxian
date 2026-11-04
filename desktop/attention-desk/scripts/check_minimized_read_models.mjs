// Offline contract check against the exact, previously verified API19 read model.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const folder=join(root,'artifacts/p0-20261104');
const bundle=JSON.parse(await readFile(join(folder,'wechat-minimized-projection-preview.json'),'utf8'));
const samples=JSON.parse(await readFile(join(folder,'wechat-minimized-text-samples.json'),'utf8'));
const candidate=JSON.parse(await readFile(join(folder,'official-api-candidate.json'),'utf8'));
const verification=JSON.parse(await readFile(join(folder,'api19-source-verification.json'),'utf8'));
assert.equal(verification.api_version,19);
assert.equal(verification.preserved_original_modules['views.ts'],true);
const entry=join(candidate.directory,'views.ts');
assert.equal(createHash('sha256').update(await readFile(entry)).digest('hex'),candidate.preserved_files_sha256['views.ts']);
const compiled=await build({entryPoints:[entry],bundle:true,write:false,format:'esm',platform:'neutral',target:'es2022'});
const {Views}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].contents).toString('base64'));
const workspace='11111111-1111-4111-8111-111111111111';
const seen=[];
const rows=bundle.records.map((r,index)=>({...r,workspace_id:workspace,revision:index+1}));
// No network is permitted. All reads use the reviewed local JSON; no real cloud mutation.
globalThis.fetch=()=>{throw new Error('Network forbidden in offline preview verification');};
const store={
  async one(scope,kind,id){assert.equal(scope,workspace);seen.push({kind,id});return rows.find(r=>r.kind===kind&&r.id===id)||null;},
  async rpc(name,args){
    assert.equal(name,'inbox_page');assert.equal(args.p_workspace,workspace);assert.equal(args.p_scope,'all');
    const items=rows.filter(r=>r.kind==='inbox_item');return {items,total:items.length,offset:0,limit:20};
  }
};
const view=new Views(store,workspace),checks=[],responses={details:{}};
const list=await view.read('/inbox',new URL('https://offline.invalid/inbox?scope=all&status=all'));
responses.list=list;
assert.equal(list.total,samples.proposed_raw_count);
for(const sample of samples.samples){
  const p=sample.payload,id=p.id,snapshot=p.source_snapshot_id;
  const ordinary=await view.read('/inbox/'+id,new URL('https://offline.invalid/inbox/'+id));
  const advanced=await view.read('/inbox/'+id,new URL('https://offline.invalid/inbox/'+id+'?advanced=1'));
  const raw=await view.read('/raw/'+snapshot,new URL('https://offline.invalid/raw/'+snapshot));
  responses.details[id]={ordinary,advanced,raw};
  assert(ordinary.title.startsWith('【脱敏节选】'));
  assert.equal(ordinary.original.original_text,p.text);
  assert.equal(advanced.debug.original_text,p.text);
  assert.equal(raw.snapshot.original_text,p.text);
  assert.deepEqual(ordinary.attachments,[]);assert.deepEqual(ordinary.school_documents,[]);
  assert.equal(ordinary.review.draft,null);assert.equal(raw.extraction,null);
  const encoded=JSON.stringify({ordinary,advanced,raw});
  for(const key of ['database_identity','raw_sender_id','chat_name','local_excerpt_spans','local_evidence_sha256'])assert(!encoded.includes(key));
  assert.deepEqual(ordinary.original.wechat.messages.map(m=>m.message_id),p.source_message_ids);
  assert(ordinary.original.wechat.messages.every(m=>Number.isFinite(new Date(m.captured_at).getTime())));
  assert.deepEqual(ordinary.course,p.course);
  checks.push({raw_item_id:id,snapshot_id:snapshot,normal_inbox:true,advanced_debug:true,raw_review:true,
    redacted_text_only:true,attachment_count:0});
}
const result={PASS:true,offline:true,api_read_model_version:19,record_count:bundle.record_count,
  raw_count:checks.length,attachment_count:0,network_requests:0,cloud_writes:0,model_requests:0,
  bundle_hash:bundle.bundle_hash,checks};
await writeFile(join(folder,'minimized-read-model-contract.json'),JSON.stringify(result,null,2));
await writeFile(join(folder,'minimized-owner-read-responses.json'),JSON.stringify({offline:true,bundle_hash:bundle.bundle_hash,...responses},null,2));
console.log(JSON.stringify(result));
