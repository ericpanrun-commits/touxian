import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { learningBookmarklet } from '../src/personal/learningHelpers.js';

test('one-click capture reads text without clicking or submitting to 学习通', () => {
  const opened=[];
  const context={ document:{title:'高数课堂结果',body:{innerText:'我的答案：B\n回答错误'},querySelectorAll:()=>[]},location:{origin:'https://mooc1.chaoxing.com',pathname:'/result',search:'?token=private'},window:{getSelection:()=>null,open:(...args)=>opened.push(args)},JSON,encodeURIComponent };
  vm.runInNewContext(learningBookmarklet().replace(/^javascript:/,''),context);
  assert.equal(opened.length,1);
  const url=new URL(opened[0][0]);
  assert.equal(url.origin,'http://127.0.0.1:8765');
  const data=JSON.parse(decodeURIComponent(url.hash.split('#learning-share=')[1]));
  assert.match(data.text,/我的答案：B/);
  assert.ok(!data.text.includes('token=private'));
  assert.equal(url.searchParams.get('personal_view'),'learning');
});

test('cross-origin frames are skipped; selected results reduce private page content', () => {
  const opened=[];const frame={get contentDocument(){throw Error('cross origin')}};
  vm.runInNewContext(learningBookmarklet().replace(/^javascript:/,''),{document:{title:'结果',body:{innerText:'unselected account information'},querySelectorAll:()=>[frame]},location:{origin:'https://chaoxing.com',pathname:'/result'},window:{getSelection:()=>({toString:()=> '已选择的课堂结果'}),open:(...args)=>opened.push(args)},JSON,encodeURIComponent});
  const data=JSON.parse(decodeURIComponent(new URL(opened[0][0]).hash.slice('#learning-share='.length)));
  assert.match(data.text,/已选择的课堂结果/);assert.ok(!data.text.includes('unselected'));
});
