import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import {taskProgressView} from '../src/cloud/taskProgress.js';
import {recentDeadlines,taskDateText} from '../src/personal/planningHelpers.js';
import {confirmationSummary,editReviewDate} from '../src/personal/reviewHelpers.js';

const compiled=await build({entryPoints:[fileURLToPath(new URL('../supabase/functions/eric-app-api/views.ts',import.meta.url))],bundle:true,write:false,format:'esm',platform:'neutral'});
const {Views}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].contents).toString('base64'));
const at=new Date('2026-11-10T04:00:00Z');
function fixture(application='applied',completed=true,status='todo') {
  const event={id:'e',title:'Synthetic event',status:'needs_action',version:3,tasks:[{id:'t',title:'Stale embedded task',status:'done',event_id:'e'}],deadlines:[]};
  const row=(kind,id,payload,revision=30)=>({kind,id,payload,revision});
  const rows={event:[row('event','e',event)],task:[row('task','t',{id:'t',event_id:'e',title:'Current task',status,deadline_id:'d'})],
    task_progress:[row('task_progress','t',{event_id:'e',completed,application_state:application,source_revision:10},20)],reminder:[]};
  const facts={items:[{id:'d',event_id:'e',event_title:event.title,event_status:event.status,label:'Submit',precision:'date',value:'2026-11-10',confirmed_by_user:true,tasks:[{id:'t',status:'done'}]}]};
  const store={clock:()=>at,rows:async(w,kind)=>rows[kind]||[],one:async(w,kind,id)=>kind==='facade'?{payload:facts}:(rows[kind]||[]).find(r=>r.id===id),
    page:async(w,kind,offset,limit)=>({items:rows[kind].slice(offset,offset+limit),total:rows[kind].length,offset,limit})};
  return {views:new Views(store,'synthetic-workspace'),rows};
}
test('P1: acknowledged completion never overrides a later Windows reopen in detail, list or Planning',async()=>{
  const {views}=fixture();
  const detail=await views.read('/events/e',new URL('https://fixture/events/e'));
  const list=await views.read('/events',new URL('https://fixture/events'));
  const planning=await views.planning(new URL('https://fixture/planning/today'),'/planning/today');
  assert.equal(detail.tasks[0].status,'todo');assert.equal(detail.tasks[0].title,'Current task');
  assert.equal(list.items[0].completed_task_count,0);
  assert.equal(planning.items[0].tasks[0].status,'todo');assert.equal(planning.items[0].remaining_task_count,1);
});
test('P1: pending is a visible intention, conflicts retain the current formal status',async()=>{
  for(const state of ['pending','needs_confirmation']) {
    const {views}=fixture(state);const tasks=(await views.read('/events/e',new URL('https://fixture/events/e'))).tasks;
    assert.equal(tasks[0].status,state==='pending'?'done':'todo');assert.equal(tasks[0].canonical_status,'todo');
    assert.equal((await views.read('/events',new URL('https://fixture/events'))).items[0].completed_task_count,0);
  }
});
test('P1: an older applied reopen also cannot hide a later Windows completion',async()=>{
  const {views}=fixture('applied',false,'done');
  assert.equal((await views.read('/events/e',new URL('https://fixture/events/e'))).tasks[0].status,'done');
  assert.equal((await views.read('/events',new URL('https://fixture/events'))).items[0].completed_task_count,1);
  const state=taskProgressView({status:'done',progress:{completed:true,application_state:'pending',_cloud:{revision:8}}});
  assert.equal(state.pending,true);assert.equal(state.sending,false,'a received intention can still be reversed while Windows is offline');
});
test('P1: a task belonging to another event cannot be composed into this event',async()=>{
  const {views,rows}=fixture();rows.task[0].payload.event_id='foreign';
  assert.equal((await views.read('/events/e',new URL('https://fixture/events/e'))).tasks[0].title,'Stale embedded task');
});
test('P1: device save, received, applied and failure messages do not claim premature cross-device completion',()=>{
  const task={id:'t',status:'todo',progress:{application_state:'pending',completed:true,_cloud:{revision:5}}};
  const command={resourceKey:'task_progress:t',createdAt:'2026-11-10',state:'local_pending',body:{expected_revision:0,value:{completed:true}}};
  assert.match(taskProgressView(task,[command]).message,/此设备/);
  assert.match(taskProgressView(task).message,/云端已接收，等待 Windows 应用/);
  const applied={...task,progress:{...task.progress,application_state:'applied'}};
  assert.equal(taskProgressView(applied).checked,false);assert.match(taskProgressView(applied).message,/已应用/);
  assert.match(taskProgressView(task,[{...command,error:{message:'offline'}}]).message,/同步失败/);
  const failed={...command,state:'needs_confirmation',error:{message:'版本冲突'}};
  const conflict=taskProgressView({...task,progress:{_cloud:{revision:0}}},[failed]);
  assert.equal(conflict.conflict,true);assert.equal(conflict.checked,false);assert.equal(conflict.desired,true);
  const received={...command,state:'completed',server:{completed:true,application_state:'pending',_cloud:{revision:8}}};
  assert.match(taskProgressView({...task,progress:null},[received]).message,/等待 Windows 应用/);
});

