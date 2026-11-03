// Browser plugin is unavailable. Playwright exercises actual React, HTTP and
// SQLite with synthetic messages; model, OCR and phone services stay mocked.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {chromium} from 'playwright';
const base=process.env.ATTENTION_QA_URL||'http://127.0.0.1:18768';
const out=process.env.ATTENTION_QA_OUTPUT;await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
page.setDefaultTimeout(12000);
const errors=[];page.on('pageerror',e=>errors.push(e.message));const checks=[];
const state=async()=>{const r=await page.request.get(base+'/__test__/state');const data=await r.json();assert.equal(data.synthetic_only,true);return data};
const noOverflow=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
try{
 const first=await state();assert.deepEqual(first.calls,[]);assert.equal(first.media,3);
 await page.goto(base+'/?personal_view=today');await page.locator('.desk-media-entry').waitFor();await page.getByRole('button',{name:'浅色',exact:true}).click();
 assert.match(await page.locator('.desk-media-entry').innerText(),/3 份附件/);
 await page.screenshot({path:path.join(out,'media-home-pending.png'),fullPage:true});
 await page.locator('.desk-media-entry').click();
 const disclosure=page.locator('.desk-holding-area details').filter({hasText:'未解析附件 · 3'});await disclosure.waitFor();assert.equal(await disclosure.getAttribute('open'),'');
 await page.getByRole('button',{name:'查看信息：未解析附件',exact:true}).click();
 let sheet=page.getByRole('dialog',{name:'未解析附件',exact:true});await sheet.waitFor();
 assert.equal(await sheet.getByRole('button',{name:'确认',exact:true}).count(),0);
 await sheet.getByText('查看原始信息与附件',{exact:true}).click();await sheet.locator('.personal-document-files li').first().waitFor();
 assert.equal(await sheet.locator('.personal-document-files li').count(),3);
 await sheet.getByRole('button',{name:'查看图片',exact:true}).first().click();
 const image=sheet.locator('.personal-document-files img').first();await image.waitFor();await image.evaluate(im=>im.decode());
 await page.screenshot({path:path.join(out,'media-pending-evidence.png')});await page.keyboard.press('Escape');
 checks.push('light-entry-groups-three-images-original-preview-readonly');
 let r=await page.request.post(base+'/__test__/recover');assert.ok(r.ok());let next=await r.json();assert.deepEqual(next.calls,['vision','text']);assert.equal(next.pending,1);assert.equal(next.media,0);
 await page.reload();await page.locator('.wechat-candidate').waitFor();assert.equal(await page.locator('.wechat-candidate').count(),1);assert.match(await page.locator('.wechat-candidate').innerText(),/包含 3 份附件/);
 await page.getByRole('button',{name:'实验室安全学习与承诺书（合成）',exact:true}).click();
 sheet=page.getByRole('dialog');await sheet.waitFor();await sheet.getByText('原始微信与 AI 分析',{exact:true}).click();await sheet.locator('.personal-document-files li').first().waitFor();
 assert.equal(await sheet.locator('.personal-document-files li').count(),3);
 assert.match(await sheet.innerText(),/重要通知见图|合成媒体验收群/);
 const picker=sheet.getByLabel('查看来源位置');const opts=await picker.locator('option').allTextContents();assert.ok(opts.some(t=>t.includes('视觉转写')));assert.ok(opts.some(t=>t.includes('OCR')));
 const visionValue=await picker.locator('option').filter({hasText:'视觉转写'}).first().getAttribute('value');await picker.selectOption(visionValue);
 await sheet.locator('.personal-source-image img').last().evaluate(im=>im.decode());await sheet.locator('.personal-source-image').last().scrollIntoViewIfNeeded();assert.match(await sheet.locator('.personal-original-text').innerText(),/请完成实验室安全学习/);
 await page.screenshot({path:path.join(out,'media-classified-evidence.png')});checks.push('one-image-action-with-all-originals-ocr-vision-context');
 await page.keyboard.press('Escape');
 await page.getByRole('button',{name:'今天',exact:true}).click();await page.locator('.wechat-candidate').waitFor();assert.equal(await page.locator('.desk-media-entry').count(),0);
 await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'深色',exact:true}).click();await noOverflow();await page.screenshot({path:path.join(out,'media-mobile-dark.png'),fullPage:true});
 r=await page.request.post(base+'/__test__/recover');assert.ok(r.ok());const final=await r.json();assert.deepEqual(final.calls,['vision','text']);assert.equal(final.events,0);assert.deepEqual(errors,[]);
 checks.push('resolved-entry-disappears-mobile-dark-no-repeat-no-auto-confirm');
 await writeFile(path.join(out,'media-browser-results.json'),JSON.stringify({passed:true,checks,errors,first,final},null,2));console.log(JSON.stringify({passed:true,checks,errors}));
}catch(e){await page.screenshot({path:path.join(out,'media-browser-failure.png')});throw e}
finally{await browser.close()}
