import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const base=new URL('../artifacts/deepseek-qr-20261108/',import.meta.url);
const {validateWechatBundle}=await import(new URL('eric-app-api/guarded_wechat_publish.ts',base));
const {hash,stable}=await import(new URL('eric-app-api/store.ts',base));
const original=JSON.parse(await readFile(new URL('synthetic-qr-bundle.json',base),'utf8'));
const hint='需扫码，二维码请返回原微信群查看';
function body(){return Object.fromEntries(['raw_id','snapshot_id','source_id','records','epoch','generation'].map(k=>[k,structuredClone(original[k])]));}
async function update(value,change){
  const rows=Object.fromEntries(value.records.map(r=>[r.kind,r.payload]));
  change(rows.raw_review.snapshot,rows);
  rows.inbox.original=structuredClone(rows.raw_review.snapshot);
  rows.inbox.review=structuredClone(rows.raw_review);
  for(const record of value.records)record.source_hash=await hash(stable(record.payload));
}
test('Python protected dispatch QR text is accepted unchanged by cloud validator',async()=>{
  const value=body();await validateWechatBundle(value);
  const snapshot=value.records.find(r=>r.kind==='raw_review').payload.snapshot;
  assert.equal(snapshot.document_warnings.at(-1).message,hint);
  assert.deepEqual(snapshot.attachments,[]);assert.deepEqual(snapshot.attachment_jobs,[]);
  assert(!JSON.stringify(value).includes('QR-ATTACHMENT-SECRET'));
  assert(snapshot.original_text.includes('11月10日中午12点'));
  assert(snapshot.original_text.includes('教学楼一楼'));
});
for(const kind of ['image','binary','extra warning','altered warning','missing warning','old publication','old capture','nontext']){
  test(`QR hint cannot authorize ${kind}`,async()=>{
    const value=body();
    await update(value,(s)=>{
      if(kind==='image')s.attachments=[{data:'QR-ATTACHMENT-SECRET'}];
      if(kind==='binary')s.attachment_jobs=[{binary:'AAAA'}];
      if(kind==='extra warning')s.document_warnings.push({code:'other',message:'secret'});
      if(kind==='altered warning')s.document_warnings.at(-1).message='secret';
      if(kind==='missing warning')s.document_warnings.pop();
      if(kind==='old publication')s.wechat.messages[0].source_time='2026-11-08T13:51:00+00:00';
      if(kind==='old capture')s.wechat.messages[0].captured_at='2026-11-08T13:51:00+00:00';
      if(kind==='nontext')s.wechat.messages[0].message_type='image';
    });
    await assert.rejects(()=>validateWechatBundle(value));
  });
}
for(const text of ['请扫码，名单：张三、李四','请扫码，学号2026001234','请扫码，手机号13800000000']){
  test('scan wording does not exempt private text even with matching hashes and fields',async()=>{
    const value=body();
    await update(value,(s,rows)=>{
      s.original_text=text;s.blocks[0].text=text;s.wechat.messages[0].raw_text=text;
      rows.inbox_item.preview=rows.inbox.preview=text;
    });
    const rows=Object.fromEntries(value.records.map(r=>[r.kind,r.payload]));
    rows.raw_review.snapshot.hash=await hash(text);
    await update(value,()=>{});
    await assert.rejects(()=>validateWechatBundle(value));
  });
}