test('P1/P2: Dashboard and task reads share the same current state and expose unsettled intentions',async()=>{
  const {views,rows}=fixture('pending');
  rows.deadline=[{kind:'deadline',id:'d',payload:{id:'d',event_id:'e',label:'Submit',value:'2026-11-10',precision:'date',confirmed_by_user:true},revision:1}];
  const original=views.facade.bind(views);
  views.facade=async path=>path==='/timetable'?{sessions:[]}:original(path);
  views.reviewList=async()=>({items:[],unread_total:0,incomplete_total:0});
  views.store.rpc=async()=>({total:0});views.store.db=async()=>[];
  const page=await views.tasks(new URL('https://fixture/planning/tasks'));
  const dashboard=await views.dashboard(new URL('https://fixture/dashboard'));
  assert.equal(page.total,1);assert.equal(dashboard.task_count,1);
  assert.equal(page.items[0].canonical_status,'todo');assert.equal(dashboard.tasks[0].progress.application_state,'pending');
  assert.equal(dashboard.deadlines[0].remaining_task_count,1);
  assert.equal(dashboard.deadlines[0].tasks[0].progress.application_state,'pending');
  assert.equal((await views.tasks(new URL('https://fixture/planning/tasks?status=done'))).total,0);
});

test('P2: task projection paginates after sorting all active tasks, with no date inference',async()=>{
  const {views,rows}=fixture();rows.task_progress=[];rows.deadline=[];rows.task=[];
  for(let i=0;i<57;i++)rows.task.push({kind:'task',id:`t${String(i).padStart(2,'0')}`,revision:i+1,payload:{id:`t${String(i).padStart(2,'0')}`,event_id:'e',title:'Undated',status:'todo',deadline_id:null}});
  rows.deadline.push({kind:'deadline',id:'d',revision:1,payload:{id:'d',event_id:'e',label:'Overdue',value:'2026-11-09',precision:'date',confirmed_by_user:true}});
  rows.task[56].payload.deadline_id='d';
  const first=await views.tasks(new URL('https://fixture/planning/tasks?limit=20'));
  const second=await views.tasks(new URL('https://fixture/planning/tasks?limit=20&offset=20'));
  assert.equal(first.total,57);assert.equal(second.total,57);assert.equal(first.items[0].id,'t56');
  assert.equal(first.items[1].deadline,null);assert.equal(first.items[1].date_state,'undated');
  assert.equal(new Set([...first.items,...second.items].map(x=>x.id)).size,40);
  await assert.rejects(views.tasks(new URL('https://fixture/planning/tasks?status=invalid')),{code:'invalid_status'});
});

test('P2: homepage partitions IDs, while date-only and unconfirmed tasks keep their precision',()=>{
  assert.deepEqual(recentDeadlines([{id:'today'},{id:'future'},{id:'future'}],[{id:'today'}]),[{id:'future'}]);
  assert.equal(taskDateText({deadline:null}),'未设截止日期');
  assert.match(taskDateText({deadline:{label:'Submit',value:'2026-11-10',precision:'date',confirmed_by_user:true,state:'today'}}),/2026-11-10 · 仅日期/);
  assert.match(taskDateText({deadline:{precision:'unknown',state:'needs_confirmation'}}),/日期待确认/);
});

test('P3: removing a selected date explains affected actions without mutating evidence or task choices',()=>{
  const proof={quote:'Synthetic original',start_offset:0,end_offset:18};
  const event={deadlines:[{id:'d1',selected:true},{id:'d2',selected:true}],actions:[{id:'a1',title:'Submit',deadline_id:'d1',selected:true,evidence:[proof]},{id:'a2',title:'Optional',deadline_id:'d2',selected:false}]};
  const {event:changed,affected}=editReviewDate(event,'d1',{selected:false});
  assert.deepEqual(affected.map(x=>x.id),['a1']);assert.equal(changed.actions[0].deadline_id,null);
  assert.equal(event.actions[0].deadline_id,'d1');assert.equal(changed.actions[0].evidence[0],proof);
  assert.equal(changed.actions[1].selected,false);assert.equal(changed.actions[1].deadline_id,'d2');
  assert.match(confirmationSummary({...changed,participation:'yes',relevance:'yes'}),/1 个日期；创建 1 个待办任务/);
  assert.match(confirmationSummary({...changed,participation:'later'}),/不创建任务，保留 1 个行动方案/);
});
