// Manual acceptance of the unchanged Inbox layout using two private API exports.
// No browser connector is available; Playwright runs only a loopback fixture server.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {resolve} from 'node:path';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import {build} from 'esbuild';
import {chromium} from 'playwright';

assert(process.env.ERIC_PDF_FIXTURE_DIR,'Set ERIC_PDF_FIXTURE_DIR to private historical-inbox.json and new-inbox.json exports.');
const root=process.cwd(),out=resolve(process.env.ERIC_PDF_FIXTURE_DIR,'browser');
await mkdir(out,{recursive:true});
const fixtures=await Promise.all(['historical','new'].map(async name=>JSON.parse(await readFile(resolve(process.env.ERIC_PDF_FIXTURE_DIR,name+'-inbox.json'),'utf8'))));
const filing=fixtures[1].note_processing.mode==='note-course-tags-v1';
const newLabel={needs_context:filing?'待确认':'待确认信息',needs_review:'待核对',paused:'已暂停',failed:'处理失败',completed:filing?'已分类':'分析已完成'}[fixtures[1].note_processing.status];
assert(newLabel,'Expected a recorded terminal or paused sample state');
const require=createRequire(import.meta.url);
const worker=await readFile(require.resolve('pdfjs-dist/legacy/build/pdf.worker.min.mjs'));
const compiled=await build({stdin:{contents:`
import React from 'react';
import {createRoot} from 'react-dom/client';
import EricInbox from './src/personal/EricInbox.jsx';
import {setPersonalTransport} from './src/personal/api.js';
import './src/styles.css';
import './src/personal/personal.css';
import './src/personal/desk.css';
window.__rows=${JSON.stringify(fixtures)};
window.__reads=[];
setPersonalTransport(async(path,options={})=>{
  if(options.method && options.method!=='GET') throw new Error('Writes forbidden in browser acceptance');
  window.__reads.push(path);
  const url=new URL(path,location.origin);
  if(url.pathname==='/inbox') {
    const status=url.searchParams.get('status');
    const rows=window.__rows.filter(r=>status==='completed'?r.processing_status==='completed':status==='needs_review'?r.needs_manual:true);
    return {items:rows,total:rows.length,limit:20,offset:0};
  }
  const row=window.__rows.find(row=>url.pathname==='/inbox/'+row.id);
  if(row)return structuredClone(row);
  throw new Error('Unexpected read '+path);
});
createRoot(document.getElementById('root')).render(<EricInbox cloud onNavigate={()=>{}} onBusyChange={()=>{}} onDirtyChange={()=>{}} />);
`,resolveDir:root,loader:'jsx'},bundle:true,write:false,outdir:out,entryNames:'app',format:'esm',jsx:'automatic',define:{'process.env.NODE_ENV':'"production"'},loader:{'.woff2':'dataurl','.woff':'dataurl'},plugins:[{name:'vite-worker-url',setup(b){
  b.onResolve({filter:/pdf\.worker\.min\.mjs\?url$/},()=>({path:'pdf-worker-url',namespace:'fixture-asset'}));
  b.onLoad({filter:/.*/,namespace:'fixture-asset'},()=>({contents:'export default "/pdf.worker.min.mjs";',loader:'js'}));
}}]});
const assets=new Map(compiled.outputFiles.map(file=>['/'+file.path.split(/[\\/]/).at(-1),file.contents]));
const originals=new Map(),originalReads=[];
if(process.env.ERIC_PDF_ORIGINAL_DIR) for(const [i,name] of ['historical','new'].entries()) {
  const data=await readFile(resolve(process.env.ERIC_PDF_ORIGINAL_DIR,name+'.pdf'));
  originals.set('/api/personal/attachments/'+fixtures[i].attachments[0].id+'/original',data);
}
assets.set('/pdf.worker.min.mjs',worker);
const html='<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"><div class="desk-shell"><main id="root" class="desk-main personal-workspace"></main></div><script type="module" src="/app.js"></script></html>';
const server=createServer((req,res)=>{
  const path=new URL(req.url,'http://127.0.0.1').pathname;
  if(path==='/'){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);return;}
  if(originals.has(path)){
    const data=originals.get(path);originalReads.push({path,sha256:createHash('sha256').update(data).digest('hex')});
    res.setHeader('Content-Type','application/pdf');res.setHeader('Content-Disposition','inline; filename="original.pdf"');res.end(data);return;
  }
  if(assets.has(path)){res.setHeader('Content-Type',path.endsWith('.css')?'text/css':'text/javascript');res.end(assets.get(path));return;}
  res.statusCode=404;res.end();
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({channel:'chromium',headless:true});
const context=await browser.newContext({viewport:{width:1280,height:900}});
const page=await context.newPage();const errors=[];
page.on('pageerror',error=>errors.push(error.message));
const report={fixture_ids:fixtures.map(row=>row.id),production_requests:0,blocked_requests:[],checks:[]};
await context.route('**/*',route=>{
  const url=new URL(route.request().url());
  if(url.hostname==='127.0.0.1'||url.protocol==='chrome-extension:')return route.continue();
  report.blocked_requests.push(url.origin+url.pathname);
  report.production_requests++;return route.abort();
});
try {
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.getByText('分析已完成',{exact:true}).first().waitFor();
  await page.locator('.eric-inbox-list .personal-status').filter({hasText:new RegExp('^'+newLabel+'$')}).first().waitFor();
  assert.equal(await page.getByText('等待 AI 分析',{exact:false}).count(),0);
  await page.screenshot({path:resolve(out,'real-two-sample-list.png'),fullPage:true});
  report.checks.push('Real saved review summary and new '+fixtures[1].note_processing.status+' reason appear in list.');
  if(originals.size) for(let i=0;i<2;i++) {
    const served=context.waitForEvent('response',{predicate:r=>r.url().includes('/attachments/'+fixtures[i].attachments[0].id+'/original?inline=true')});
    const [popup]=await Promise.all([page.waitForEvent('popup'),page.locator('.eric-inbox-list .personal-event-row').nth(i).click()]);
    const response=await served;assert.equal(response.status(),200);
    assert.equal(response.headers()['content-type'],'application/pdf');
    await popup.waitForURL('**/attachments/'+fixtures[i].attachments[0].id+'/original?inline=true',{waitUntil:'commit'});
    assert.equal(await popup.evaluate(()=>window.opener),null);
    await popup.close();
    assert.equal(await page.locator('.desk-sheet').count(),0);
  }
  if(originals.size) {assert.equal(originalReads.length,2);report.checks.push('Both cards open the corresponding original PDF directly.');}
  await page.getByRole('button',{name:'分类与详情',exact:true}).first().click();
  await page.getByText(fixtures[0].note_processing.message,{exact:true}).waitFor();
  await page.getByText(fixtures[0].note_processing.summary,{exact:true}).waitFor();
  await page.getByText(fixtures[0].note_processing.summary,{exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:resolve(out,'historical-detail.png'),fullPage:true});
  report.checks.push('Historical detail shows the already saved review.');
  await page.goto('http://127.0.0.1:'+server.address().port+'/?inbox_item='+fixtures[1].id);
  await page.getByText(fixtures[1].note_processing.message,{exact:true}).waitFor();
  await page.getByText(fixtures[1].note_processing.message,{exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:resolve(out,'new-detail.png'),fullPage:true});
  report.checks.push('New detail shows the existing workflow reason.');
  if(filing) {
    await page.getByText('知识点：'+fixtures[1].classification.note_classification.knowledge_point,{exact:true}).waitFor();
    await page.getByText('标签：'+fixtures[1].classification.note_classification.tags.join(' · '),{exact:true}).waitFor();
    assert.equal(await page.getByText('本地分类未调用云端 AI。',{exact:false}).count(),0);
    report.checks.push('Saved course/knowledge/tags appear without a formula or textbook prerequisite.');
  }
  // Presentation-only variants: never persisted as sample analysis or model evidence.
  for(const [status,message,label] of [['needs_context','课程尚待确认，原件可查看。',filing?'待确认':'待确认信息'],['paused','AI 处理已暂停。','已暂停'],['failed','本地解析失败，原件保留。','处理失败'],['completed','笔记处理已完成。',filing?'已分类':'分析已完成']]) {
    await page.evaluate(({status,message})=>{
      const row=window.__rows[1];row.note_processing={...row.note_processing,status,message,summary:null};
      row.processing_status=status==='paused'?'needs_review':status;
      window.dispatchEvent(new Event('eric-data-changed'));
    },{status,message});
    await page.getByText(message,{exact:true}).waitFor();
    await page.getByText(label,{exact:true}).waitFor();
    report.checks.push('Detail refresh renders '+status+' (presentation fixture only).');
  }
  await page.setViewportSize({width:390,height:844});
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.locator('.eric-inbox-list .personal-status').filter({hasText:new RegExp('^'+newLabel+'$')}).first().waitFor();
  await page.screenshot({path:resolve(out,'mobile-two-sample-list.png'),fullPage:true});
  report.reads=await page.evaluate(()=>window.__reads);
  report.original_reads=originalReads;
  await writeFile(resolve(out,'result.json'),JSON.stringify(report,null,2));
  assert.deepEqual(errors,[]);assert.equal(report.production_requests,0);report.console_errors=errors;report.passed=true;
  await writeFile(resolve(out,'result.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
} finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
