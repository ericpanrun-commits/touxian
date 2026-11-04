import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';

async function moduleAt(path){const entry=process.env.ERIC_API_TEST_ROOT?resolve(process.env.ERIC_API_TEST_ROOT,path.split('/').at(-1)):fileURLToPath(new URL(path,import.meta.url));const compiled=await build({entryPoints:[entry],bundle:true,write:false,format:'esm',platform:'neutral',target:'es2022'});return import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].contents).toString('base64'));}
const {createHandler,captureInput,commandInput}=await moduleAt('../supabase/functions/eric-app-api/handler.ts');
const {CloudStore,hash}=await moduleAt('../supabase/functions/eric-app-api/store.ts');
const {byteRange,fileResponse,verifyResource}=await moduleAt('../supabase/functions/eric-app-api/files.ts');
const {civilDay,civilInstant,deadlineState}=await moduleAt('../supabase/functions/eric-app-api/views.ts');
const BASE='https://fixture.supabase.co',ROOT=BASE+'/functions/v1/eric-app-api',ORIGIN='https://eric-fixture.example',SECRET='only-on-server';
const W='11111111-1111-4111-8111-111111111111',OWNER='22222222-2222-4222-8222-222222222222',DEVICE='33333333-3333-4333-8333-333333333333',SESSION='44444444-4444-4444-8444-444444444444';
const NOW=new Date('2026-10-29T12:00:00Z');
const claims={sub:OWNER,session_id:SESSION,role:'authenticated',exp:NOW.getTime()/1000+3600};
const TOKEN=['fixture',Buffer.from(JSON.stringify(claims)).toString('base64url'),'fixture-signature'].join('.');
const auth={Authorization:'Bearer '+TOKEN,Origin:ORIGIN};

test('Source correction is bound to the owned parent review and canonical lecture revision',async()=>{
  const lookups=[],store={async one(workspace,kind,id){lookups.push({workspace,kind,id});return id==='owned-review'?{payload:{lecture_id:'owned-lecture'}}:null;}};
  const input={id:DEVICE,method:'POST',path:'/school/reviews/owned-review/source-correction',body:{changes:[]},expected_revision:12};
  const accepted=await commandInput(store,W,DEVICE,input);
  assert.equal(accepted.resource_key,'lecture:owned-lecture');assert.equal(accepted.expected_revision,12);
  assert.deepEqual(lookups,[{workspace:W,kind:'review',id:'owned-review'}]);
  await assert.rejects(commandInput(store,W,DEVICE,{...input,path:'/school/reviews/foreign-review/source-correction'}),{code:'unsupported_action'});
});

test('Textbook actions bind canonical course/book/document revisions and never accept implicit Math targets',async()=>{
  const store={async one(workspace,kind,id){assert.equal(workspace,W);return kind==='document_section'&&id==='owned-section'?{payload:{textbook_id:'english-book'}}:null;}};
  const action={id:DEVICE,method:'POST',path:'/school/textbooks',body:{course_id:'english',title:'Grammar'},expected_revision:9};
  assert.equal((await commandInput(store,W,DEVICE,action)).resource_key,'course:english');
  const file={...action,method:'PUT',path:'/school/textbook/file',body:{textbook_id:'english-book',expected_version:2}};
  assert.equal((await commandInput(store,W,DEVICE,file)).resource_key,'textbook:english-book');
  await assert.rejects(commandInput(store,W,DEVICE,{...file,body:{expected_version:2}}),{code:'invalid_reference'});
  await assert.rejects(commandInput(store,W,DEVICE,{...file,body:{textbook_id:'english-book'}}),{code:'invalid_input'});
  const section={...action,path:'/school/sections/owned-section/index',body:{}};
  assert.equal((await commandInput(store,W,DEVICE,section)).resource_key,'textbook:english-book');
  await assert.rejects(commandInput(store,W,DEVICE,{...section,path:'/school/sections/foreign/index'}),{code:'unsupported_action'});
  assert.equal((await commandInput(store,W,DEVICE,{...section,path:'/school/documents/owned-doc/catalog'})).resource_key,'document:owned-doc');
});

test('Course-scoped school reads retain an honest missing-book state and never select the Math facade',async()=>{
  const r=rig();
  r.record('facade','/school',{textbook:{id:'math-book'},course:{id:'math'}});
  r.record('facade','/school?course_id=english',{textbook:null,course:{id:'english',name:'English'},lectures:[]});
  r.record('facade','/school/notes?course_id=english',{items:[{raw_item_id:'english-notes'}]});
  r.record('lecture_item','english-class',{id:'english-class',course_id:'english',lecture_date:'2026-10-28'});
  r.record('lecture_item','math-class',{id:'math-class',course_id:'math',lecture_date:'2026-10-28'});
  const response=await r.request('/school?course_id=english');
  if(response.status!==200)assert.fail(await response.text());
  const page=await response.json();assert.equal(page.course.id,'english');assert.equal(page.textbook,null);
  assert.deepEqual(page.items.map(item=>item.id),['english-class']);
  const missing=await r.request('/school/textbook?course_id=english');assert.equal(missing.status,200);assert.equal(await missing.json(),null);
  const notes=await (await r.request('/school/notes?course_id=english')).json();assert.equal(notes.items[0].raw_item_id,'english-notes');
});

