// Browser plugin not available. Production builds, isolated SQLite and mocked
// owner/device transport; no production requests, credentials or notifications.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {chromium,expect} from 'playwright/test';
import {randomUUID} from 'node:crypto';

const WIN=process.env.ERIC_PHASE9_WINDOWS||'http://127.0.0.1:18796';
const PWA=process.env.ERIC_PHASE9_PWA||'http://127.0.0.1:18797';
const OUTPUT=process.env.ERIC_PHASE9_EVIDENCE||'D:/Eric OS/phase9-planner/evidence/browser';
await mkdir(OUTPUT,{recursive:true});
const browser=await chromium.launch({headless:true});
const checks=[],errors=[],external=[],consoleErrors=[];
const watch=page=>{page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text());});};
const shot=(page,name)=>page.screenshot({path:resolve(OUTPUT,name+'.png'),fullPage:true,animations:'disabled'});
const noOverflow=async page=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
let lastConfirm;
try {
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  const page=await context.newPage();watch(page);
  await context.request.post(WIN+'/__test__/reopen');
  const state=await (await context.request.get(WIN+'/__test__/state')).json();assert.equal(state.synthetic_only,true);
  await page.goto(WIN+'/?personal_view=list&planner_list=tasks');
  const list=page.locator('.planner-tasks');
  await expect(list.getByRole('heading',{name:'待办任务 · 26',exact:true})).toBeVisible();
  assert.equal(await list.locator('li').count(),20);await expect(list.getByText('未设截止日期',{exact:true}).first()).toBeVisible();
  await list.getByRole('button',{name:'下一页',exact:true}).click();
  await expect(list.getByText('第 2 页 · 共 26 个任务')).toBeVisible();assert.equal(await list.locator('li').count(),6);
  await list.getByRole('button',{name:'上一页',exact:true}).click();
  await list.getByRole('checkbox',{name:'完成：合成逾期任务',exact:true}).click();
  await expect(list.getByRole('heading',{name:'待办任务 · 25',exact:true})).toBeVisible();
  await list.getByRole('combobox',{name:'任务状态'}).selectOption('done');
  await expect(list.getByRole('checkbox',{name:'恢复未完成：合成逾期任务',exact:true})).toBeChecked();
  const restore=list.getByRole('checkbox',{name:'恢复未完成：合成逾期任务',exact:true});await restore.focus();await restore.press('Space');
  await expect(list.getByText(/这个范围暂无任务/)).toBeVisible();
  await list.getByRole('combobox',{name:'任务状态'}).selectOption('todo');
  await expect(list.getByRole('heading',{name:'待办任务 · 26',exact:true})).toBeVisible();
  await shot(page,'windows-tasks');checks.push('Windows: accurate pagination, undated/overdue tasks, completion and completed filter');
  await page.goto(WIN+'/?personal_view=today');
  await expect(page.getByRole('heading',{name:'今天 / 逾期与提醒',exact:true})).toBeVisible();
  await expect(page.locator('.desk-date-group small').filter({hasText:'合成今天'})).toHaveCount(1);
  await noOverflow(page);await shot(page,'windows-today');checks.push('Windows Today: overdue visible and date nodes occur once');

  await page.setViewportSize({width:390,height:844});
  await page.goto(WIN+'/?personal_view=wechat-inbox');
  await page.locator('.wechat-candidate').getByRole('button',{name:'确认',exact:true}).click();
  const sheet=page.getByRole('dialog',{name:'确认通知',exact:true});
  await sheet.getByRole('checkbox',{name:'保留此日期：申请日期',exact:true}).uncheck();
  await expect(sheet.getByText(/取消日期后，这些行动将未设截止日期：提交申请/)).toBeVisible();
  await expect(sheet.getByRole('combobox',{name:'关联截止时间'}).first()).toHaveValue('');
  await sheet.getByRole('checkbox',{name:'保留行动：补交材料',exact:true}).uncheck();
  await expect(sheet.getByText('将保存 1 个事项、1 个日期；创建 1 个待办任务。',{exact:true})).toBeVisible();
  await sheet.getByText('将保存 1 个事项、1 个日期；创建 1 个待办任务。',{exact:true}).scrollIntoViewIfNeeded();
  await noOverflow(page);await shot(page,'windows-phone-confirmation');
  page.on('request',r=>{if(r.url().endsWith('/confirm')&&r.method()==='POST')lastConfirm=r.postDataJSON();});
  await sheet.getByRole('button',{name:'确认并保存事项',exact:true}).click();
  await expect(page.getByRole('heading',{name:'隔离测试 · 微信确认',exact:true})).toBeVisible();
  const after=await (await context.request.get(WIN+'/__test__/state')).json();
  assert.equal(after.counts.event,state.counts.event+1);assert.equal(after.counts.task,state.counts.task+1);assert.equal(after.counts.deadline,state.counts.deadline+1);
  const retry=await context.request.post(WIN+'/api/personal/raw/'+state.snapshot_id+'/confirm',{data:lastConfirm});assert.equal(retry.ok(),true);
  assert.deepEqual((await (await context.request.get(WIN+'/__test__/state')).json()).counts,after.counts);
  checks.push('Windows phone: action selection, date unlink notice, final confirmation, real SQLite receipt idempotency');
  await context.request.post(WIN+'/__test__/reopen');

  const compiled=await build({entryPoints:[fileURLToPath(new URL('../supabase/functions/eric-app-api/views.ts',import.meta.url))],bundle:true,write:false,format:'esm',platform:'neutral'});
  const {Views}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].contents).toString('base64'));
  let records=[],progress=[],revision=1000,offline=false;
  const refresh=async()=>{records=(await (await context.request.get(WIN+'/__test__/cloud')).json()).records;};await refresh();
  const store={clock:()=>new Date(),rows:async(w,kind)=>kind==='task_progress'?progress:records.filter(r=>r.kind===kind),
    one:async(w,kind,id)=>records.find(r=>r.kind===kind&&r.id===id),
    page:async(w,kind,offset,limit)=>{const rows=records.filter(r=>r.kind===kind);return {items:rows.slice(offset,offset+limit),total:rows.length,limit,offset};}};
  const views=new Views(store,'synthetic');
  const pwa=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,serviceWorkers:'block',reducedMotion:'reduce'});
  const owner=randomUUID(),device=randomUUID(),session=randomUUID(),claims={sub:owner,session_id:session,role:'authenticated',exp:Math.floor(Date.now()/1000)+3600};
  const token=[Buffer.from('{"alg":"HS256","typ":"JWT"}').toString('base64url'),Buffer.from(JSON.stringify(claims)).toString('base64url'),Buffer.alloc(32,1).toString('base64url')].join('.');
  const user={id:owner,email:'synthetic@example.test',aud:'authenticated',role:'authenticated',email_confirmed_at:new Date().toISOString(),app_metadata:{provider:'email',providers:['email']},user_metadata:{},created_at:new Date().toISOString()};
  await pwa.route('https://mantuehuzptactymjpqg.supabase.co/**',async route=>{
    if(offline)return route.abort('internetdisconnected');
    const req=route.request(),url=new URL(req.url()),path=url.pathname.replace(/^\/functions\/v1\/eric-app-api/,'');
    const headers={'Access-Control-Allow-Origin':PWA,'Access-Control-Allow-Headers':'authorization,apikey,content-type,x-client-info,x-supabase-api-version','Access-Control-Allow-Methods':'GET,POST,PUT,DELETE,OPTIONS'};
    const send=(value,status=200)=>route.fulfill({status,headers,contentType:'application/json',body:JSON.stringify(value)});
    if(req.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(url.pathname.startsWith('/auth/v1/token'))return send({access_token:token,refresh_token:'synthetic',expires_in:3600,token_type:'bearer',user});
    if(url.pathname==='/auth/v1/user')return send(user);
    if(path==='/session')return send({account:user,device:{id:device,label:'合成验收设备'},timezone:'Asia/Shanghai'});
    if(path==='/changes')return send({items:[],cursor:revision,more:false});
    if(path==='/activity'||path==='/captures')return send({items:[]});
    if(path==='/system')return send({cloud_available:true,processing_online:false,cursor:revision,waiting:0});
    if(path==='/courses')return send({items:[],total:0});
    if(path==='/learning')return send({courses:[],topics:[],pending_captures:[]});
    if(path==='/dashboard'){
      const tasks=await views.tasks(new URL('?limit=5',PWA));const planning=await views.planning(new URL(PWA),'/planning/today');
      return send({today:state.today,classes:[],tasks:tasks.items,task_count:tasks.total,deadlines:planning.items,reviews:[],needs_confirmation:0});
    }
    if(path.startsWith('/progress/task/')&&req.method()==='PUT'){
      const id=path.split('/').pop(),body=req.postDataJSON(),task=records.find(r=>r.kind==='task'&&r.id===id);
      const row={id,kind:'task_progress',revision:++revision,payload:{event_id:task.payload.event_id,source_status:task.payload.status,source_title:task.payload.title,completed:body.value.completed,application_state:'pending'}};
      progress=[...progress.filter(p=>p.id!==id),row];return send({...row.payload,_cloud:{id,kind:'task_progress',revision:row.revision}});
    }
    if(path==='/planning/tasks'||path==='/planning/upcoming'||path.startsWith('/events'))return send(await views.read(path,url));
    external.push(req.method()+' '+path);return send({message:'Unexpected synthetic request'},404);
  });
  const phone=await pwa.newPage();watch(phone);await phone.goto(PWA+'/?view=tasks');
  await phone.getByRole('textbox',{name:'邮箱',exact:true}).fill(user.email);await phone.locator('input[type=password]').fill('Synthetic-password');
  await phone.getByRole('button',{name:'登录 Eric OS',exact:true}).click();
  const phoneList=phone.locator('.planner-tasks');await expect(phoneList.getByRole('heading',{name:/待办任务/})).toBeVisible();
  await noOverflow(phone);await shot(phone,'pwa-phone-tasks');
  // Offline save -> cloud received -> actual isolated Windows application ->
  // later Windows reopen, retaining the older applied cloud progress record.
  offline=true;await phone.evaluate(()=>{Object.defineProperty(navigator,'onLine',{configurable:true,get:()=>false});window.dispatchEvent(new Event('offline'));});
  await phoneList.getByRole('checkbox',{name:'完成：合成逾期任务',exact:true}).click();
  await expect(phoneList.getByText(/已保存在此设备/).first()).toBeVisible();
  await shot(phone,'pwa-offline-intent');
  offline=false;await phone.evaluate(()=>{Object.defineProperty(navigator,'onLine',{configurable:true,get:()=>true});window.dispatchEvent(new Event('online'));});
  await expect.poll(()=>progress.length,{timeout:15000}).toBe(1);
  await expect(phoneList.getByText('云端已接收，等待 Windows 应用。',{exact:true}).first()).toBeVisible({timeout:15000});
  const pending=progress.find(p=>p.id===state.task_id);assert.ok(pending);
  const applied=await context.request.post(WIN+'/__test__/progress',{data:pending});assert.equal(applied.status(),200);
  pending.payload.application_state='applied';pending.revision=++revision;await refresh();
  await context.request.post(WIN+'/__test__/reopen');await refresh();
  await phone.evaluate(()=>window.dispatchEvent(new Event('eric-data-changed')));
  await expect(phoneList.getByRole('checkbox',{name:'完成：合成逾期任务',exact:true})).not.toBeChecked();
  await expect(phoneList.getByText('已应用；当前显示最新任务状态。',{exact:true}).first()).toBeVisible();
  await shot(phone,'pwa-windows-reopened');
  pending.payload.application_state='needs_confirmation';pending.payload.message='合成版本冲突，请核对。';pending.revision=++revision;
  await phone.evaluate(()=>window.dispatchEvent(new Event('eric-data-changed')));
  await expect(phoneList.getByText('合成版本冲突，请核对。',{exact:true})).toBeVisible();
  await expect(phoneList.getByRole('checkbox',{name:'完成：合成逾期任务',exact:true})).toBeDisabled();
  await expect(phoneList.getByRole('button',{name:'核对后确认标为完成',exact:true})).toBeEnabled();
  await shot(phone,'pwa-conflict');pending.payload.application_state='applied';pending.revision=++revision;
  const event=await (await context.request.get(WIN+'/api/personal/events/'+state.event_id)).json();
  await context.request.post(WIN+'/api/personal/events/'+state.event_id+'/status',{data:{expected_version:event.version,status:'done',confirm_incomplete:true}});await refresh();
  await phone.goto(PWA+'/?view=event&id='+state.event_id);
  await expect(phone.getByRole('button',{name:'恢复事项',exact:true})).toBeVisible();
  for(const input of await phone.locator('.cloud-task-list input[type=checkbox]').all())await expect(input).toBeDisabled();
  const closed=await (await context.request.get(WIN+'/api/personal/events/'+state.event_id)).json();
  await context.request.post(WIN+'/api/personal/events/'+state.event_id+'/restore',{data:{expected_version:closed.version}});await refresh();
  checks.push('PWA: explicit conflict confirmation and closed-parent task controls');
  checks.push('PWA phone: offline device save, received/waiting, actual synthetic Windows apply, newer Windows reopen beats old applied cloud intent');
  await phone.goto(PWA+'/?view=today');await expect(phone.getByRole('heading',{name:/今天 \/ 逾期与提醒/})).toBeVisible();
  await expect(phone.locator('.today-upcoming small').filter({hasText:'合成今天'})).toHaveCount(1);
  await noOverflow(phone);await shot(phone,'pwa-phone-today');checks.push('PWA phone Today: accurate task total and nonduplicated overdue/today/upcoming nodes');
  await phone.setViewportSize({width:1280,height:900});await noOverflow(phone);await shot(phone,'pwa-desktop-today');
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  await writeFile(resolve(OUTPUT,'results.json'),JSON.stringify({synthetic_only:true,browser:'Chromium / Playwright; Browser plugin not available',checks,errors,unexpected_api:external,consoleErrors},null,2));
  console.log(JSON.stringify({checks,errors,unexpected_api:external,consoleErrors},null,2));
  await pwa.close();await context.close();
} finally {await browser.close();}
