import {AppFault,SHA,UUID,hash,stable} from './store.ts';
import {validateWechatBundle,safeText} from './guarded_wechat_publish.ts';
import {createReceiptFencePublisher,createReceiptFenceReconciler} from './receipt_fence_experiment.ts';

const POLICY='wechat-personal-actions-v2';
const CORE=['raw_item','inbox_item','inbox','raw_review'];
const FORMAL=['event','task','deadline'];
const object=(v:any,keys:string[])=>v && typeof v==='object' && !Array.isArray(v) && stable(Object.keys(v).sort())===stable([...keys].sort());
const uuid=(v:any)=>typeof v==='string' && UUID.test(v);
const integer=(v:any)=>Number.isSafeInteger(v) && v>=0;
const stamp=(v:any)=>typeof v==='string' && /(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
function invalid():never {throw new AppFault(400,'invalid_automatic_task_bundle','自动事项的来源或内容无法核对。');}
function require(value:any):asserts value {if(!value)invalid();}

export function automationScope(raw:string):any {
  let v:any;try{v=JSON.parse(raw);}catch{throw new AppFault(503,'automation_disabled','自动事项同步尚未获准启用。');}
  if(!object(v,['enabled','workspace_id','processor_id','not_before','activation_hash','source_ids']) || v.enabled!==true ||
    !uuid(v.workspace_id) || !uuid(v.processor_id) || !stamp(v.not_before) || !SHA.test(v.activation_hash) ||
    !Array.isArray(v.source_ids) || v.source_ids.length!==5 || new Set(v.source_ids).size!==5 ||
    !v.source_ids.every((id:any)=>typeof id==='string' && /^wechat:[A-Za-z0-9_-]{1,80}$/.test(id)))
    throw new AppFault(503,'automation_disabled','自动事项同步尚未获准启用。');
  return v;
}

export async function validateAutomaticTasks(body:any,scope:any):Promise<any> {
  require(object(body,['raw_id','snapshot_id','source_id','records','epoch','generation']) &&
    uuid(body.raw_id) && uuid(body.snapshot_id) && uuid(body.epoch) && integer(body.generation) &&
    body.generation<Number.MAX_SAFE_INTEGER && scope.source_ids.includes(body.source_id) &&
    Array.isArray(body.records) && body.records.length>=6 && body.records.length<=128);
  const seen=new Set<string>();
  for(const r of body.records){
    require(object(r,['kind','id','payload','source_hash']) && [...CORE,...FORMAL].includes(r.kind) && uuid(r.id) &&
      !seen.has(r.kind+'\0'+r.id) && SHA.test(r.source_hash) && r.source_hash===await hash(stable(r.payload)));
    seen.add(r.kind+'\0'+r.id);
  }
  const core=structuredClone(body.records.filter((r:any)=>CORE.includes(r.kind)));
  require(core.length===4 && new Set(core.map((r:any)=>r.kind)).size===4);
  const by=Object.fromEntries(core.map((r:any)=>[r.kind,r.payload]));
  const review=by.raw_review,summary=by.inbox_item,detail=by.inbox;
  require(['pending','confirmed'].includes(review.status) && Array.isArray(review.confirmed_event_ids) &&
    review.confirmed_event_ids.length>0 && summary.needs_manual===false &&
    summary.processing_status==='complete' && summary.processing_stage==='complete');
  const confirmed=[...review.confirmed_event_ids];
  review.status='archived';review.confirmed_event_ids=[];
  Object.assign(summary,{needs_manual:true,processing_status:'needs_review',processing_stage:'review',review_status:'archived'});
  Object.assign(detail,summary);detail.review=review;
  for(const r of core)r.source_hash=await hash(stable(r.payload));
  await validateWechatBundle({...body,records:core}); // v1 remains strict and cannot publish tasks.
  const original=body.records.find((r:any)=>r.kind==='raw_review').payload.snapshot;
  require(Date.parse(by.raw_item.created_at)>=Date.parse(scope.not_before) &&
    original.wechat.messages.every((m:any)=>Date.parse(m.source_time)>=Date.parse(scope.not_before) && Date.parse(m.captured_at)>=Date.parse(scope.not_before)));
  const events=body.records.filter((r:any)=>r.kind==='event'), tasks=body.records.filter((r:any)=>r.kind==='task'), dates=body.records.filter((r:any)=>r.kind==='deadline');
  require(events.length>0 && stable([...confirmed].sort())===stable(events.map((r:any)=>r.id).sort()));
  const blocks=new Map(original.blocks.map((b:any)=>[b.block_id,b.text]));
  const linkedTasks=new Set<string>(),linkedDates=new Set<string>();
  for(const row of events){
    const e=row.payload;
    require(object(e,['id','raw_item_id','raw_snapshot_id','title','summary','category','status','version','created_at','updated_at',
      'tasks','deadlines','evidence','raw_snapshot','source','course','course_id','evidence_snapshots','source_update','context','automation','priority']) &&
      e.id===row.id && e.raw_item_id===body.raw_id && e.raw_snapshot_id===body.snapshot_id && e.status==='needs_action' && e.version===1 &&
      stamp(e.created_at) && stamp(e.updated_at) && Date.parse(e.created_at)>=Date.parse(scope.not_before) &&
      safeText(e.title) && safeText(e.summary) && safeText(e.category) && e.course===null && e.course_id===null &&
      e.source_update===null && object(e.evidence_snapshots,[]) && stable(e.raw_snapshot)===stable(original) &&
      object(e.source,['id','kind','name']) && e.source.id===body.source_id && e.source.kind==='wechat' && e.source.name==='微信通知');
    const a=e.automation;
    require(object(a,['policy','activation_hash','source_key','job_id','attempt_id','batch_id']) && a.policy===POLICY &&
      a.activation_hash===scope.activation_hash && SHA.test(a.source_key) && uuid(a.job_id) && uuid(a.attempt_id) &&
      a.batch_id===original.wechat.batch_id);
    require(object(e.context,['relevance','participation','notice_kind','uncertainties','tasks_activated','planned_actions','audience','eligibility','materials','locations']) &&
      stable(e.context)==stable({relevance:'yes',participation:'yes',notice_kind:'new',uncertainties:[],tasks_activated:true,
        planned_actions:[],audience:null,eligibility:[],materials:[],locations:[]}));
    require(Array.isArray(e.tasks) && e.tasks.length>0 && e.tasks.length<=32 && Array.isArray(e.deadlines) && e.deadlines.length<=32 &&
      Array.isArray(e.evidence) && e.evidence.length>0 && e.evidence.length<=200);
    for(const q of e.evidence){
      require(object(q,['id','event_id','raw_snapshot_id','field_path','block_id','quote','start','end']) && uuid(q.id) &&
        q.event_id===e.id && q.raw_snapshot_id===body.snapshot_id && typeof q.field_path==='string' &&
        /^(?:tasks\.[0-9a-f-]+\.title|planned_actions\.[A-Za-z0-9_-]+\.title|deadlines\.[0-9a-f-]+\.value|audience\.0\.text|locations\.\d+\.text)$/.test(q.field_path) &&
        safeText(q.quote) && integer(q.start) && integer(q.end) && q.end>q.start && typeof blocks.get(q.block_id)==='string' &&
        [...String(blocks.get(q.block_id))].slice(q.start,q.end).join('')===q.quote);
    }
    for(const t of e.tasks){
      require(object(t,['id','event_id','title','order','deadline_id','origin','status','created_at','updated_at']) && uuid(t.id) &&
        t.event_id===e.id && t.origin==='explicit' && t.status==='todo' && integer(t.order) && safeText(t.title) && stamp(t.created_at) && stamp(t.updated_at) &&
        (t.deadline_id===null || uuid(t.deadline_id)) && !linkedTasks.has(t.id) &&
        e.evidence.some((q:any)=>q.field_path==='tasks.'+t.id+'.title' && q.quote.includes(t.title)));
      require(tasks.some((r:any)=>r.id===t.id && stable(r.payload)===stable(t)));linkedTasks.add(t.id);
    }
    for(const d of e.deadlines){
      require(object(d,['id','event_id','label','value','precision','original_text','confirmed_by_user','verified_automatically','created_at','updated_at','end_value','range_precision','timezone']) &&
        uuid(d.id) && d.event_id===e.id && d.confirmed_by_user===false && d.verified_automatically===true && safeText(d.label) && safeText(d.original_text) &&
        ['date','minute','range'].includes(d.precision) && d.timezone==='Asia/Shanghai' && stamp(d.created_at) && stamp(d.updated_at) &&
        /^\d{4}-\d\d-\d\d(?:T\d\d:\d\d)?$/.test(d.value) && !linkedDates.has(d.id) &&
        e.evidence.some((q:any)=>q.field_path==='deadlines.'+d.id+'.value' && q.quote.includes(d.original_text)));
      require(dates.some((r:any)=>r.id===d.id && stable(r.payload)===stable(d)));linkedDates.add(d.id);
    }
    require(e.tasks.every((t:any)=>t.deadline_id===null || e.deadlines.some((d:any)=>d.id===t.deadline_id)));
    // Priority is a display reducer output, never an instruction or hidden source field.
    require(JSON.stringify(e.priority).length<4000 && !/local_path|api_key|worker_key|raw_output|authorization/i.test(JSON.stringify(e.priority)));
  }
  require(tasks.length===linkedTasks.size && dates.length===linkedDates.size);
  return body;
}

export function validateAutomaticReconciliation(v:any,scope:any):any {
  require(object(v,['raw_id','snapshot_id','source_id','records','epoch','generation']) && uuid(v.raw_id) && uuid(v.snapshot_id) &&
    uuid(v.epoch) && integer(v.generation) && v.generation<Number.MAX_SAFE_INTEGER && scope.source_ids.includes(v.source_id) &&
    Array.isArray(v.records) && v.records.length>=6 && v.records.length<=128);
  const expected:any={raw_item:v.raw_id,inbox_item:v.raw_id,inbox:v.raw_id,raw_review:v.snapshot_id};const seen=new Set<string>();
  for(const r of v.records){require(object(r,['kind','id','source_hash']) && [...CORE,...FORMAL].includes(r.kind) && uuid(r.id) &&
    (!CORE.includes(r.kind) || expected[r.kind]===r.id) && SHA.test(r.source_hash) && !seen.has(r.kind+'\0'+r.id));seen.add(r.kind+'\0'+r.id);}
  require(CORE.every(k=>seen.has(k+'\0'+expected[k])) && v.records.some((r:any)=>r.kind==='event') && v.records.some((r:any)=>r.kind==='task'));
  return v;
}

export function createAutomaticTaskEndpoints(options:{databaseUrl:string;scope:string;clientFactory?:(url:string)=>Promise<any>}) {
  const scope=automationScope(options.scope);
  const publish=createReceiptFencePublisher(options,(b:any)=>validateAutomaticTasks(b,scope));
  const reconcile=createReceiptFenceReconciler(options,(b:any)=>validateAutomaticReconciliation(b,scope));
  const guard=(fn:any)=>async(workspace:string,processor:string,body:any)=>{
    if(workspace!==scope.workspace_id || processor!==scope.processor_id)throw new AppFault(403,'automation_scope_denied','自动事项同步超出授权范围。');
    return fn(workspace,processor,body);
  };
  return {publish:guard(publish),reconcile:guard(reconcile),status:guard(async()=>({
    protocol:POLICY,activation_hash:scope.activation_hash,not_before:scope.not_before,source_ids:scope.source_ids}))};
}