test('Canonical textbook catalogue reads filter courses and preserve owned record revisions',async()=>{
  const r=rig();r.record('textbook','english-book',{id:'english-book',course_id:'english',title:'Grammar',sections:[]},17);
  r.record('textbook','math-book',{id:'math-book',course_id:'math',title:'Calculus'},18);
  const response=await r.request('/school/textbooks?course_id=english');assert.equal(response.status,200);
  const page=await response.json();assert.deepEqual(page.items.map(book=>book.id),['english-book']);
  assert.equal(page.items[0]._cloud.kind,'textbook');assert.equal(page.items[0]._cloud.revision,17);
  const detail=await (await r.request('/school/textbooks/english-book')).json();assert.equal(detail.id,'english-book');
  const foreign=await r.request('/school/textbooks/foreign-book');assert.equal(foreign.status,404);
});

function rig(options={}){
  const tables={eric_app_workspaces:[{id:W,owner_id:OWNER,timezone:'Asia/Shanghai'}],eric_app_devices:[{id:DEVICE,workspace_id:W,user_id:OWNER,session_id:SESSION,revoked_at:null}],eric_app_processors:[],eric_app_records:[],eric_app_resources:[],eric_app_resource_parts:[],eric_app_captures:[],eric_app_changes:[],eric_app_workers:[]};
  const objects=new Map(),calls=[],rpc={},faults={auth:null},clock={now:NOW};
  rpc.eric_app_inbox_page=body=>Response.json({items:[],total:0,offset:body.p_offset,limit:body.p_limit});
  function selected(rows,url){return rows.filter(row=>[...url.searchParams].every(([key,value])=>{
    if(['select','limit','offset','order'].includes(key))return true;
    const actual=key.startsWith('payload->>')?row.payload[key.slice(10)]:row[key];
    if(value==='is.null')return actual==null;
    if(value==='not.is.null')return actual!=null;
    if(value==='eq.false')return actual===false;
    if(value==='eq.true')return actual===true;
    if(value.startsWith('eq.'))return String(actual)===value.slice(3);
    if(value.startsWith('gt.'))return actual>Number(value.slice(3));
    if(value.startsWith('not.in.('))return !value.slice(8,-1).split(',').includes(String(actual));
    if(value.startsWith('in.('))return value.slice(4,-1).split(',').includes(String(actual));
    throw new Error('Unhandled fixture query '+key+'='+value);
  }));}
  const fetcher=async(input,init={})=>{
    const url=new URL(input),headers=new Headers(init.headers);assert.equal(url.origin,BASE);assert.equal(headers.get('apikey'),SECRET);calls.push(url.pathname+url.search);
    if(url.pathname==='/auth/v1/user') {
      if(faults.auth)return Response.json({},{status:faults.auth});
      return headers.get('Authorization')==='Bearer '+TOKEN?Response.json({id:OWNER,email:'synthetic@example.test',email_confirmed_at:'2026-01-01T00:00:00Z'}):Response.json({},{status:401});
    }
    if(url.pathname.startsWith('/rest/v1/rpc/')) {
      const name=url.pathname.split('/').at(-1),body=JSON.parse(init.body);
      assert.ok(rpc[name],'Unexpected RPC '+name);return rpc[name](body);
    }
    if(url.pathname.startsWith('/rest/v1/')){
      const name=url.pathname.split('/').at(-1);assert.ok(tables[name],name);
      const all=selected(tables[name],url),offset=Number(url.searchParams.get('offset')||0),limit=Number(url.searchParams.get('limit')||1000);
      const fields=url.searchParams.get('select');let rows=all.slice(offset,offset+limit);
      if(fields&&/^[a-z_,]+$/.test(fields))rows=rows.map(row=>Object.fromEntries(fields.split(',').map(field=>[field,row[field]])));
      return Response.json(rows,{headers:{'Content-Range':all.length?`${offset}-${Math.min(all.length-1,offset+limit-1)}/${all.length}`:`*/0`}});
    }
    const storage='/storage/v1/object/eric-app-private/';
    if(url.pathname.startsWith(storage)){
      const path=url.pathname.slice(storage.length);
      if(init.method==='POST') {if(objects.has(path))return Response.json({error:'Duplicate'},{status:409});objects.set(path,new Uint8Array(init.body));return Response.json({});}
      if(!objects.has(path))return new Response(null,{status:404});
      // Deliberately ignore Range: the API must also correctly handle a Storage origin returning 200.
      return new Response(objects.get(path),{headers:{'Content-Length':String(objects.get(path).length)}});
    }
    throw new Error('Unexpected external request '+url.pathname);
  };
  const store=new CloudStore({base:BASE,secret:SECRET,fetcher,clock:()=>clock.now});
  const handler=createHandler({base:BASE,secret:SECRET,fetcher,origins:[ORIGIN],clock:()=>clock.now,...options});
  async function request(path,method='GET',body,headers=auth){return handler(new Request(ROOT+path,{method,headers:{...headers,...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})}));}
  function record(kind,id,payload,revision=1){tables.eric_app_records.push({workspace_id:W,kind,id,payload,revision,deleted:false,updated_at:NOW.toISOString()});}
  return {tables,objects,calls,rpc,faults,clock,store,handler,request,record};
}

