// Manual acceptance of the unchanged Inbox layout using two private API exports.
// No browser connector is available; Playwright runs only a loopback fixture server.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {resolve} from 'node:path';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {chromium} from 'playwright';

assert(process.env.ERIC_PDF_FIXTURE_DIR,'Set ERIC_PDF_FIXTURE_DIR to private historical-inbox.json and new-inbox.json exports.');
const root=process.cwd(),out=resolve(process.env.ERIC_PDF_FIXTURE_DIR,'browser');
await mkdir(out,{recursive:true});
const fixtures=await Promise.all(['historical','new'].map(async name=>JSON.parse(await readFile(resolve(process.env.ERIC_PDF_FIXTURE_DIR,name+'-inbox.json'),'utf8'))));
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
assets.set('/pdf.worker.min.mjs',worker);
const html='<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"><div class="desk-shell"><main id="root" class="desk-main personal-workspace"></main></div><script type="module" src="/app.js"></script></html>';
const server=createServer((req,res)=>{
  const path=new URL(req.url,'http://127.0.0.1').pathname;
  if(path==='/'){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);return;}
  if(assets.has(path)){res.setHeader('Content-Type',path.endsWith('.css')?'text/css':'text/javascript');res.end(assets.get(path));return;}
  res.statusCode=404;res.end();
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1280,height:900}});const errors=[];
page.on('pageerror',error=>errors.push(error.message));
const report={fixture_ids:fixtures.map(row=>row.id),production_requests:0,checks:[]};
await page.route('**/*',route=>{
  if(new URL(route.request().url()).hostname==='127.0.0.1')return route.continue();
  report.production_requests++;return route.abort();
});
try {
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.getByText('分析已完成',{exact:true}).waitFor();
  await page.getByText('待确认信息',{exact:true}).waitFor();
  assert.equal(await page.getByText('等待 AI 分析',{exact:false}).count(),0);
  await page.screenshot({path:resolve(out,'real-two-sample-list.png'),fullPage:true});
  report.checks.push('Real saved review summary and new needs_context reason appear in list.');
  await page.locator('.eric-inbox-list button').first().click();
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
  // Presentation-only variants: never persisted as sample analysis or model evidence.
  for(const [status,message,label] of [['paused','AI 处理已暂停。','已暂停'],['failed','本地解析失败，原件保留。','处理失败'],['completed','笔记分析已完成，复盘待核对。','分析已完成']]) {
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
  await page.getByText('待确认信息',{exact:true}).waitFor();
  await page.screenshot({path:resolve(out,'mobile-two-sample-list.png'),fullPage:true});
  report.reads=await page.evaluate(()=>window.__reads);
  assert.deepEqual(errors,[]);assert.equal(report.production_requests,0);report.console_errors=errors;report.passed=true;
  await writeFile(resolve(out,'result.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
} finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
