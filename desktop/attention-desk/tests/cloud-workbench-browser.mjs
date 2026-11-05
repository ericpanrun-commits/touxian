// Browser plugin not available. Actual production build + Playwright; all fixture writes stay in isolated browser/mock storage.
import {chromium,webkit,expect} from 'playwright/test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {offlineOrigin} from './cloud-offline-origin.mjs';
import {pdfFixture} from './cloud-pdf-fixture.mjs';
import {courseWorkspaceFlows,courseNoteConfirmationFlow} from './course-workspaces-browser-flows.mjs';

const BASE=process.env.ERIC_WEB_PREVIEW||'http://127.0.0.1:4175';
const OUTPUT=process.env.ERIC_WEB_EVIDENCE||'D:/Eric OS/backups/web-pwa-v1-20261029/browser-round-one';
const shot=async(page,name)=>{if(process.env.ERIC_SKIP_SCREENSHOTS!=='1'&&(!process.env.ERIC_SCREENSHOT_ONLY||process.env.ERIC_SCREENSHOT_ONLY===name)){await page.waitForLoadState('networkidle');await page.evaluate(async()=>{scrollTo(0,0);await document.fonts.ready;await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});await page.screenshot({path:resolve(OUTPUT,name),fullPage:true,animations:'disabled'});}};
await mkdir(OUTPUT,{recursive:true});
const OWNER='11111111-1111-4111-8111-111111111111',NOW='2026-10-29T08:00:00Z';
const png=Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="840" height="1188"><rect width="840" height="1188" fill="white"/><g fill="#202124" font-family="sans-serif"><text x="70" y="90" font-size="27">Synthetic classroom evidence</text><text x="70" y="140" font-size="18">Isolated browser test. Never published as learner data.</text><path d="M70 174H770" stroke="#ccc"/><text x="70" y="230" font-size="21">Source page 1</text><text x="70" y="290" font-size="20">lim (sin x / x) = 1</text><text x="70" y="340" font-size="17">This page checks reading layout and source navigation.</text></g></svg>');
const state={revision:10,read:false,progressRevision:0,changes:[],captures:new Map(),resources:new Map(),commands:new Map(),reserveCalls:0,partCalls:0,lostCommit:true,windows:false};
const errors=[],unexpected=[],privateRequests=[],failedStatic=[];
const pdf=pdfFixture(),pdfRanges=[];
function changed(kind,id){state.revision++;state.changes.push({revision:state.revision,kind,record_id:id});}
function dto(kind,id,value,revision=1){return {...value,_cloud:{kind,id,revision,updated_at:NOW}};}
const course=dto('course','course-math-ii',{id:'course-math-ii',name:'隔离测试 · 高等数学'});
const generalCourse=dto('course','course-computing',{id:'course-computing',name:'隔离测试 · 程序设计'});
const otherCourse=dto('course','course-history',{id:'course-history',name:'隔离测试 · 历史'});
const courses=[course,generalCourse,otherCourse];
const document={id:'doc-synthetic',attachment_id:'synthetic-pdf',title:'隔离测试教材',page_count:3,status:'ready',mime_type:'application/pdf'};
const textbook=dto('textbook','book-synthetic',{id:'book-synthetic',course_id:course.id,title:'隔离测试教材',version:1,document_id:document.id,document,sections:[],companions:[]});
const books=new Map([[textbook.id,textbook]]);
const lecture=dto('lecture','lecture-synthetic',{id:'lecture-synthetic',course_id:course.id,title:'隔离测试课堂',lecture_date:'2026-10-29',status:'needs_review',version:1,notes:[{...document,raw_item_id:'raw-synthetic'}],sections:[],jobs:[],artifacts:[{id:'review-synthetic',version:1,status:'needs_review',created_at:NOW}],textbook});
function review(){return dto('review','review-synthetic',{id:'review-synthetic',lecture_id:lecture.id,version:1,status:'needs_review',model:'隔离测试',generated_at:NOW,created_at:NOW,adopted:false,feedback:[],sources:[],input_versions:{},content:{before_lecture:null,core_concepts:[{name:'合成阅读材料',explanation:{text:'这段内容只用于隔离界面验证。',provenance:'notes',citations:[]}}],notes_taught:[],must_master:[],method_cards:[],textbook_supplement:[],understanding_gaps:[],connections:[],mistakes:{message:'无合成历史错误',items:[]},self_tests:[{question:'隔离界面问题',answer:'合成参考答案',citations:[]}],suggested_actions:[]},reading:{completed:state.read,_cloud:{revision:state.progressRevision}}});}
const generalLecture=dto('lecture','lecture-computing',{...lecture,id:'lecture-computing',course_id:generalCourse.id,course:generalCourse,title:'隔离测试 · 条件与循环',textbook:null,artifacts:[{id:'review-computing',version:1,status:'needs_review',has_content:true,created_at:NOW}]});
const generalReview=dto('review','review-computing',{...review(),id:'review-computing',lecture_id:generalLecture.id,content:{...review().content,schema_version:'lecture-review-general-v1',source_scope:'notes_only',core_concepts:[],notes_taught:[{text:'这份合成笔记只记录了循环条件。',provenance:'notes',citations:[]}],self_tests:[]}});
const snapshot={id:'snapshot-synthetic',raw_item_id:'raw-synthetic',original_text:'隔离测试通知原文。',created_at:NOW,source_name:'隔离测试来源',attachments:[],source_blocks:[],attachment_jobs:[]};
const inbox=dto('inbox','raw-synthetic',{id:'raw-synthetic',title:'隔离测试输入',preview:snapshot.original_text,created_at:NOW,input_kind:'text',source:'隔离测试来源',processing_status:'completed',processing_stage:'completed',snapshot_id:snapshot.id,original_snapshot_id:snapshot.id,version:1,managed:true,needs_manual:false,proposal_count:0,attachments:[],receipts:[],original:snapshot,review_available:true,review:{snapshot,extraction:null,course_suggestions:{},status:'pending',version:1},extraction_quality:{files:[],reasons:[]},school_documents:[]});
const bookInput=dto('inbox','raw-computing-book',{...inbox,id:'raw-computing-book',title:'隔离测试 · 程序设计教材.pdf',input_kind:'pdf',course:generalCourse});
const pendingNote=dto('learning_capture','capture-course-confirmation',{id:'capture-course-confirmation',raw_item_id:inbox.id,status:'needs_context',context:{note_share:true},parsed:{questions:[]},error:null});
const learning={today:'2026-10-29',courses,preview:null,preview_sessions:[],recent_lectures:[],classroom:{total:0,correct:0,incorrect:0,unknown:0},received_without_date:{total:0,correct:0,incorrect:0,unknown:0},weak:[],progress:[],history:[],topics:[],pending_captures:[pendingNote],due:[],upcoming:[],total_due:0,history_pending_diagnostic:0,limits:{topics_total:0,topics_shown:0},background_analysis:{enabled:false,available:false,daily_request_limit:0,monthly_estimate_limit_cny:0}};
const event=dto('event','event-synthetic',{id:'event-synthetic',title:'隔离测试事项',summary:'只用于测试，不写入学习数据。',version:1,status:'needs_action',tasks:[dto('task','task-synthetic',{id:'task-synthetic',event_id:'event-synthetic',title:'隔离测试任务',status:'todo',progress:{_cloud:{revision:0}}},8)],deadlines:[],raw_item_id:'raw-synthetic'});
function list(items,url){const limit=Number(url.searchParams.get('limit')||20),offset=Number(url.searchParams.get('offset')||0);return {items:items.slice(offset,offset+limit),total:items.length,offset,limit};}