test('Daily Review pagination follows canonical classroom membership and original cloud revisions',async()=>{
  const r=rig();
  for(let version=1;version<=131;version++)r.record('review_item','recent-v'+version,{id:'recent-v'+version,lecture_id:'recent',version,has_content:true,status:'needs_review'},version+10);
  r.record('review_item','earlier-v1',{id:'earlier-v1',lecture_id:'earlier',version:1,has_content:true,status:'needs_review'},501);
  r.record('facade','/review-queue',{schema_version:1,members:[
    {review_id:'recent-v131',lecture_id:'recent',version:131,has_content:true,newer_than_adopted:true},
    {review_id:'earlier-v1',lecture_id:'earlier',version:1,has_content:true,newer_than_adopted:false},
  ]});
  r.record('review_progress','recent-v130',{completed:true},9);
  // A similarly shaped foreign record never satisfies an owned membership.
  r.tables.eric_app_records.push({workspace_id:OWNER,kind:'review_item',id:'earlier-v1',payload:{id:'foreign'},revision:999,deleted:false});
  const before=structuredClone(r.tables);
  const page=await(await r.request('/reviews?limit=1&offset=1')).json();
  assert.equal(page.total,2);assert.equal(page.unread_total,2);assert.equal(page.items[0].id,'earlier-v1');assert.equal(page.items[0]._cloud.revision,501);
  const first=await(await r.request('/reviews?limit=1')).json();assert.equal(first.items[0].id,'recent-v131');assert.equal(first.items[0].newer_than_adopted,true);
  assert.equal(first.items[0]._cloud.revision,141);assert.deepEqual(r.tables,before);
  assert.ok(r.calls.some(path=>path.includes('id=in.(earlier-v1)')));
  assert.ok(!r.calls.some(path=>path.includes('kind=eq.review_item')&&!path.includes('id=in.(')));
});

test('Reading a current Review does not expose old unread versions and filtering precedes pagination',async()=>{
  const r=rig(),members=[];
  for(let i=0;i<121;i++){
    const id='class-'+i+'-current';members.push({review_id:id,lecture_id:'class-'+i,has_content:true,version:2});
    r.record('review_item',id,{id,lecture_id:'class-'+i,version:2,has_content:true,status:'needs_review'});
    r.record('review_item','class-'+i+'-old',{id:'class-'+i+'-old',lecture_id:'class-'+i,version:1,has_content:true,status:'needs_review'});
    if(i<120)r.record('review_progress',id,{completed:true},5);
  }
  members.push({review_id:'unfinished',lecture_id:'unfinished-class',has_content:false,version:1});
  r.record('review_item','unfinished',{id:'unfinished',has_content:false,status:'failed'});
  r.record('facade','/review-queue',{schema_version:1,members});
  const unread=await(await r.request('/reviews?limit=8&unread_only=true')).json();
  assert.equal(unread.total,1);assert.equal(unread.unread_total,1);assert.equal(unread.incomplete_total,1);assert.deepEqual(unread.items.map(item=>item.id),['class-120-current']);
  r.record('facade','/timetable',{sessions:[]});r.record('facade','/planning/facts',{items:[]});
  let dashboard=await(await r.request('/dashboard')).json();assert.equal(dashboard.review_count,1);assert.equal(dashboard.review_incomplete_count,1);assert.equal(dashboard.next_action.id,'class-120-current');
  r.record('review_progress','class-120-current',{completed:true},6);
  dashboard=await(await r.request('/dashboard')).json();assert.equal(dashboard.review_count,0);assert.deepEqual(dashboard.reviews,[]);
  assert.equal(dashboard.next_action.kind,'capture');
  const page=await(await r.request('/reviews?limit=100')).json();assert.equal(page.total,122);assert.ok(page.items.every(item=>!item.id.endsWith('-old')));
});

test('Queue reads retain legacy processor data and report missing owned records without fabricating revisions',async()=>{
  const r=rig();r.record('review_item','legacy',{id:'legacy',has_content:true,status:'needs_review'},23);
  let page=await(await r.request('/reviews')).json();assert.equal(page.items[0]._cloud.revision,23);
  r.record('facade','/review-queue',{schema_version:1,members:[{review_id:'foreign',lecture_id:'class',has_content:true}]});
  r.tables.eric_app_records.push({workspace_id:OWNER,kind:'review_item',id:'foreign',payload:{id:'foreign'},revision:99,deleted:false});
  const missing=await r.request('/reviews');assert.equal(missing.status,503);assert.equal((await missing.json()).detail.code,'review_queue_syncing');
  r.tables.eric_app_records.find(row=>row.kind==='facade').payload.members=[];
  page=await(await r.request('/reviews')).json();assert.equal(page.total,0);assert.deepEqual(page.items,[]);
});

