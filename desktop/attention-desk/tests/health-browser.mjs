// Browser plugin not available. Production builds + isolated HTTP fixture + Playwright.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {chromium,expect} from 'playwright/test';
import {healthFixture} from './health-fixture.mjs';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const evidence=resolve(process.env.ERIC_HEALTH_EVIDENCE||'artifacts/health-overview');
await mkdir(evidence,{recursive:true});
const servers=[],checks=[],errors=[];
const ports={desktop:18818,pwa:18819};
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function start(kind){
  const runId=randomUUID();
  const child=spawn(process.env.ERIC_TEST_PYTHON||'python',['-m','uvicorn','health_browser_fixture:app','--app-dir','tests','--host','127.0.0.1','--port',String(ports[kind]),'--log-level','warning'],{cwd:root,windowsHide:true,env:{...process.env,PYTHONPATH:root,APP_ENV:'development',APP_RUNTIME:'desktop',HEALTH_TEST_PWA:kind==='pwa'?'1':'0',ERIC_HEALTH_TEST_RUN:runId},stdio:['ignore','pipe','pipe']});
  servers.push(child);let log='';child.stderr.on('data',data=>log+=data);
  const base='http://127.0.0.1:'+ports[kind];
  for(let i=0;i<80;i++){if(child.exitCode!==null)throw Error(log);try{const response=await fetch(base+'/__test__/state');if(response.ok&&(await response.json()).run_id===runId)return base;}catch{}await wait(100);}
  throw Error('Fixture did not start '+log);
}
const owner='11111111-1111-4111-8111-111111111111',device='33333333-3333-4333-8333-333333333333';
async function cloud(context,base){
  const now=new Date(),claims={sub:owner,role:'authenticated',session_id:'44444444-4444-4444-8444-444444444444',exp:Math.floor(now.getTime()/1000)+3600};
  const encode=value=>Buffer.from(JSON.stringify(value)).toString('base64url');
  const token=[encode({alg:'HS256'}),encode(claims),Buffer.alloc(32,1).toString('base64url')].join('.');
  const user={id:owner,email:'synthetic@example.test',aud:'authenticated',role:'authenticated',email_confirmed_at:now.toISOString(),app_metadata:{provider:'email',providers:['email']},user_metadata:{},created_at:now.toISOString()};
  const state={reads:0,mode:'trend',defer:null,owner};
  await context.route('https://mantuehuzptactymjpqg.supabase.co/**',async route=>{
    const url=new URL(route.request().url()),path=url.pathname.replace(/^\/functions\/v1\/eric-app-api/,''),method=route.request().method();
    const headers={'Access-Control-Allow-Origin':base,'Access-Control-Allow-Headers':'authorization,apikey,content-type,x-client-info,x-supabase-api-version','Access-Control-Allow-Methods':'GET,HEAD,POST,PUT,DELETE,OPTIONS','Cache-Control':'private, no-store'};
    const send=(value,status=200)=>route.fulfill({status,headers,contentType:'application/json',body:JSON.stringify(value)});
    if(method==='OPTIONS')return route.fulfill({status:204,headers});
    const currentUser={...user,id:state.owner};
    const currentToken=state.owner===owner?token:[encode({alg:'HS256'}),encode({...claims,sub:state.owner}),Buffer.alloc(32,1).toString('base64url')].join('.');
    if(url.pathname.startsWith('/auth/v1/token'))return send({access_token:currentToken,refresh_token:'synthetic-refresh',expires_in:3600,expires_at:claims.exp,token_type:'bearer',user:currentUser});
    if(url.pathname==='/auth/v1/user')return send(currentUser);
    if(url.pathname.startsWith('/auth/v1/logout'))return send({});
    if(path==='/session')return send({device:{id:device,label:'Synthetic browser',platform:'web'},account:currentUser,timezone:'Asia/Shanghai'});
    if(path==='/health/overview'){
      state.reads++;
      if(state.defer)await state.defer;
      if(state.owner!==owner)return send({detail:{code:'health_not_authorized',message:'此账号尚未连接 HealthData。'}},403);
      if(state.mode==='failure')return send({detail:{code:'health_unavailable',message:'Synthetic upstream unavailable'}},503);
      if(state.mode==='revoked')return send({detail:{code:'device_unavailable',message:'请重新登录。'}},401);
      const value=healthFixture();
      if(state.mode==='summary'){value.mode='summary';for(const metric of value.metrics){delete metric.days;delete metric.chart;}}
      return send(value);
    }
    if(path==='/changes')return send({items:[],cursor:0,more:false});
    if(path==='/activity')return send({items:[]});
    if(path==='/courses')return send({items:[],total:0});
    if(path==='/dashboard')return send({today:healthFixture().window.end,timezone:'Asia/Shanghai',classes:[],deadlines:[],tasks:[],reviews:[],review_count:0,needs_confirmation:0});
    if(path==='/planning/upcoming')return send({items:[],total:0});
    return send({items:[],total:0});
  });
  return state;
}
async function noStoredHealth(page){
  const result=await page.evaluate(async()=>{
    const storage=[...Object.values(localStorage),...Object.values(sessionStorage)].join('\n');
    const names=await indexedDB.databases();const entries=[];
    for(const {name} of names){const db=await new Promise((resolve,reject)=>{const request=indexedDB.open(name);request.onsuccess=()=>resolve(request.result);request.onerror=reject;});
      for(const store of db.objectStoreNames){const rows=await new Promise((resolve,reject)=>{const request=db.transaction(store).objectStore(store).getAll();request.onsuccess=()=>resolve(request.result);request.onerror=reject;});entries.push(...rows);}db.close();}
    const cached=[];for(const name of await caches.keys()){const cache=await caches.open(name);for(const request of await cache.keys()){cached.push(request.url);const response=await cache.match(request);if(response.headers.get('content-type')?.includes('json'))cached.push(await response.text());}}
    return JSON.stringify({storage,entries,cached});
  });
  assert.ok(!result.includes('health-overview.v1'));
  assert.ok(!result.includes('/health/overview'));
  assert.ok(!result.includes('D'.repeat(43))&&!result.includes('C'.repeat(43)));
}