async function environment(browser,viewport,label){
  // WebKit-controlled requests can bypass Playwright route interception. Test
  // the actual service worker separately, without an online auth/data mock.
  const context=await browser.newContext({viewport,deviceScaleFactor:1,hasTouch:label!=='desktop',serviceWorkers:process.env.ERIC_WEB_ENGINE==='webkit'?'block':'allow'}),device=randomUUID(),session=randomUUID(),network={offline:false};
  await context.addInitScript(()=>{window.ericAuthDiagnostics=[];for(const event of ['eric-auth-error','eric-local-save-error'])window.addEventListener(event,item=>window.ericAuthDiagnostics.push({event,...item.detail}));});
  const claims={sub:OWNER,role:'authenticated',session_id:session,exp:Math.floor(Date.now()/1000)+3600};
  const token=[Buffer.from('{"alg":"HS256","typ":"JWT"}').toString('base64url'),Buffer.from(JSON.stringify(claims)).toString('base64url'),Buffer.alloc(32,1).toString('base64url')].join('.');
  const user={id:OWNER,email:'synthetic@example.test',aud:'authenticated',role:'authenticated',email_confirmed_at:NOW,app_metadata:{provider:'email',providers:['email']},user_metadata:{},created_at:NOW};
  context.on('page',page=>{page.on('pageerror',error=>errors.push(label+': '+error.message));page.on('requestfailed',req=>{if(req.url().startsWith(BASE))failedStatic.push({path:new URL(req.url()).pathname,error:req.failure()?.errorText});});page.on('request',request=>{if(/https?:\/\/(?:192\.168\.|10\.|172\.(?:1[6-9]|2\d|3[01])\.|localhost)/.test(request.url()))privateRequests.push(request.url());});});
  await context.route('https://mantuehuzptactymjpqg.supabase.co/**',async route=>{
    if(network.offline)return route.abort('internetdisconnected');
    const req=route.request(),url=new URL(req.url()),method=req.method(),path=url.pathname.replace(/^\/functions\/v1\/eric-app-api/,'');
    const headers={'Access-Control-Allow-Origin':BASE,'Access-Control-Allow-Headers':'authorization,apikey,content-type,x-client-info,x-supabase-api-version','Access-Control-Allow-Methods':'GET,HEAD,POST,PUT,DELETE,OPTIONS'};
    const send=(value,status=200)=>route.fulfill({status,headers,contentType:'application/json',body:JSON.stringify(value)});
    if(method==='OPTIONS')return route.fulfill({status:204,headers});
    if(url.pathname.startsWith('/auth/v1/token'))return send({access_token:token,refresh_token:'synthetic-refresh-'+session,expires_in:3600,expires_at:claims.exp,token_type:'bearer',user});
    if(url.pathname==='/auth/v1/user')return send(user);
    if(url.pathname.startsWith('/auth/v1/logout'))return send({});
    const body=req.postDataJSON?(()=>{try{return req.postDataJSON();}catch{return null;}})():null;
    if(path==='/session')return send({device:{id:device,label:'隔离测试 '+label,platform:label==='desktop'?'windows':label==='tablet'?'ipad':'iphone'},account:user,timezone:'Asia/Shanghai'});
    if(path==='/system')return send({cloud_available:true,processing_online:state.windows,last_processed_at:NOW,cursor:state.revision,waiting:0});
    if(path==='/planning/upcoming')return send({items:[],total:0,today:'2026-10-29',timezone:'Asia/Shanghai'});
    if(path==='/changes')return send({items:state.changes.filter(item=>item.revision>Number(url.searchParams.get('after')||0)),cursor:state.revision,more:false});
    if(path==='/activity')return send({items:[...state.commands.values()]});
    if(path==='/dashboard')return send({today:'2026-10-29',timezone:'Asia/Shanghai',classes:[{uid:'synthetic-class',title:'隔离测试 · 高数课',start:'2026-10-29T13:30:00+08:00',end:'2026-10-29T15:00:00+08:00',state:'upcoming',course_id:course.id}],deadlines:[],tasks:event.tasks.filter(task=>task.status!=='done').map(task=>({...task,event_id:event.id,event_title:event.title,event_version:1})),reviews:state.read?[]:[{id:'review-synthetic',title:lecture.title,version:1,lecture_date:lecture.lecture_date}],review_count:state.read?0:1,needs_confirmation:0,next_action:{kind:'review',title:'读一份课堂复盘',id:'review-synthetic'}});
    if(path==='/courses')return send(list(courses,url));
    if(path.startsWith('/courses/')){const item=courses.find(item=>item.id===path.split('/')[2]);if(item)return send(item);}
    if(path==='/school')return send({...list([lecture,generalLecture].filter(item=>item.course_id===(url.searchParams.get('course_id')||course.id)).map(item=>dto('lecture_item',item.id,item)),url),textbook:[...books.values()].find(item=>item.course_id===(url.searchParams.get('course_id')||course.id))||null});
    if(path==='/school/textbooks')return send(list([...books.values()].filter(item=>!url.searchParams.get('course_id')||item.course_id===url.searchParams.get('course_id')),url));
    if(path==='/school/textbook')return send([...books.values()].find(item=>item.course_id===(url.searchParams.get('course_id')||course.id))||null);
    if(path==='/school/notes')return send({items:[]});
    if(path==='/documents/'+document.id)return send(dto('document',document.id,document));
    if(path==='/school/lectures/'+lecture.id)return send(lecture);
    if(path==='/school/lectures/'+lecture.id+'/context')return send({missing:[],sources:[],before_lecture:null,needs_vision:[],budget:{},lecture:{id:lecture.id}});
    if(path==='/school/lectures/'+lecture.id+'/cost')return send({estimated_cost_cny:0,max_images:0,max_requests:3});
    if(path==='/school/lectures/'+lecture.id+'/section-suggestions')return send({items:[]});
    if(path==='/school/lectures/'+generalLecture.id)return send(generalLecture);
    if(path==='/school/lectures/'+generalLecture.id+'/context')return send({missing:['尚未关联教材原件。'],review_profile:'general-course-v1',course:generalCourse,sources:[],before_lecture:null,needs_vision:[],budget:{},lecture:{id:generalLecture.id}});
    if(path==='/school/lectures/'+generalLecture.id+'/cost')return send({estimated_cost_cny:0,max_images:0,max_requests:3});
    if(path==='/school/lectures/'+generalLecture.id+'/section-suggestions')return send({items:[]});
    if(path==='/school/reviews/'+generalReview.id)return send(generalReview);
    if(path==='/school/reviews/review-synthetic')return send(review());
    if(path==='/reviews')return send(list([{...review(),has_content:true,title:lecture.title,lecture_date:lecture.lecture_date}],url));
    if(path==='/recent')return send(list([dto('recent_item','review:review-synthetic',{id:'review-synthetic',title:'隔离测试课堂 · 复盘',kind:'review',occurred_at:NOW})],url));
    if(path==='/inbox')return send(list([inbox,bookInput],url));
    if(path==='/inbox/'+inbox.id)return send(inbox);
    if(path==='/events')return send(list([{...event,task_count:1,completed_task_count:0,deadline_count:0}],url));
    if(path==='/events/'+event.id)return send(event);
    if(path==='/devices')return send({items:[{id:device,label:'隔离测试 '+label,platform:label,created_at:NOW,last_seen_at:NOW}],current:device});
    if(path==='/learning')return send(dto('facade','/learning',learning));
    if(path==='/learning/captures/'+pendingNote.id)return send(pendingNote);
    if(path==='/processors')return send({items:[]});
    if(path==='/progress/task/task-synthetic'&&method==='PUT'){event.tasks[0].status=body.value.completed?'done':'todo';changed('task_progress','task-synthetic');event.tasks[0].progress={completed:body.value.completed,_cloud:{revision:state.revision}};return send(dto('task_progress','task-synthetic',event.tasks[0].progress,state.revision));}
    if(path==='/progress/review/review-synthetic'&&method==='PUT'){
      state.read=body.value.completed;changed('review_progress','review-synthetic');state.progressRevision=state.revision;
      return send(dto('review_progress','review-synthetic',{completed:state.read},state.revision));
    }
    if(path==='/commands'&&method==='POST'){const command=state.commands.get(body.id)||{...body,state:'pending',created_at:NOW,revision:++state.revision};state.commands.set(command.id,command);if(state.lostCommand){state.lostCommand=false;return send({detail:{code:'temporarily_unavailable',message:'隔离测试：提交回执中断。'}},503);}return send(command,202);}
    if(path.startsWith('/commands/')&&method==='GET')return send(state.commands.get(path.split('/')[2]));
    if(path==='/captures'&&method==='GET')return send({items:[...state.captures.values()],offset:0,limit:50});
    if(path==='/captures'&&method==='POST'){
      state.reserveCalls++;
      const old=state.captures.get(body.id);if(old)return send(old,201);
      const capture={...body,state:body.files.length?'uploading':'received',created_at:body.shared_at,received_at:body.files.length?null:NOW,revision:++state.revision};state.captures.set(capture.id,capture);
      for(const file of body.files)state.resources.set(file.resource_id,{...file,parts:new Map(),complete:false});return send(capture,201);
    }
    let match;
    if((match=/^\/files\/(.+)\/parts$/.exec(path))&&method==='GET'){const file=state.resources.get(decodeURIComponent(match[1]));return send({complete:file.complete,items:[...file.parts].map(([part_index,bytes])=>({part_index,bytes:bytes.length}))});}
    if((match=/^\/files\/(.+)\/parts\/(\d+)$/.exec(path))&&method==='PUT'){
      state.partCalls++;const id=decodeURIComponent(match[1]),resource=state.resources.get(id);resource.parts.set(Number(match[2]),req.postDataBuffer());return send({saved:true});
    }
    if((match=/^\/files\/(.+)\/finish$/.exec(path))&&method==='POST'){
      const file=state.resources.get(decodeURIComponent(match[1])),bytes=Buffer.concat([...file.parts].sort((a,b)=>a[0]-b[0]).map(row=>row[1]));
      assert.equal(bytes.length,file.bytes);assert.equal(createHash('sha256').update(bytes).digest('hex'),file.sha256);file.complete=true;return send({complete:true});
    }
    if((match=/^\/captures\/([^/]+)\/commit$/.exec(path))&&method==='POST'){
      const capture=state.captures.get(match[1]);assert.ok(capture.files.every(file=>state.resources.get(file.resource_id).complete));capture.state='received';capture.received_at=NOW;capture.revision=++state.revision;changed('capture',capture.id);
      if(state.lostCommit){state.lostCommit=false;return send({detail:{code:'temporarily_unavailable',message:'隔离测试：确认响应丢失，待自动重试。'}},503);}return send(capture);
    }
    if((match=/^\/files\/(.+)\/ticket$/.exec(path))&&method==='POST'){
      const resource=decodeURIComponent(match[1]);
      if(resource==='page:doc-synthetic:3:image')return send({detail:{code:'source_unavailable',message:'按需读取原件'}},404);
      return send({url:BASE+(resource==='attachment:synthetic-pdf:original'?'/fixture-source.pdf':'/fixture-source.png'),expires_in:600});
    }
    unexpected.push(method+' '+path);return send({detail:{code:'fixture_missing',message:'隔离测试尚未提供此路由。'}},404);
  });
  await context.route(BASE+'/fixture-source.png',route=>route.fulfill({contentType:'image/svg+xml',body:png}));
  await context.route(BASE+'/fixture-source.pdf',route=>{
    const range=route.request().headers().range,match=/^bytes=(\d+)-(\d+)$/.exec(range||'');
    const start=match?Number(match[1]):0,end=match?Math.min(Number(match[2]),pdf.length-1):pdf.length-1;
    if(match)pdfRanges.push([start,end]);
    return route.fulfill({status:match?206:200,contentType:'application/pdf',headers:{'Accept-Ranges':'bytes','Content-Length':String(end-start+1),...(match?{'Content-Range':`bytes ${start}-${end}/${pdf.length}`}:{})},body:pdf.subarray(start,end+1)});
  });
  const page=await context.newPage();await page.goto(BASE,{waitUntil:'networkidle'});
  await expect(page.getByRole('heading',{name:'Eric OS',exact:true})).toBeVisible();
  if(label==='phone')await shot(page,'phone-login.png');
  await page.getByLabel('邮箱',{exact:true}).fill('synthetic@example.test');await page.getByLabel('密码',{exact:true}).fill('synthetic-password-only-in-mock');await page.getByRole('button',{name:'登录 Eric OS',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Today',exact:true})).toBeVisible({timeout:15000});await expect(page.locator('.today-timeline').getByText('隔离测试 · 高数课',{exact:true})).toBeVisible();
  return {context,page,label,setOffline:async value=>{
    network.offline=value;
    if(process.env.ERIC_WEB_ENGINE==='webkit'){
      // This WebKit build's native offline emulation also prevents reads of
      // picker-provided local files (verified by the standalone storage probe).
      // Abort API requests for these functional flows; the separate shell case
      // still disables the actual network before reopening the production app.
      for(const active of context.pages())await active.evaluate(offline=>window.dispatchEvent(new Event(offline?'offline':'online')),value);
    }else await context.setOffline(value);
  }};
}

async function webkitOfflineShell(browser,source){
  const snapshot=await source.page.evaluate(async()=>{
    const request=indexedDB.open('eric-os-v1');const db=await new Promise((resolve,reject)=>{request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});
    const names=[...db.objectStoreNames],transaction=db.transaction(names),stores=[];
    for(const name of names){const store=transaction.objectStore(name),schema={name,keyPath:store.keyPath,autoIncrement:store.autoIncrement,indexes:[...store.indexNames].map(key=>{const index=store.index(key);return {name:key,keyPath:index.keyPath,unique:index.unique,multiEntry:index.multiEntry};})};
      const rows=['identity','cache'].includes(name)?store.getAll():null;stores.push({schema,request:rows});}
    const rows=await Promise.all(stores.map(item=>item.request?new Promise((resolve,reject)=>{item.request.onsuccess=()=>resolve(item.request.result);item.request.onerror=()=>reject(item.request.error);}):Promise.resolve([])));
    const result={version:db.version,stores:stores.map((item,index)=>({schema:item.schema,rows:rows[index]}))};db.close();return result;
  });
  const state=await source.context.storageState();
  const origin=await offlineOrigin(),offlineBase=origin.url;
  const profiles='D:/Eric OS/backups/web-pwa-v1-20261029/webkit-profiles';await mkdir(profiles,{recursive:true});
  const context=await webkit.launchPersistentContext(await mkdtemp(profiles+'/offline-'),{headless:true,viewport:{width:390,height:844},serviceWorkers:'allow'});
  try{
    const page=await context.newPage();page.on('pageerror',error=>errors.push('webkit-offline: '+error.message));
    await page.goto(offlineBase+'/offline-shell-prime');
    await page.evaluate(items=>{for(const item of items)localStorage.setItem(item.name,item.value);},state.origins.find(entry=>entry.origin===BASE)?.localStorage||[]);
    await page.evaluate(async snapshot=>{
      const request=indexedDB.open('eric-os-v1',snapshot.version);
      request.onupgradeneeded=()=>{for(const {schema} of snapshot.stores){const store=request.result.createObjectStore(schema.name,{keyPath:schema.keyPath,autoIncrement:schema.autoIncrement});for(const index of schema.indexes)store.createIndex(index.name,index.keyPath,{unique:index.unique,multiEntry:index.multiEntry});}};
      const db=await new Promise((resolve,reject)=>{request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});
      const transaction=db.transaction(snapshot.stores.map(item=>item.schema.name),'readwrite');
      const done=new Promise((resolve,reject)=>{transaction.oncomplete=resolve;transaction.onerror=()=>reject(transaction.error);});
      for(const item of snapshot.stores)for(const row of item.rows)transaction.objectStore(item.schema.name).put(row);
      await done;db.close();await navigator.serviceWorker.register('/sw.js');await navigator.serviceWorker.ready;
      if(!navigator.serviceWorker.controller)await new Promise(resolve=>navigator.serviceWorker.addEventListener('controllerchange',resolve,{once:true}));
    },snapshot);
    // Stop the real static origin. Also prevent any synthetic Auth/API traffic
    // escaping into production when the cached app starts without connectivity.
    await origin.stop();
    await context.addInitScript(()=>{Object.defineProperty(navigator,'onLine',{configurable:true,get:()=>false});const nativeFetch=fetch;window.fetch=(input,options)=>new URL(typeof input==='string'?input:input.url,location.href).origin===location.origin?nativeFetch(input,options):Promise.reject(new TypeError('Isolated disconnected API'));});
    await page.goto(offlineBase+'/?view=recent');
    await expect(page.getByRole('heading',{name:'最近',exact:true})).toBeVisible({timeout:15000});
    assert.equal(await page.evaluate(()=>navigator.onLine),false);
  }finally{await origin.stop();await context.close();}
}

const engine=process.env.ERIC_WEB_ENGINE==='webkit'?webkit:chromium;
const browser=await engine.launch({headless:true});
const reports=[];
try {
  const desktop=await environment(browser,{width:1440,height:1000},'desktop');
  const tablet=await environment(browser,{width:1024,height:1366},'tablet');
  const phone=await environment(browser,{width:390,height:844},'phone');
  await courseNoteConfirmationFlow({phone,BASE,state,course:generalCourse,shot,reports});
  for(const env of [desktop,tablet,phone]){
    const metrics=await env.page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,content:document.querySelector('main').getBoundingClientRect().width}));
    assert.ok(metrics.scroll<=metrics.width+1,JSON.stringify({label:env.label,...metrics}));
    await shot(env.page,env.label+'-today.png');reports.push({device:env.label,title:await env.page.title(),layout:metrics});
  }
  await phone.page.locator('.cloud-task-list input').click();
  for(const env of [phone,tablet,desktop])await expect(env.page.locator('.cloud-task-list input')).toHaveCount(0,{timeout:20000});
  await desktop.page.goto(BASE+'/?view=event&id=event-synthetic');
  await expect(desktop.page.locator('.cloud-task-list input')).toBeChecked();
  await desktop.page.locator('.cloud-task-list input').click();
  for(const env of [phone,tablet])await expect(env.page.locator('.cloud-task-list input')).toHaveCount(1,{timeout:20000});
  reports.push({shared_task_completion:'complete and restore agree in three independent clients while Windows stays offline'});
  await desktop.page.goto(BASE+'/?view=lecture&id=lecture-synthetic');
  await expect(desktop.page.getByRole('heading',{name:'原始课堂笔记',exact:true})).toBeVisible();
  await expect(desktop.page.locator('.school-note .personal-source-image img')).toBeVisible();
  await shot(desktop.page,'desktop-lecture.png');
  const downloadPromise=desktop.page.waitForEvent('download');
  await desktop.page.getByRole('button',{name:'导出本次证据说明',exact:true}).click();
  assert.ok((await downloadPromise).suggestedFilename().endsWith('-evidence.json'));
  await desktop.page.goto(BASE+'/?view=system');
  await expect(desktop.page.getByRole('heading',{name:'安装到桌面或主屏幕',exact:true})).toBeVisible();
  await shot(desktop.page,'desktop-system.png');
  reports.push({canonical_lecture:'notes, source preview, contextual export and device status use the cloud adapter'});
  await phone.page.getByRole('navigation',{name:'主要导航'}).getByRole('button',{name:'更多',exact:true}).click();await phone.page.getByRole('button',{name:'Review',exact:true}).click();await expect(phone.page.getByRole('heading',{name:'课堂复盘',exact:true})).toBeVisible();
  await phone.page.getByRole('button',{name:/隔离测试课堂/}).click();await expect(phone.page.getByRole('button',{name:'标记已读',exact:true})).toBeVisible();
  await phone.page.getByRole('button',{name:'标记已读',exact:true}).click();await expect(phone.page.getByRole('button',{name:'标为未读',exact:true})).toBeVisible({timeout:20000});
  await shot(phone.page,'phone-review.png');
  await tablet.page.getByRole('button',{name:'Review',exact:true}).click();await tablet.page.getByRole('button',{name:/隔离测试课堂/}).click();await expect(tablet.page.getByRole('button',{name:'标为未读',exact:true})).toBeVisible({timeout:15000});
  await desktop.page.getByRole('button',{name:'Review',exact:true}).click();await desktop.page.getByRole('button',{name:/隔离测试课堂/}).click();await expect(desktop.page.getByRole('button',{name:'标为未读',exact:true})).toBeVisible();
  reports.push({shared_reading_progress:'three independent browser stores agree while Windows is off'});
  await phone.page.getByRole('button',{name:'新建 Capture',exact:true}).click();await expect(phone.page.getByLabel('内容',{exact:true})).toBeVisible();
  await shot(phone.page,'phone-capture.png');
  await phone.page.getByLabel('标题（可选）',{exact:true}).fill('返回保护测试');
  await phone.page.goBack();await expect(phone.page.getByRole('dialog')).toBeVisible();
  await phone.page.getByRole('button',{name:'继续当前操作',exact:true}).click();
  await expect(phone.page.getByLabel('标题（可选）',{exact:true})).toHaveValue('返回保护测试');
  reports.push({unsaved_back:'browser Back cancellation preserves the current draft and exact navigation entry'});
  await phone.setOffline(true);await phone.page.getByLabel('标题（可选）',{exact:true}).fill('离线恢复测试');await phone.page.getByLabel('内容',{exact:true}).fill('只在隔离浏览器中保存，不进入正式学习记录。');
  const pdf=Buffer.from('%PDF-1.7\nSynthetic queue test, not learner evidence\n%%EOF');
  await phone.page.locator('input[type=file]').first().setInputFiles({name:'synthetic-note.pdf',mimeType:'application/pdf',buffer:pdf});
  await phone.page.getByRole('button',{name:'保存到 Eric OS',exact:true}).click();await expect(phone.page.getByRole('heading',{name:'最近',exact:true})).toBeVisible();await expect(phone.page.getByText('待发送',{exact:true})).toBeVisible();
  await expect(phone.page.getByText('联网后会自动读取云端记录。',{exact:true})).toHaveCount(2);await expect(phone.page.getByRole('alert')).toHaveCount(0);
  await shot(phone.page,'phone-offline-receipt.png');
  const captureId=await phone.page.evaluate(async()=>{const request=indexedDB.open('eric-os-v1');const db=await new Promise((r,j)=>{request.onsuccess=()=>r(request.result);request.onerror=()=>j(request.error);});const tx=db.transaction('outbox'),get=tx.objectStore('outbox').getAll();const items=await new Promise(r=>get.onsuccess=()=>r(get.result));db.close();return items[0].id;});
  await phone.page.close();await phone.setOffline(false);phone.page=await phone.context.newPage();await phone.page.goto(BASE+'/?view=recent');
  await expect(phone.page.getByText('云端已接收',{exact:false})).toBeVisible({timeout:35000});
  await expect.poll(()=>state.reserveCalls,{timeout:20000,message:'Lost commit response must be resolved through the same capture ID'}).toBeGreaterThanOrEqual(2);
  assert.equal(state.captures.size,1);assert.ok(state.captures.has(captureId));assert.equal(state.partCalls,1);
  reports.push({offline_capture:'file and stable request survive closing; reconnect retries a lost receipt without a second upload'});
  if(process.env.ERIC_WEB_ENGINE==='webkit'){
    await webkitOfflineShell(browser,phone);reports.push({pwa_shell:'actual WebKit production shell opens after its static origin is stopped, with cached identity/data and disconnected API; online mocked flows run separately'});
  }else{
  const manifests=await phone.page.evaluate(async()=>{await navigator.serviceWorker.ready;return {manifest:await(await fetch('/manifest.webmanifest')).json(),caches:await caches.keys()};});
  assert.equal(manifests.manifest.display,'standalone');assert.ok(manifests.caches.some(key=>key.startsWith('eric-os-shell-')));
  await phone.page.reload();await phone.page.waitForLoadState('networkidle');await phone.setOffline(true);await phone.page.reload();await expect(phone.page.getByRole('heading',{name:'最近',exact:true})).toBeVisible({timeout:15000});reports.push({pwa_shell:'production service worker opens the app offline'});await phone.setOffline(false);
  }
  await tablet.page.goto(BASE+'/?view=textbooks');await expect(tablet.page.getByRole('heading',{name:'隔离测试教材 · 第 1 页',exact:true})).toBeVisible();await expect(tablet.page.locator('.personal-source-image img')).toBeVisible();
  await shot(tablet.page,'tablet-textbook.png');
  await tablet.page.getByLabel('PDF 页码',{exact:true}).fill('3');await tablet.page.getByRole('button',{name:'查看这一页',exact:true}).click();
  const pdfCanvas=tablet.page.locator('canvas[aria-label="原文件第 3 页"]');
  await expect(pdfCanvas).toBeVisible({timeout:30000});
  assert.ok(await pdfCanvas.evaluate(canvas=>{const {width,height}=canvas,bytes=canvas.getContext('2d').getImageData(0,0,width,height).data;return width>=600&&height>=840&&bytes.some((byte,index)=>index%4!==3&&byte<150);}));
  assert.ok(pdfRanges.length>0,'An uncached page must use bounded PDF ranges');
  reports.push({textbook:'page without a stored preview renders from the original PDF with bounded byte ranges and no Windows processor'});
  await shot(tablet.page,'tablet-pdf-page.png');
  await courseWorkspaceFlows({desktop,tablet,phone,BASE,state,books,course:generalCourse,otherCourse,document,dto,changed,shot,reports});
  // Logout/revocation preserves local bytes. Recovery is read-only; no new
  // device may write a previous device's unfinished upload.
  const retainedId=randomUUID();
  await phone.page.evaluate(async({captureId,retainedId})=>{
    const request=indexedDB.open('eric-os-v1'),db=await new Promise((resolve,reject)=>{request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});
    const transaction=db.transaction('outbox','readwrite'),store=transaction.objectStore('outbox'),get=store.get(captureId);
    get.onsuccess=()=>store.add({...get.result,id:retainedId,title:'此前登录留下的本机内容',deviceId:'00000000-0000-4000-8000-000000000000',state:'needs_confirmation',server:null,confirmedAt:null,error:{code:'previous_device',message:'此前登录的原件仍保存在这里，请核对接收情况。'}});
    await new Promise((resolve,reject)=>{transaction.oncomplete=resolve;transaction.onerror=()=>reject(transaction.error);});db.close();
  },{captureId,retainedId});
  const reservations=state.reserveCalls,partWrites=state.partCalls;
  const original=state.captures.get(captureId);state.captures.set(retainedId,{...original,id:retainedId,title:'此前登录留下的本机内容',state:'uploading',received_at:null});
  await phone.page.goto(BASE+'/?view=recent');await phone.page.getByText('查看本机保留内容',{exact:true}).click();
  await phone.page.getByRole('button',{name:'取回 synthetic-note.pdf',exact:true}).click();
  const recovered=phone.page.waitForEvent('download');await phone.page.getByRole('link',{name:'下载 synthetic-note.pdf',exact:true}).click();
  assert.deepEqual(await readFile(await (await recovered).path()),pdf);
  assert.equal(state.reserveCalls,reservations);assert.equal(state.partCalls,partWrites);
  reports.push({previous_device:'old login copy remains downloadable by its owner; cloud uploading state cannot hide the local issue; recovery performs no upload'});
  await shot(phone.page,'phone-retained-copy.png');
  await desktop.page.goto(BASE+'/?view=inbox');await expect(desktop.page.getByRole('heading',{name:'Inbox',exact:true})).toBeVisible();await expect(desktop.page.getByText('隔离测试输入',{exact:true})).toBeVisible();await shot(desktop.page,'desktop-inbox.png');
  await phone.page.setViewportSize({width:320,height:740});await phone.page.emulateMedia({colorScheme:'dark',reducedMotion:'reduce'});await phone.page.goto(BASE+'/?view=today');await expect(phone.page.getByRole('heading',{name:'Today',exact:true})).toBeVisible();
  await expect(phone.page.getByRole('heading',{name:/^今日课程/})).toBeVisible();
  await expect.poll(()=>phone.page.evaluate(()=>document.documentElement.dataset.deskTheme)).toBe('dark');
  await phone.page.evaluate(async()=>{await document.fonts.ready;await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});
  assert.ok(await phone.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await phone.page.keyboard.press('Tab');await shot(phone.page,'phone-320-dark-keyboard.png');
  assert.deepEqual(privateRequests,[]);assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[]);
  await writeFile(resolve(OUTPUT,'report.json'),JSON.stringify({browser_path:'Browser plugin not available; Playwright fallback',actual_cloud_auth_tested:false,actual_cellular_tested:false,results:reports,errors,unexpected,privateRequests},null,2));
  process.stdout.write(JSON.stringify({passed:true,checks:reports,output:OUTPUT})+'\n');
} catch(error) {
  const pages=[];
  for(const context of browser.contexts())for(const page of context.pages())pages.push({url:page.url(),text:await page.locator('body').innerText().catch(()=>''),
    layout:await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,theme:document.documentElement.dataset.deskTheme,overflow:[...document.querySelectorAll('body *')].map(element=>({element:element.tagName+'.'+element.className,right:element.getBoundingClientRect().right,width:element.getBoundingClientRect().width,text:element.textContent.slice(0,100)})).filter(item=>item.right>innerWidth+1).slice(0,15)})).catch(()=>null),
    authDiagnostics:await page.evaluate(()=>window.ericAuthDiagnostics).catch(()=>null),worker:await page.evaluate(()=>({controlled:!!navigator.serviceWorker.controller,online:navigator.onLine})).catch(()=>null)});
  await writeFile(resolve(OUTPUT,'failure.json'),JSON.stringify({message:error.message,errors,unexpected,failedStatic,pages},null,2));
  throw error;
} finally {await browser.close();}