test('Today counts all current unread classes while returning only its eight bounded summaries',async()=>{
  const r=rig(),members=[];
  for(let i=0;i<109;i++){
    const id='reading-'+i;members.push({review_id:id,lecture_id:'class-'+i,has_content:true,version:1});
    r.record('review_item',id,{id,lecture_id:'class-'+i,version:1,has_content:true,status:'needs_review'});
  }
  r.record('facade','/review-queue',{schema_version:1,members});
  r.record('facade','/timetable',{sessions:[]});r.record('facade','/planning/facts',{items:[]});
  const dashboard=await(await r.request('/dashboard')).json();
  assert.equal(dashboard.review_count,109);assert.equal(dashboard.reviews.length,8);
  const loaded=r.calls.filter(path=>path.includes('kind=eq.review_item'));
  assert.equal(loaded.length,1);assert.equal(new URL(loaded[0],BASE).searchParams.get('id').split(',').length,8);
});

test('Today delegates confirmation counts to the same submitted, unarchived Inbox owner',async()=>{
  const r=rig(),calls=[];let unarchived=1;
  r.record('facade','/timetable',{sessions:[]});r.record('facade','/planning/facts',{items:[]});
  r.record('inbox_item','automatic',{id:'automatic',needs_manual:true,managed:false});
  r.record('inbox_item','already-archived',{id:'already-archived',needs_manual:true,managed:true});
  r.record('inbox_progress','already-archived',{archived:true},8);
  r.rpc.eric_app_inbox_page=body=>{calls.push(body);return Response.json({items:[],total:unarchived,offset:body.p_offset,limit:body.p_limit});};
  const before=structuredClone(r.tables);
  let dashboard=await(await r.request('/dashboard')).json();
  assert.equal(dashboard.needs_confirmation,1);assert.equal(dashboard.next_action.kind,'inbox');
  assert.deepEqual(calls[0],{p_workspace:W,p_status:'needs_review',p_scope:'submitted',p_offset:0,p_limit:1});
  unarchived=0;
  dashboard=await(await r.request('/dashboard')).json();assert.equal(dashboard.needs_confirmation,0);assert.equal(dashboard.next_action.kind,'capture');
  assert.deepEqual(r.tables,before);
  assert.ok(!r.calls.some(path=>path.includes('kind=eq.inbox_item')));
});

test('CORS preflight is allowed only for the configured product origin and reveals no data',async()=>{
  const r=rig();const ok=await r.request('/captures','OPTIONS',undefined,{Origin:ORIGIN});assert.equal(ok.status,204);assert.equal(ok.headers.get('Access-Control-Allow-Origin'),ORIGIN);
  const denied=await r.request('/captures','OPTIONS',undefined,{Origin:'https://untrusted.example'});assert.equal(denied.status,403);assert.equal(denied.headers.get('Access-Control-Allow-Origin'),null);assert.equal(r.calls.length,0);
});
test('The full processor has an independent credential; notes-only credentials and revoked processors cannot read workbench data',async()=>{
  const r=rig(),key='ework_'+'a'.repeat(43);
  await assert.rejects(()=>r.store.worker(new Request(ROOT,{headers:{'X-Eric-Sync-Key':'esync_'+'a'.repeat(43)}})),/unauthorized/);
  await assert.rejects(()=>r.store.worker(new Request(ROOT,{headers:{'X-Eric-Worker-Key':key}})),/unauthorized/);
  r.tables.eric_app_processors.push({id:DEVICE,workspace_id:W,key_hash:await hash(key),revoked_at:null});
  assert.equal((await r.store.worker(new Request(ROOT,{headers:{'X-Eric-Worker-Key':key}}))).workspace.id,W);
  r.tables.eric_app_processors[0].revoked_at=NOW.toISOString();
  await assert.rejects(()=>r.store.worker(new Request(ROOT,{headers:{'X-Eric-Worker-Key':key}})),/unauthorized/);
});
test('A decoded JWT, publishable key or worker-shaped request cannot bypass server verification',async()=>{
  const r=rig();for(const headers of [{apikey:'public-key'}, {...auth,Authorization:'Bearer '+TOKEN.replace('fixture-signature','forged-signature')}])assert.equal((await r.request('/courses','GET',undefined,headers)).status,401);
  assert.equal((await r.request('/worker/publish','POST',{epoch:SESSION,records:[]},auth)).status,401);
  assert.equal(r.calls.some(x=>x.includes('eric_app_records')),false);
});

