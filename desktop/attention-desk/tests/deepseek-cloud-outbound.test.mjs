import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

// The package is reconstructed from the pinned deployed-v23 snapshot and the
// reviewed patch. These tests do not load any environment, credential or client.
const packageUrl=new URL('../artifacts/deepseek-cloud-20261108/guarded-api-candidate/eric-app-api/',import.meta.url);
const {validateWechatBundle}=await import(new URL('guarded_wechat_publish.ts',packageUrl));
const {hash,stable}=await import(new URL('store.ts',packageUrl));
const fixture=JSON.parse(await readFile(new URL('synthetic-bundle.json',packageUrl),'utf8'));
const cases=JSON.parse(await readFile(new URL('../server/fixtures/wechat/cloud-public-policy-cases.json',import.meta.url),'utf8'));

async function body(text,state='pending') {
  const records=structuredClone(fixture.records),rows=Object.fromEntries(records.map(r=>[r.kind,r]));
  const raw=rows.raw_item.payload,review=rows.raw_review.payload,summary=rows.inbox_item.payload,detail=rows.inbox.payload;
  const snapshot=review.snapshot;
  assert.equal(snapshot.blocks.length,1);assert.equal(snapshot.wechat.messages.length,1);
  snapshot.original_text=text;snapshot.hash=await hash(text);
  snapshot.blocks[0].text=text;snapshot.wechat.messages[0].raw_text=text;
  review.status=state;
  summary.preview=Array.from(text).slice(0,160).join('');summary.review_status=state;
  Object.assign(detail,summary);detail.original=structuredClone(snapshot);detail.review=structuredClone(review);
  for(const record of records)record.source_hash=await hash(stable(record.payload));
  return {raw_id:raw.id,snapshot_id:review.raw_snapshot_id,source_id:raw.source_id,records,
    epoch:'f655a6c0-9d99-41eb-aa7a-34bb6b464893',generation:1};
}

for(const [index,entry] of cases.entries()) {
  test(`public-policy privacy case ${index+1} matches local validation`,async()=>{
    const value=await body(entry.text);
    if(entry.safe) {
      await validateWechatBundle(value);
      assert.equal(value.records.find(r=>r.kind==='raw_review').payload.snapshot.original_text,entry.text);
    } else await assert.rejects(()=>validateWechatBundle(value));
  });
}

for(const state of ['pending','archived','accepted','ignored']) {
  test(`preserve review status ${state} without reopening a task`,async()=>{
    const value=await body('请全体同学提前确认参加时间。',state);
    if(['pending','archived'].includes(state))await validateWechatBundle(value);
    else await assert.rejects(()=>validateWechatBundle(value));
  });
}

test('inconsistent status cannot be disguised by recomputing hashes',async()=>{
  const value=await body('请全体同学提前确认参加时间。','archived');
  value.records.find(r=>r.kind==='inbox_item').payload.review_status='pending';
  for(const record of value.records)record.source_hash=await hash(stable(record.payload));
  await assert.rejects(()=>validateWechatBundle(value));
});