let browser;
try{
  const [desktop,pwa]=await Promise.all([start('desktop'),start('pwa')]);
  browser=await chromium.launch({headless:true});
  for(const [kind,base] of [['desktop',desktop],['pwa',pwa]]){
    const context=await browser.newContext({viewport:{width:1440,height:1050},hasTouch:kind==='pwa'});
    const state=kind==='pwa'?await cloud(context,base):null;
    context.on('page',page=>page.on('pageerror',error=>errors.push(kind+': '+error.message)));
    await context.route('https://health-analyzer.healthdash26.workers.dev/**',route=>{errors.push('Browser must not call HealthData directly');return route.abort();});
    const page=await context.newPage();
    await page.goto(base+(kind==='pwa'?'/?view=health':'/?personal_view=health'));
    if(kind==='pwa'){
      await page.getByLabel('邮箱',{exact:true}).fill('synthetic@example.test');
      await page.getByLabel('密码',{exact:true}).fill('synthetic-password');
      await page.getByRole('button',{name:'登录 Eric OS',exact:true}).click();
    }
    await expect(page.getByRole('heading',{name:'健康概览',exact:true})).toBeVisible();
    await expect(page.locator('.health-metric')).toHaveCount(6);
    await expect(page.locator('.health-chart')).toHaveCount(6);
    await expect(page.getByText('分析已完成',{exact:true})).toBeVisible();
    assert.ok((await page.title()).includes('Health'));
    const before=kind==='pwa'?state.reads:(await (await fetch(desktop+'/__test__/state')).json()).reads;
    assert.equal(before,1);
    await page.getByText('查看每日数值',{exact:true}).first().click();
    await page.getByText('查看每日数值',{exact:true}).first().focus();await page.keyboard.press('Enter');await expect(page.locator('.health-values').first()).not.toHaveAttribute('open','');await page.keyboard.press('Enter');
    await expect(page.locator('.health-values').first().getByText('缺失',{exact:true})).toBeVisible();
    await expect(page.locator('.health-values').first().getByText('0 小时',{exact:true})).toBeVisible();
    assert.equal(await page.locator('.health-metric').first().locator('.health-chart-line').count(),4,'missing day must break line');
    const link=page.getByRole('link',{name:'打开 HealthData'});assert.equal(await link.getAttribute('href'),'https://health-analyzer.healthdash26.workers.dev/');assert.equal(await link.getAttribute('rel'),'noopener noreferrer');
    await noStoredHealth(page);
    for(const [width,height,label] of [[1440,1050,'desktop'],[768,1024,'tablet'],[390,844,'phone']]){
      await page.setViewportSize({width,height});
      if(width===390&&kind==='pwa'){await page.getByText('查看每日数值',{exact:true}).first().tap();await expect(page.locator('.health-values').first()).not.toHaveAttribute('open','');await page.getByText('查看每日数值',{exact:true}).first().tap();}
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),kind+' overflow at '+width);
      await page.screenshot({path:resolve(evidence,kind+'-'+label+'.png'),fullPage:true});
    }
    await page.emulateMedia({colorScheme:'dark',reducedMotion:'reduce'});await page.screenshot({path:resolve(evidence,kind+'-dark.png'),fullPage:true});
    await page.setViewportSize({width:1440,height:1050});await page.evaluate(()=>document.documentElement.style.zoom='2');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.evaluate(()=>document.documentElement.style.zoom='');
    // A visible 16-second interval must not reproduce HealthData's legacy 15s polling.
    await page.clock.install();await page.clock.runFor(16_100);
    const after=kind==='pwa'?state.reads:(await (await fetch(desktop+'/__test__/state')).json()).reads;assert.equal(after,before);
    await page.getByRole('button',{name:'刷新',exact:true}).click();await expect(page.locator('.health-metric')).toHaveCount(6);
    await page.evaluate(()=>window.dispatchEvent(new Event('offline')));await expect(page.locator('.health-metric')).toHaveCount(0);
    await page.evaluate(()=>window.dispatchEvent(new Event('online')));await expect(page.locator('.health-metric')).toHaveCount(0);
    await page.getByRole('button',{name:'刷新',exact:true}).click();await expect(page.locator('.health-metric')).toHaveCount(6);
    await page.clock.setFixedTime(new Date(Date.now()+86400_000));await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
    await expect(page.locator('.health-metric')).toHaveCount(0);await expect(page.getByText('已进入新的一天，点击刷新查看最近七天。',{exact:true})).toBeVisible();
    await page.clock.setFixedTime(new Date());
    if(kind==='pwa')state.mode='summary';else await fetch(desktop+'/__test__/mode/summary',{method:'POST'});
    await page.getByRole('button',{name:'刷新',exact:true}).click();await expect(page.locator('.health-metric')).toHaveCount(6);await expect(page.locator('.health-chart')).toHaveCount(0);
    if(kind==='pwa')state.mode='failure';else await fetch(desktop+'/__test__/mode/failure',{method:'POST'});
    await page.getByRole('button',{name:'刷新',exact:true}).click();await expect(page.getByText('暂时无法读取健康摘要，请刷新或打开 HealthData。',{exact:true})).toBeVisible();await expect(page.locator('.health-metric')).toHaveCount(0);
    if(kind==='pwa'){
      state.mode='trend';let release;state.defer=new Promise(resolve=>release=resolve);
      await page.getByRole('button',{name:'刷新',exact:true}).click();
      await page.evaluate(()=>window.dispatchEvent(new CustomEvent('eric-auth-required',{detail:{message:'Synthetic revoked'}})));release();state.defer=null;
      await expect(page.getByRole('button',{name:'登录 Eric OS',exact:true})).toBeVisible();await expect(page.locator('.health-metric')).toHaveCount(0);
      state.owner='55555555-5555-4555-8555-555555555555';
      await page.getByLabel('邮箱',{exact:true}).fill('other-synthetic@example.test');await page.getByLabel('密码',{exact:true}).fill('other-synthetic-password');
      await page.getByRole('button',{name:'登录 Eric OS',exact:true}).click();
      await expect(page.getByRole('heading',{name:'健康概览',exact:true})).toBeVisible();
      await expect(page.getByText('暂时无法读取健康摘要，请刷新或打开 HealthData。',{exact:true})).toBeVisible();
      await expect(page.locator('.health-metric')).toHaveCount(0);
    } else {
      await fetch(desktop+'/__test__/mode/trend',{method:'POST'});
      await page.getByRole('button',{name:'刷新',exact:true}).click();await expect(page.locator('.health-metric')).toHaveCount(6);
      await page.getByRole('button',{name:'工程作品集',exact:true}).click();await expect(page.locator('.health-metric')).toHaveCount(0);
    }
    await noStoredHealth(page);checks.push(kind+': values, gaps, dates, layout, cache absence, manual refresh, midnight, offline, summary/failure, privacy clear');
    await context.close();
  }
  assert.deepEqual(errors,[]);await writeFile(resolve(evidence,'results.json'),JSON.stringify({checks,errors},null,2));console.log(JSON.stringify({passed:checks,evidence},null,2));
}finally{await browser?.close();for(const server of servers)server.kill();}