test('Restart catalogue exposes only this processor workspace source hashes, never owner progress or record payloads',async()=>{
  const r=rig(),key='ework_'+'a'.repeat(43);
  r.tables.eric_app_processors.push({id:DEVICE,workspace_id:W,key_hash:await hash(key),revoked_at:null});
  r.record('course','same-source',{private_text:'only inside private records'});Object.assign(r.tables.eric_app_records[0],{source_hash:'a'.repeat(64),source_epoch:SESSION});
  r.record('review_progress','progress',{completed:true});
  r.tables.eric_app_records.push({workspace_id:OWNER,kind:'course',id:'other-workspace',source_hash:'b'.repeat(64),source_epoch:SESSION,deleted:false});
  const response=await r.request('/worker/records', 'GET',undefined,{'X-Eric-Worker-Key':key});assert.equal(response.status,200);
  const result=await response.json();assert.equal(result.items.length,1);assert.equal(result.items[0].id,'same-source');assert.equal(result.items[0].source_hash,'a'.repeat(64));
  assert.ok(!('payload' in result.items[0]));assert.deepEqual(Object.keys(result.items[0]).sort(),['id','kind','source_hash']);
  assert.ok(r.calls.some(path=>path.includes('select=kind%2Cid%2Csource_hash')||path.includes('select=kind,id,source_hash')));
  assert.equal((await r.request('/worker/records')).status,401);
});
test('Official source health is read-only and bound to an active processor workspace',async()=>{
  const read=[],r=rig({officialHealth:async workspace=>{read.push(workspace);return {owner:'cloud',sources:[]};}});
  const key='ework_'+'a'.repeat(43),headers={'X-Eric-Worker-Key':key};
  assert.equal((await r.request('/worker/official-health')).status,401);
  assert.equal((await r.request('/worker/official-health','GET',undefined,headers)).status,401);
  assert.deepEqual(read,[]);
  r.tables.eric_app_processors.push({id:DEVICE,workspace_id:W,key_hash:await hash(key),revoked_at:null});
  const reply=await r.request('/worker/official-health','GET',undefined,headers);
  assert.equal(reply.status,200);assert.deepEqual(read,[W]);
  assert.equal(reply.headers.get('Cache-Control'),'private, no-store');
  assert.equal((await r.request('/worker/official-health','POST',{},headers)).status,404);
  r.tables.eric_app_processors[0].revoked_at=NOW.toISOString();
  assert.equal((await r.request('/worker/official-health','GET',undefined,headers)).status,401);
  assert.deepEqual(read,[W]);
});

