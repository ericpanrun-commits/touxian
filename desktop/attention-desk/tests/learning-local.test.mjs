import test from 'node:test';
import assert from 'node:assert/strict';
import { consumeLearningShare } from '../src/personal/learningHelpers.js';

test('private shared text is consumed from the fragment and never retained in navigation', () => {
  const changed=[];globalThis.history={replaceState:(_state,_title,url)=>changed.push(String(url))};
  const url=new URL('http://127.0.0.1:8765/?personal_view=learning');
  url.hash='#learning-share='+encodeURIComponent(JSON.stringify({title:'课堂结果',text:'我的答案：B\n回答错误'}));
  const data=consumeLearningShare(url);
  assert.equal(data.title,'课堂结果');assert.match(data.request_id,/^[a-f0-9-]{36}$/);
  assert.equal(changed.length,1);assert.equal(new URL(changed[0]).hash,'');
  assert.ok(!changed[0].includes('我的答案'));
  url.hash='#learning-share=broken%';
  assert.match(consumeLearningShare(url).error,/无法读取/);
  delete globalThis.history;
});
