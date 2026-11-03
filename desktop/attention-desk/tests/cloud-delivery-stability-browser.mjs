// Browser plugin not available. Test the actual transport with real IndexedDB,
// synthetic auth/API responses and no production account or learner data.
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
import {expect} from 'playwright/test';
import {createServer} from 'node:http';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const OWNER='11111111-1111-4111-8111-111111111111',DEVICE='22222222-2222-4222-8222-222222222222';
const fixture={name:'synthetic-auth',setup(builder){builder.onResolve({filter:/^@supabase\/supabase-js$/},()=>({path:'auth',namespace:'fixture'}));builder.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:`export const createClient=()=>({auth:{async getSession(){return {data:{session:{user:{id:'${OWNER}'},access_token:'synthetic-test-only-token'}}};}}});`,loader:'js'}));}};
const bundle=await build({entryPoints:['src/cloud/client.js'],bundle:true,write:false,format:'iife',globalName:'EricTransport',platform:'browser',target:'es2022',plugins:[fixture]});
const server=createServer((req,res)=>{res.writeHead(200,{'Content-Type':'text/html'});res.end('<!doctype html><title>Eric OS backend transport fixture</title><p>Synthetic transport fixture</p>');});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const origin='http://127.0.0.1:'+server.address().port;
const engine=process.env.ERIC_WEB_ENGINE==='webkit'?webkit:chromium;
const browser=await engine.launch({headless:true});
const results=[];
try {
  const context=await browser.newContext();let failures=true,status=503,code='data_unavailable';
  const sent=[],requests=[],errors=[],captures=new Map();let lostAck=true,partUploads=0;
  await context.route('https://mantuehuzptactymjpqg.supabase.co/**',async route=>{
    const path=new URL(route.request().url()).pathname.split('/eric-app-api')[1];
    let value={};let responseStatus=200;
    if(path==='/session')value={device:{id:DEVICE},account:{email:'synthetic@example.invalid'}};
    else if(path==='/system')value={cursor:0};
    else if(path?.startsWith('/changes'))value={cursor:0,items:[]};
    else if(path==='/captures'||path==='/commands'){
      const body=route.request().postDataJSON();requests.push({path,body});
      if(failures){responseStatus=status;value={detail:{code,message:'Synthetic provider failure'}};}
      else if(path==='/captures'){
        if(!captures.has(body.id)){sent.push(body);captures.set(body.id,{...body,state:'uploading',received_at:null});}
        value=captures.get(body.id);
      }
      else value={id:body.id,state:'pending'};
    } else if(path?.match(/^\/files\/.+\/parts$/))value={complete:false,items:[]};
    else if(path?.match(/^\/files\/.+\/parts\/\d+$/)){partUploads++;value={saved:true};}
    else if(path?.match(/^\/files\/.+\/finish$/))value={complete:true};
    else if(path?.match(/^\/captures\/.+\/commit$/)){
      value=captures.get(path.split('/')[2]);value.state='received';value.received_at='2026-11-03T00:00:00Z';
      if(lostAck){lostAck=false;await route.abort('failed');return;}
    } else if(path?.startsWith('/commands/'))value={state:'pending'};
    else throw new Error('Unexpected fixture route '+path);
    await route.fulfill({status:responseStatus,contentType:'application/json',headers:{'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Headers':'*'},body:JSON.stringify(value)});
  });
  let page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.goto(origin);await page.addScriptTag({content:bundle.outputFiles[0].text});
  assert.equal(await page.title(),'Eric OS backend transport fixture');
  await page.evaluate(async()=>{await EricTransport.restoreIdentity();EricTransport.startSync();});
  const id=await page.evaluate(async()=>{
    const item=await EricTransport.capture({title:'Synthetic retry',text:'Retain this synthetic source across a connection outage.',
      files:[new File(['Synthetic file retained'],'note.txt',{type:'text/plain'})]});return item.id;
  });
  const row=()=>page.evaluate(id=>EricTransport.local.outbox.get(id),id);
  for(let attempt=1;attempt<=5;attempt++){
    await expect.poll(async()=>{
      const current=await row();
      if((current?.attempts||0)<attempt)await page.evaluate(async id=>{await EricTransport.local.outbox.update(id,{nextAttempt:0});await EricTransport.pump();},id);
      return (await row())?.attempts;
    }).toBe(attempt);
  }
  const failed=await row();assert.equal(failed.state,'failed',JSON.stringify({state:failed.state,attempts:failed.attempts,retryBase:failed.retryBase,error:failed.error}));assert.equal(failed.error.code,'retry_exhausted');
  assert.equal(failed.attempts,5);assert.ok(failed.lastAttemptAt&&failed.completedAt&&failed.text);
  const before=requests.length;await page.evaluate(async()=>{await EricTransport.pump();await EricTransport.pump();});
  assert.equal(requests.length,before);results.push({bounded_failure:true,attempts:5,original_retained:true});
  const retained=await page.evaluate(async id=>(await EricTransport.captureLocalFile(id,0)).blob.text(),id);
  assert.equal(retained,'Synthetic file retained');
  await page.evaluate(()=>EricTransport.stopSync());await page.close();
  page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.goto(origin);await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(async()=>{await EricTransport.restoreIdentity();});
  assert.equal((await row()).state,'failed');
  assert.equal(await page.evaluate(async id=>(await EricTransport.captureLocalFile(id,0)).blob.text(),id),retained);
  failures=false;
  await page.evaluate(()=>{EricTransport.startSync();window.dispatchEvent(new Event('online'));});
  await expect.poll(async()=>{
    if((await row())?.state==='local_pending')await page.evaluate(async id=>{await EricTransport.local.outbox.update(id,{nextAttempt:0});await EricTransport.pump();},id);
    return (await row())?.state;
  }).toBe('received');
  const recovered=await row();assert.equal(recovered.id,id);assert.equal(recovered.attempts,6);assert.equal(recovered.text,failed.text);
  assert.equal(sent.length,1);assert.ok(requests.filter(x=>x.path==='/captures').every(x=>x.body.id===id));
  assert.equal(captures.size,1);assert.equal(partUploads,1);
  results.push({reopen_preserved_file_bytes:true,reconnect_resumed_same_identity:true,lost_ack_did_not_reupload:true,cumulative_failures_preserved:true});
  failures=true;status=500;code='schema_mismatch';
  const command=await page.evaluate(async()=>EricTransport.saveCommand('/courses',{name:'Synthetic course'},{target:'course:new',expectedRevision:0}));
  await expect.poll(async()=>(await page.evaluate(id=>EricTransport.local.commands.get(id),command.id))?.state).toBe('needs_confirmation');
  const permanent=await page.evaluate(id=>EricTransport.local.commands.get(id),command.id);
  assert.equal(permanent.attempts,1);assert.equal(permanent.error.code,'schema_mismatch');
  const stable=requests.length;await page.evaluate(async()=>{await EricTransport.pump();});assert.equal(requests.length,stable);
  results.push({permanent_schema_error_stopped:true,command_retained:true});
  assert.deepEqual(errors,[]);await page.evaluate(()=>EricTransport.stopSync());await context.close();
} finally {await browser.close();await new Promise(r=>server.close(r));}
const output=process.env.ERIC_WEB_EVIDENCE||'D:/Eric OS/test-data/stability-browser';
await mkdir(output,{recursive:true});await writeFile(resolve(output,(process.env.ERIC_WEB_ENGINE||'chromium')+'-transport.json'),JSON.stringify({passed:true,browser_path:'Browser plugin not available; regular Playwright',results},null,2));
process.stdout.write(JSON.stringify({passed:true,results,output})+'\n');