test('An owner account and an active independently revocable device are both required',async()=>{
  const r=rig();r.tables.eric_app_workspaces=[];assert.equal((await r.request('/courses')).status,403);
  r.tables.eric_app_workspaces=[{id:W,owner_id:OWNER}];r.tables.eric_app_devices[0].revoked_at=NOW.toISOString();assert.equal((await r.request('/courses')).status,401);
});
test('An authentication outage reports a retryable connection problem without claiming the session is expired',async()=>{
  const r=rig();r.faults.auth=503;const res=await r.request('/courses');assert.equal(res.status,503);assert.equal((await res.json()).detail.code,'authentication_unavailable');
});
test('Private file URLs are short lived, workspace bound and cease working after per-device revocation',async()=>{
  const r=rig(),id='attachment:synthetic:original',body=new TextEncoder().encode('synthetic original'),sha=await hash(body);
  r.tables.eric_app_resources.push({workspace_id:W,id,complete:true,bytes:body.length,sha256:sha,filename:'原件.txt',mime:'text/plain',part_count:1});
  r.tables.eric_app_resource_parts.push({workspace_id:W,resource_id:id,part_index:0,sha256:sha,bytes:body.length,object_path:W+'/'+sha});r.objects.set(W+'/'+sha,body);
  const res=await r.request('/files/'+id+'/ticket','POST',{});assert.equal(res.status,200);const {url}=await res.json();assert.ok(!url.includes(SECRET));
  const opened=await r.handler(new Request(url));assert.equal(await opened.text(),'synthetic original');
  const changed=new URL(url);changed.pathname=changed.pathname.replace(encodeURIComponent(id),encodeURIComponent('attachment:other:original'));
  const tampered=await r.handler(new Request(changed));assert.equal(tampered.status,401);
  r.clock.now=new Date(NOW.getTime()+601_000);assert.equal((await r.handler(new Request(url))).status,401);
  r.clock.now=NOW;r.tables.eric_app_devices[0].revoked_at=NOW.toISOString();assert.equal((await r.handler(new Request(url))).status,401);
});
test('A conflicted task retains both intentions and is surfaced as actionable across devices',async()=>{
  const r=rig();r.record('event','event-task',{id:'event-task',status:'needs_action',tasks:[{id:'task-original',title:'Changed title',status:'todo'}],deadlines:[]},4);
  r.record('task','task-original',{id:'task-original',event_id:'event-task',title:'Changed title',status:'todo'},5);
  r.record('task_progress','task-original',{event_id:'event-task',completed:true,application_state:'needs_confirmation',message:'请核对变化的任务。'},6);
  r.rpc.eric_app_pending_changes=()=>Response.json([]);
  const event=await(await r.request('/events/event-task')).json();assert.equal(event.tasks[0].status,'todo');assert.equal(event.tasks[0].progress.completed,true);assert.equal(event.tasks[0]._cloud.revision,5);
  const activity=await(await r.request('/activity')).json();assert.equal(activity.items[0].resource_key,'event:event-task');assert.equal(activity.items[0].state,'needs_confirmation');
});
test('Attachment page links resolve only through the same workspace original document',async()=>{
  const r=rig(),id='page:doc-original:2:image';r.record('document','doc-original',{id:'doc-original',attachment_id:'original-pdf',page_count:2});
  r.tables.eric_app_resources.push({workspace_id:W,id,complete:true});
  const res=await r.request('/files/attachment-page:original-pdf:2:image/ticket','POST',{});assert.equal(res.status,200);
  assert.ok((await res.json()).url.includes(encodeURIComponent(id)));
  assert.equal((await r.request('/files/attachment-page:unrelated-pdf:2:image/ticket','POST',{})).status,404);
});
test('PDF byte ranges span immutable parts without fetching or buffering the whole book',async()=>{
  const r=rig(),id='attachment:book:original',bodies=['0123456789','abcdefghij','KLMNOPQRST'].map(x=>new TextEncoder().encode(x));
  r.tables.eric_app_resources.push({workspace_id:W,id,complete:true,bytes:30,sha256:await hash(new Uint8Array(bodies.flatMap(b=>[...b]))),filename:'book.pdf',mime:'application/pdf',part_count:3});
  for(let i=0;i<bodies.length;i++){const sha=await hash(bodies[i]);r.tables.eric_app_resource_parts.push({workspace_id:W,resource_id:id,part_index:i,sha256:sha,bytes:10,object_path:W+'/'+sha});r.objects.set(W+'/'+sha,bodies[i]);}
  const res=await fileResponse(r.store,new Request(ROOT,{headers:{Range:'bytes=8-22'}}),W,id);assert.equal(res.status,206);assert.equal(res.headers.get('Content-Range'),'bytes 8-22/30');assert.equal(await res.text(),'89abcdefghijKLM');
  r.calls.length=0;const small=await fileResponse(r.store,new Request(ROOT,{headers:{Range:'bytes=11-12'}}),W,id);assert.equal(await small.text(),'bc');assert.equal(r.calls.filter(x=>x.startsWith('/storage/')).length,1);
  assert.deepEqual(byteRange('bytes=-4',30),[26,29]);assert.throws(()=>byteRange('bytes=30-31',30));assert.throws(()=>byteRange('bytes=0-1,4-5',30));
});
async function readingFixture(){
  const r=rig(),source=new TextEncoder().encode('%PDF-1.7\nsynthetic original bytes\n%%EOF'),tail=new TextEncoder().encode('\nsynthetic incremental PDF index\n%%EOF\n');
  const attachment='book',document='doc-book',original='attachment:'+attachment+':original',sourceHash=await hash(source),tailHash=await hash(tail);
  const id='pdf-view:'+document+':'+tailHash,joined=new Uint8Array([...source,...tail]);
  r.tables.eric_app_resources.push({workspace_id:W,id:original,complete:true,bytes:source.length,sha256:sourceHash,filename:'book.pdf',mime:'application/pdf',part_count:1});
  r.tables.eric_app_resource_parts.push({workspace_id:W,resource_id:original,part_index:0,sha256:sourceHash,bytes:source.length,object_path:W+'/'+sourceHash});r.objects.set(W+'/'+sourceHash,source);
  r.record('document',document,{id:document,attachment_id:attachment,sha256:sourceHash,page_count:274,reading_resource_id:id});
  r.record('pdf_reading',id.slice(9),{id:id.slice(9),document_id:document,version:'pdf-reading-tree-1',source_resource_id:original,source_sha256:sourceHash,source_bytes:source.length,page_count:274,
    tail_sha256:tailHash,tail_bytes:tail.length,tail_base64:Buffer.from(tail).toString('base64'),bytes:joined.length,sha256:await hash(joined)});
  return {r,id,source,tail,joined};
}
test('Read-only PDF indexes support original, crossing and suffix-only ranges without a second physical book',async()=>{
  const {r,id,source,joined}=await readingFixture();
  for(const [begin,end] of [[0,7],[source.length-5,source.length+7],[source.length,joined.length-1]]){
    r.calls.length=0;const response=await fileResponse(r.store,new Request(ROOT,{headers:{Range:`bytes=${begin}-${end}`}}),W,id);
    assert.equal(response.status,206);assert.equal(response.headers.get('Content-Range'),`bytes ${begin}-${end}/${joined.length}`);
    assert.deepEqual(new Uint8Array(await response.arrayBuffer()),joined.slice(begin,end+1));
    if(begin===source.length)assert.equal(r.calls.filter(path=>path.startsWith('/storage/')).length,0);
  }
  const response=await fileResponse(r.store,new Request(ROOT),W,id);assert.deepEqual(new Uint8Array(await response.arrayBuffer()),joined);
  assert.equal(r.tables.eric_app_resources.length,1);assert.equal(r.objects.size,1);
  assert.equal((await r.request('/files/'+id+'/finish','POST',{})).status,404);
});
test('PDF reading links remain device-revocable and never serialize the internal suffix or credentials',async()=>{
  const {r,id}=await readingFixture();
  const response=await r.request('/files/'+id+'/ticket','POST',{});assert.equal(response.status,200);const {url}=await response.json();
  assert.equal((await r.handler(new Request(url,{method:'HEAD'}))).status,200);
  const key='ework_'+'a'.repeat(43);r.tables.eric_app_processors.push({id:DEVICE,workspace_id:W,key_hash:await hash(key),revoked_at:null});
  const manifest=await(await r.request('/worker/files/'+id+'/manifest','GET',undefined,{'X-Eric-Worker-Key':key})).json();
  assert.equal(manifest.id,id);assert.ok(!('tail' in manifest)&&!('tail_base64' in manifest));assert.ok(!JSON.stringify(manifest).includes(SECRET));
  r.tables.eric_app_devices[0].revoked_at=NOW.toISOString();assert.equal((await r.handler(new Request(url))).status,401);
});
test('PDF reading views reject corrupted, stale, incomplete and other-workspace dependencies',async()=>{
  for(const mutate of [
    r=>{r.tables.eric_app_records.find(row=>row.kind==='pdf_reading').payload.tail_base64=Buffer.from('changed suffix').toString('base64');},
    r=>{r.tables.eric_app_records.find(row=>row.kind==='pdf_reading').payload.tail_bytes=1_000_001;},
    r=>{r.tables.eric_app_records.find(row=>row.kind==='pdf_reading').payload.source_bytes++;},
    r=>{r.tables.eric_app_records.find(row=>row.kind==='document').payload.sha256='a'.repeat(64);},
    r=>{r.tables.eric_app_records.find(row=>row.kind==='document').payload.reading_resource_id='pdf-view:stale';},
    r=>{r.tables.eric_app_records.find(row=>row.kind==='document').workspace_id=OWNER;},
    r=>{r.tables.eric_app_records.find(row=>row.kind==='pdf_reading').deleted=true;},
    r=>{r.tables.eric_app_resources[0].complete=false;},
  ]){
    const {r,id}=await readingFixture();mutate(r);assert.ok([404,409].includes((await r.request('/files/'+id+'/ticket','POST',{})).status));
    assert.equal(r.calls.filter(path=>path.startsWith('/storage/')).length,0);
  }
});
test('File confirmation rejects missing bytes or a whole-file hash mismatch',async()=>{
  const r=rig(),id='capture:'+SESSION+':'+'a'.repeat(64),data=new TextEncoder().encode('a complete synthetic file'),sha=await hash(data);
  const resource={id,part_count:1,bytes:data.length,sha256:'b'.repeat(64)};
  r.tables.eric_app_resource_parts.push({workspace_id:W,resource_id:id,part_index:0,sha256:sha,bytes:data.length,object_path:W+'/'+sha});
  await assert.rejects(()=>verifyResource(r.store,W,resource),/file_incomplete/);r.objects.set(W+'/'+sha,data);
  await assert.rejects(()=>verifyResource(r.store,W,resource),/file_changed/);resource.sha256=sha;await verifyResource(r.store,W,resource);
});
test('Original device binding prevents a second device from writing another device unfinished upload',async()=>{
  const r=rig(),id='capture:'+SESSION+':'+'a'.repeat(64);
  r.tables.eric_app_resources.push({workspace_id:W,id,complete:false});
  r.tables.eric_app_captures.push({id:SESSION,workspace_id:W,source:'pwa',device_id:OWNER,files:[{resource_id:id}]});
  const denied=await r.request('/files/'+id+'/finish','POST',{});assert.equal(denied.status,403);assert.equal(r.calls.some(x=>x.includes('/rpc/')),false);
});
test('A capture retains immutable metadata and rejects empty, executable, duplicate or unrelated files',()=>{
  const value={id:SESSION,title:'Synthetic note',purpose:'lecture_note',text_content:'a note',files:[],course_candidates:[],shared_at:NOW.toISOString()};
  assert.equal(captureInput(value,DEVICE,NOW).device_id,DEVICE);
  assert.throws(()=>captureInput({...value,text_content:''},DEVICE,NOW));assert.throws(()=>captureInput(null,DEVICE,NOW));
  const f={resource_id:'capture:'+SESSION+':'+'a'.repeat(64),sha256:'a'.repeat(64),bytes:100,part_count:1,filename:'note.html',mime:'text/html'};
  assert.throws(()=>captureInput({...value,files:[f]},DEVICE,NOW));f.filename='note.pdf';f.mime='application/pdf';assert.throws(()=>captureInput({...value,files:[f,f]},DEVICE,NOW));
  assert.throws(()=>captureInput({...value,files:[{...f,resource_id:'attachment:unrelated:original'}]},DEVICE,NOW));
});

