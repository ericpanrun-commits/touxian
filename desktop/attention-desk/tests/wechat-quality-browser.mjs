// Browser plugin unavailable; real HTTP/SQLite/React, synthetic messages and mock providers.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {chromium} from 'playwright';
const base=process.env.ATTENTION_QA_URL||'http://127.0.0.1:18767';
const out=process.env.ATTENTION_QA_OUTPUT||path.join(os.tmpdir(),'attention-quality-qa');await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
const checks=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
const state=async()=>{const r=await page.request.get(base+'/__test__/state');const s=await r.json();assert.equal(s.synthetic_only,true);return s};
const update=async name=>{const r=await page.request.post(base+'/__test__/'+name);assert.ok(r.ok());return r.json()};
const noOverflow=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
try {
 let first;
 for(let i=0;i<40;i++){try{first=await state();break}catch(e){if(!/ECONNREFUSED|Timeout/.test(String(e))||i===39)throw e;await new Promise(r=>setTimeout(r,250))}}
 assert.deepEqual([first.pending,first.fyi,first.media,first.media_groups,first.unresolved,first.bark_calls],[1,2,2,1,1,1]);
 await page.goto(base+'/?personal_view=wechat-inbox');await page.locator('.wechat-candidate').waitFor();
 assert.equal(await page.locator('.wechat-candidate').count(),1);
 assert.equal(await page.locator('.wechat-candidate').filter({hasText:/不可识别|获奖|自愿|校园网|查询/}).count(),0);
 await page.getByText('未解析附件 · 2',{exact:true}).click();
 assert.equal(await page.locator('.desk-holding-area').getByRole('button',{name:'查看信息：未解析附件',exact:true}).count(),1);
 await page.getByRole('button',{name:'查看信息：未解析附件',exact:true}).click();
 let sheet=page.getByRole('dialog',{name:'未解析附件',exact:true});await sheet.waitFor();
 assert.equal(await sheet.getByRole('button',{name:'确认',exact:true}).count(),0);assert.equal(await sheet.getByRole('button',{name:'编辑',exact:true}).count(),0);
 await sheet.getByText('查看原始信息与附件',{exact:true}).click();await sheet.locator('.personal-original-text').waitFor();
 await page.screenshot({path:path.join(out,'quality-media-sheet.png')});await page.keyboard.press('Escape');checks.push('one-action-only-grouped-unparsed-images-readonly');
 await page.getByRole('button',{name:'今天',exact:true}).click();await page.getByRole('heading',{name:'值得知道',exact:true}).waitFor();
 await page.getByRole('button',{name:'查看信息：成绩查询方式（合成）',exact:true}).waitFor();
 assert.equal(await page.locator('.desk-knowledge-rows strong').count(),2);
 await page.getByRole('button',{name:'查看信息：考试校园网要求（合成）',exact:true}).click();
 sheet=page.getByRole('dialog',{name:'值得知道',exact:true});await sheet.waitFor();
 assert.equal(await sheet.getByRole('button',{name:'确认',exact:true}).count(),0);
 assert.ok((await sheet.innerText()).includes('网络使用条件说明'));
 await page.keyboard.press('Escape');assert.equal((await state()).events,0);assert.equal((await state()).bark_calls,1);
 await page.screenshot({path:path.join(out,'quality-home.png'),fullPage:true});checks.push('fyi-home-auto-archive-no-confirm-no-push-no-formal');
 const question=await update('question');assert.equal(question.model_calls,first.model_calls);assert.equal(question.pending,1);assert.equal(question.bark_calls,1);
 const confirmed=await update('confirm');assert.equal(confirmed.model_calls,first.model_calls+1);assert.equal(confirmed.pending,2);assert.equal(confirmed.bark_calls,2);
 assert.equal(confirmed.bark[1].level,'timeSensitive');assert.equal(confirmed.unresolved,1);
 const repeated=await update('repeat');assert.equal(repeated.bark_calls,2);assert.equal(repeated.model_calls,confirmed.model_calls);checks.push('question-zero-calls-confirm-once-same-topic-merge-bark-once');
 await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'深色',exact:true}).click();
 await page.goto(base+'/?personal_view=wechat-inbox');await page.getByRole('button',{name:'课程上课地点改为二田（合成）',exact:true}).waitFor();
 assert.equal(await page.locator('.wechat-candidate').count(),2);await noOverflow();await page.screenshot({path:path.join(out,'quality-mobile-dark.png'),fullPage:true});
 await page.getByRole('button',{name:'今天',exact:true}).click();await page.getByRole('button',{name:'查看信息：成绩查询方式（合成）',exact:true}).waitFor();await noOverflow();
 await page.screenshot({path:path.join(out,'quality-mobile-knowledge.png'),fullPage:true});checks.push('mobile-dark-action-and-knowledge-no-overflow');
 const final=await state();assert.equal(final.events,0);assert.equal(final.messages_sent,0);assert.equal(final.unauthorized_message_bodies_read,0);assert.deepEqual(errors,[]);
 const report={passed:true,checks,errors,initial:first,final};await writeFile(path.join(out,'quality-browser-results.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({passed:true,checks,errors}));
} finally {await browser.close()}