test('An owner selection is explicit; ordinary device hints cannot promote themselves to confirmed courses',()=>{
  const value={id:SESSION,title:'Course note',purpose:'lecture_note',text_content:'a note',files:[],
    course_candidates:[{course_id:'english',label:'English',basis:'owner_selected'}],shared_at:NOW.toISOString()};
  assert.equal(captureInput(value,DEVICE,NOW).course_candidates[0].basis,'device_suggestion');
  const selected=captureInput({...value,selected_course_id:'english'},DEVICE,NOW);
  assert.deepEqual(selected.course_candidates,[{course_id:'english',label:'English',basis:'owner_selected'}]);
});

test('A selected Capture course must belong to the authenticated workspace and renaming cannot change its retry hash',async()=>{
  const r=rig(),saved=[];r.record('course','english',{id:'english',name:'Original course name'});
  r.rpc.eric_app_reserve_capture=body=>{saved.push(body.p_capture);return Response.json([{...body.p_capture,state:'uploading',received_at:null}]);};
  const value={id:SESSION,title:'Selected course note',purpose:'lecture_note',text_content:'class date: 2026-10-28',files:[],
    selected_course_id:'english',course_candidates:[{course_id:'english',label:'Original course name'}],shared_at:NOW.toISOString()};
  assert.equal((await r.request('/captures','POST',value)).status,201);
  r.tables.eric_app_records[0].payload.name='Renamed course';
  assert.equal((await r.request('/captures','POST',value)).status,201);
  assert.equal(saved[0].request_hash,saved[1].request_hash);
  const denied=await r.request('/captures','POST',{...value,selected_course_id:'foreign-course'});
  assert.equal(denied.status,400);assert.equal((await denied.json()).detail.code,'invalid_course');assert.equal(saved.length,2);
});
test('Source text IDs remain stable, pagination exposes totals and clients never receive privileged secrets',async()=>{
  const r=rig();r.record('course','course-math-ii',{id:'course-math-ii',name:'Synthetic course'},7);
  const response=await r.request('/courses');assert.equal(response.status,200);const body=await response.json();assert.equal(body.total,1);assert.equal(body.items[0]._cloud.revision,7);assert.equal(body.items[0].id,'course-math-ii');assert.ok(!JSON.stringify(body).includes(SECRET));
});
test('Large record sets page past PostgREST defaults and never silently return a truncated collection',async()=>{
  const r=rig();for(let i=0;i<1201;i++)r.record('synthetic','item'+i,{i});const rows=await r.store.rows(W,'synthetic');assert.equal(rows.length,1201);assert.equal(r.calls.length,3);
});

test('PDF document metadata comes only from the authenticated workspace',async()=>{
  const r=rig();r.record('document','doc-synthetic',{id:'doc-synthetic',attachment_id:'attachment-synthetic',page_count:306});
  const response=await r.request('/documents/doc-synthetic');assert.equal(response.status,200);assert.equal((await response.json()).attachment_id,'attachment-synthetic');
  assert.equal((await r.request('/documents/not-published')).status,404);
});
test('Today derives the Shanghai civil day from the clock while Windows stays offline across midnight',()=>{
  const deadline={confirmed_by_user:true,uncertain:false,precision:'date',value:'2026-10-29',timezone:'Asia/Shanghai'};
  assert.equal(civilDay(new Date('2026-10-29T15:59:59Z')),'2026-10-29');assert.equal(civilDay(new Date('2026-10-29T16:00:00Z')),'2026-10-30');
  assert.equal(deadlineState(deadline,new Date('2026-10-29T15:59:59Z')).state,'today');assert.equal(deadlineState(deadline,new Date('2026-10-29T16:00:00Z')).state,'overdue');
  assert.equal(deadlineState({...deadline,confirmed_by_user:false},NOW).state,'needs_confirmation');
});

test('Overseas civil instants retain their declared zone rather than the device or server zone',()=>{
  const value=deadlineState({confirmed_by_user:true,uncertain:false,precision:'minute',value:'2026-10-29T23:30',timezone:'Europe/London'},new Date('2026-10-29T16:30:00Z'));
  assert.equal(value.end_at,'2026-10-29T23:30:00.000Z');assert.equal(value.end_date,'2026-10-30');assert.equal(value.state,'today');assert.equal(value.overdue,false);
  assert.equal(civilInstant('2026-03-08T02:30','America/New_York'),null);
  assert.equal(civilInstant('2026-11-01T01:30','America/New_York'),null);
  assert.equal(new Date(civilInstant('2026-11-01T01:30','America/New_York',0)).toISOString(),'2026-11-01T05:30:00.000Z');
  assert.equal(new Date(civilInstant('2026-11-01T01:30','America/New_York',1)).toISOString(),'2026-11-01T06:30:00.000Z');
});
