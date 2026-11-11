import { AppFault,CloudStore,identifier } from './store.ts';

export function civilDay(at: Date,timezone='Asia/Shanghai'): string {
  const p=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(at).map(x=>[x.type,x.value]));
  return p.year+'-'+p.month+'-'+p.day;
}
export function civilInstant(value: string,timezone: string,fold?: number): number|null {
  if(!/^\d{4}-\d\d-\d\dT\d\d:\d\d$/.test(value))return null;
  const wanted=value.replace('T','-').replace(':','-').split('-').map(Number),naive=Date.UTC(wanted[0],wanted[1]-1,wanted[2],wanted[3],wanted[4]);
  try {
    const formatter=new Intl.DateTimeFormat('en-CA',{timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
    const fields=(at: number)=>{const p=Object.fromEntries(formatter.formatToParts(new Date(at)).map(x=>[x.type,x.value]));return [Number(p.year),Number(p.month),Number(p.day),Number(p.hour),Number(p.minute)];};
    const offsets=new Set<number>();
    for(const hours of [-36,-12,0,12,36]){const instant=naive+hours*3600000,p=fields(instant);offsets.add(Date.UTC(p[0],p[1]-1,p[2],p[3],p[4])-instant);}
    const candidates=[...offsets].map(offset=>naive-offset).filter(instant=>fields(instant).every((part,i)=>part===wanted[i])).sort((a,b)=>a-b);
    if(!candidates.length||candidates.length>1&&fold!==0&&fold!==1)return null;
    return candidates[fold===1?candidates.length-1:0];
  } catch{return null;}
}
export function deadlineState(item: any,at: Date): any {
  if (!item.confirmed_by_user || item.uncertain || !['date','minute','range'].includes(item.precision)) return {...item,state:'needs_confirmation'};
  const allDay=item.precision==='date' || item.precision==='range'&&item.range_precision==='date';
  const first=item.value,last=item.precision==='range'?item.end_value:first;
  if (typeof first!=='string'||typeof last!=='string') return {...item,state:'needs_confirmation'};
  const today=civilDay(at),startInstant=allDay?null:civilInstant(first,item.timezone||'Asia/Shanghai',item.fold),endInstant=allDay?null:civilInstant(last,item.timezone||'Asia/Shanghai',item.fold);
  if(!allDay&&(startInstant===null||endInstant===null||startInstant>endInstant))return {...item,state:'needs_confirmation'};
  const start=allDay?first.slice(0,10):civilDay(new Date(startInstant!)),end=allDay?last.slice(0,10):civilDay(new Date(endInstant!));
  if (!/^\d{4}-\d\d-\d\d$/.test(start)||!/^\d{4}-\d\d-\d\d$/.test(end)) return {...item,state:'needs_confirmation'};
  const overdue=allDay?end<today:endInstant!<at.getTime();
  const days=Math.round((Date.parse(end+'T00:00:00Z')-Date.parse(today+'T00:00:00Z'))/86400000);
  return {...item,all_day:allDay,state:overdue?'overdue':start<=today&&end>=today?'today':'upcoming',overdue,
    start_date:start,end_date:end,start_at:allDay?null:new Date(startInstant!).toISOString(),end_at:allDay?null:new Date(endInstant!).toISOString(),
    days_remaining:days,overdue_days:overdue?Math.max(0,-days):0,relative_label:allDay?(overdue?'已逾期 '+Math.max(0,-days)+' 天':days===0?'今天':'还有 '+days+' 天'):
      (overdue?'已逾期 ':'还有 ')+(Math.abs(endInstant!-at.getTime())>=86400000?Math.floor(Math.abs(endInstant!-at.getTime())/86400000)+' 天':Math.abs(endInstant!-at.getTime())>=3600000?Math.floor(Math.abs(endInstant!-at.getTime())/3600000)+' 小时':Math.max(1,Math.floor(Math.abs(endInstant!-at.getTime())/60000))+' 分钟'),display_timezone:'Asia/Shanghai'};
}
export function dto(row: any): any {return {...row.payload,_cloud:{kind:row.kind,id:row.id,revision:row.revision,updated_at:row.updated_at}};}
function bounded(value: string|null,fallback: number,min: number,max: number): number {
  const result=value===null?fallback:Number(value);
  if (!Number.isSafeInteger(result)||result<min||result>max) throw new AppFault(400,'invalid_page','请从列表重新选择范围。');
  return result;
}
export function paging(url: URL): {limit:number;offset:number} {return {limit:bounded(url.searchParams.get('limit'),20,1,100),offset:bounded(url.searchParams.get('offset'),0,0,100_000)};}

const WAITING_COPY='等待 AI 分析…', FAILED_COPY='AI 分析失败，可重新处理', LOW_COPY='部分内容需要确认';
export function isImageOrPdf(item: any): boolean {
  if (item?.input_kind==='image'||item?.input_kind==='pdf') return true;
  const files=[...(item?.attachments||[]), ...(item?.files||[])];
  return files.some((file: any)=>{
    const mime=file?.mime_type||file?.mime||'';
    return mime.startsWith('image/')||mime==='application/pdf'||['png','jpg','jpeg','webp','heic','heif','pdf'].includes(String(file?.extension||'').toLowerCase());
  });
}
export function inboxCard(item: any): any {
  const analysis=item?.vision_analysis&&typeof item.vision_analysis==='object'&&!Array.isArray(item.vision_analysis)?item.vision_analysis:{};
  const status=item?.vision_status||null;
  const note=item?.note_processing;
  const low=item?.vision_confidence==='low'||item?.preview_source==='low_confidence'||analysis.confidence==='low';
  const file=(item?.attachments||[])[0]||{};
  let body_source='text', body=typeof item?.preview==='string'?item.preview:'', notice: string|null=null;
  if (item?.preview_source==='human'&&body.trim()) body_source='human';
  else if (status==='failed'||item?.preview_source==='failed') {body_source='failed'; body=FAILED_COPY; notice=FAILED_COPY;}
  else if ((status==='completed'||item?.preview_source==='vision'||item?.preview_source==='low_confidence')&&low) {body_source='low_confidence'; body=LOW_COPY; notice=LOW_COPY;}
  else if (status==='completed'||item?.preview_source==='vision') {body_source='vision'; body=analysis.summary||analysis.readable_content||body||WAITING_COPY;}
  else if (note&&typeof note.message==='string') {body_source='note'; body=note.summary||note.message; notice=note.message;}
  else if (item?.input_kind==='pdf'||[...(item?.attachments||[]),...(item?.files||[])].some((f: any)=>(f?.mime_type||f?.mime)==='application/pdf'||String(f?.extension||'').toLowerCase()==='pdf')) {
    body_source='saved';
    const reasons: Record<string,string>={extracting:'正在提取 PDF 内容。',analyzing:'正在分析这份笔记。',failed:'PDF 处理未完成，请核对后重试。',recovery_needed:'PDF 处理已中断，原件保留。'};
    body=item?.error?.message||reasons[item?.processing_status]||'PDF 已保存，尚无已确认的 AI 分析结果。'; notice=body;
  }
  else if (isImageOrPdf(item)) {body_source='waiting'; body=WAITING_COPY; notice=WAITING_COPY;}
  return {body_source, body, notice, title:analysis.title||item?.title||file.filename||'', course:item?.course?.name||item?.course?.label||analysis.probable_course||null,
    summary:analysis.summary||(body_source==='note'?note.summary:null)||null, readable_content:analysis.readable_content||null, vision_status:status, confidence:item?.vision_confidence||analysis.confidence||null,
    key_point_count:Array.isArray(analysis.key_points)?analysis.key_points.length:0, equation_count:Array.isArray(analysis.equations)?analysis.equations.length:0,
    filename:file.filename||null, mime:file.mime_type||file.mime||null};
}
export function presentInbox(value: any, url: URL): any {
  const advanced=url.searchParams.get('advanced')==='1'||url.searchParams.get('debug')==='1';
  const card=inboxCard(value);
  const next={...value, card, preview:card.body, preview_source:card.body_source};
  const original=value?.review?.snapshot?.original_text;
  if (value?.review?.snapshot) {
    const snapshot={...value.review.snapshot};
    if (!advanced) delete snapshot.original_text;
    next.review={...value.review, snapshot};
  }
  if (!advanced) delete next.ocr_preview;
  else next.debug={collapsed:true, ocr_preview:value?.ocr_preview??null, original_text:typeof original==='string'?original:null};
  if (typeof original==='string'||value?.ocr_preview) next.debug_available=true;
  return next;
}

export class Views {
  constructor(public store: CloudStore,public workspace: string) {}
  async require(kind: string,id: string): Promise<any> {
    const row=await this.store.one(this.workspace,kind,id);
    if (!row) throw new AppFault(404,'not_found','这份资料尚未同步，请稍后重试。');
    return dto(row);
  }
  async facade(path: string): Promise<any> {return this.require('facade',path);}
  async list(kind: string,url: URL,filter='',order='updated_at.desc,id.asc'): Promise<any> {
    const {limit,offset}=paging(url),page=await this.store.page(this.workspace,kind,offset,limit,filter,order);
    return {...page,items:page.items.map(dto)};
  }
  async progress(kind: string): Promise<Map<string,any>> {
    return new Map((await this.store.rows(this.workspace,kind)).map(row=>[row.id,dto(row)]));
  }
  async taskStates(): Promise<Map<string,any>> {
    const [records,progress]=await Promise.all([this.store.rows(this.workspace,'task'),this.progress('task_progress')]);
    return new Map(records.map(row=>[row.id,{...dto(row),progress:progress.get(row.id)||{_cloud:{revision:0}}}]));
  }
  eventTasks(event: any,states: Map<string,any>): any {
    return {...event,tasks:(event.tasks||[]).map((task: any)=>{
      const current=states.get(task.id);if(!current)return task;
      const progress=current.progress;
      return {...task,_cloud:current._cloud,progress,status:progress.application_state!=='needs_confirmation'&&typeof progress.completed==='boolean'?(progress.completed?'done':'todo'):task.status};
    })};
  }
  async reviewList(url: URL): Promise<any> {
    const {limit,offset}=paging(url),unreadOnly=url.searchParams.get('unread_only')==='true';
    const [queue,progress]=await Promise.all([this.store.one(this.workspace,'facade','/review-queue'),this.progress('review_progress')]);
    const reading=(id: string)=>progress.get(id)||{completed:false,_cloud:{revision:0}};
    if(queue?.payload.schema_version!==1||!Array.isArray(queue.payload.members)) {
      // Compatibility while an older processor is still publishing. Keep its
      // real versions visible until the canonical membership arrives.
      let items=(await this.store.rows(this.workspace,'review_item')).map(dto).sort((a,b)=>b._cloud.updated_at.localeCompare(a._cloud.updated_at)||a.id.localeCompare(b.id));
      const unread=items.filter(item=>item.has_content&&!reading(item.id).completed&&!['superseded','rejected'].includes(item.status));
      const incomplete=items.filter(item=>!item.has_content&&!['superseded','rejected'].includes(item.status));
      if(unreadOnly)items=unread;
      return {items:items.slice(offset,offset+limit).map(item=>({...item,reading:reading(item.id)})),total:items.length,limit,offset,unread_total:unread.length,incomplete_total:incomplete.length};
    }
    const members=queue.payload.members;
    const unread=members.filter((member: any)=>member.has_content&&!reading(member.review_id).completed);
    const selected=(unreadOnly?unread:members).slice(offset,offset+limit);
    const ids=selected.map((member: any)=>identifier(member.review_id));
    const records=ids.length?await this.store.rows(this.workspace,'review_item','&id=in.('+ids.join(',')+')'):[];
    const byId=new Map(records.map(row=>[row.id,row]));
    if(ids.some((id: string)=>!byId.has(id)))throw new AppFault(503,'review_queue_syncing','课堂复盘正在同步，稍后会自动继续。');
    return {items:selected.map((member: any)=>({...dto(byId.get(member.review_id)),reading:reading(member.review_id),
      newer_than_adopted:member.newer_than_adopted,newer_unfinished_version:member.newer_unfinished_version})),
      total:unreadOnly?unread.length:members.length,limit,offset,unread_total:unread.length,
      incomplete_total:members.filter((member: any)=>!member.has_content).length};
  }
  async planning(url: URL,path: string): Promise<any> {
    const facts=await this.facade('/planning/facts'),at=this.store.clock(),today=civilDay(at);
    const includeTerminal=url.searchParams.get('include_terminal')==='true';
    const reminders=(await this.store.rows(this.workspace,'reminder')).map(x=>x.payload);
    const reminderIds=new Set(reminders.filter(r=>!['cancelled','dismissed'].includes(r.status)&&[r.snoozed_until||r.scheduled_at,r.fired_at].some(v=>v&&civilDay(new Date(v))===today)).map(r=>r.deadline_id));
    let items=facts.items.filter((item: any)=>includeTerminal||!['done','cancelled'].includes(item.event_status)).map((item: any)=>deadlineState(item,at)).filter((x: any)=>x.state!=='needs_confirmation');
    if (path==='/planning/today') items=items.filter((x: any)=>['overdue','today'].includes(x.state)||reminderIds.has(x.id)).map((x: any)=>({...x,reasons:[x.state==='overdue'?x.relative_label:x.state==='today'?'今天截止':'今天有提醒']}));
    else {
      const days=bounded(url.searchParams.get('days'),7,7,30);
      if (![7,14,30].includes(days)) throw new AppFault(400,'invalid_range','请选择一周、两周或一个月。');
      const end=new Date(Date.parse(today+'T00:00:00Z')+days*86400000).toISOString().slice(0,10);
      items=items.filter((x: any)=>x.state!=='overdue'&&x.start_date<end&&x.end_date>=today);
    }
    items.sort((a: any,b: any)=>a.end_date.localeCompare(b.end_date)||a.id.localeCompare(b.id));
    return {items,total:items.length,timezone:'Asia/Shanghai',today};
  }
  async dashboard(url: URL): Promise<any> {
    const at=this.store.clock(),today=civilDay(at);
    const [timetable,planning,reviews,events,inbox,worker,taskStates]=await Promise.all([
      this.facade('/timetable'),this.planning(url,'/planning/today'),
      this.reviewList(new URL('?limit=8&unread_only=true',url)),this.store.rows(this.workspace,'event','&payload->>status=not.in.(done,cancelled)'),
      this.store.rpc('inbox_page',{p_workspace:this.workspace,p_status:'needs_review',p_scope:'submitted',p_offset:0,p_limit:1}),
      this.store.db('eric_app_workers?workspace_id=eq.'+this.workspace+'&limit=1'),this.taskStates()]);
    const classes=(timetable.sessions||[]).filter((item: any)=>civilDay(new Date(item.start))===today).map((item: any)=>({...item,course_id:timetable.course_mappings?.[item.title]||null,
      state:Date.parse(item.end)<at.getTime()?'finished':Date.parse(item.start)<=at.getTime()?'in_progress':'upcoming'})).sort((a: any,b: any)=>a.start.localeCompare(b.start));
    const tasks=events.flatMap(row=>this.eventTasks(dto(row),taskStates).tasks.filter((x: any)=>x.status!=='done'||x.progress?.application_state==='needs_confirmation').map((x: any)=>({...x,event_id:row.id,event_title:row.payload.title,event_version:row.payload.version})));
    const unread=reviews.items;
    const nextClass=classes.find((x: any)=>x.state!=='finished');
    const next=nextClass?{kind:'class',title:nextClass.state==='in_progress'?'正在上课：'+nextClass.title:'下一节：'+nextClass.title,item:nextClass}:unread.length?{kind:'review',title:'读一份课堂复盘',id:unread[0].id}:tasks.length?{kind:'event',title:tasks[0].title,id:tasks[0].event_id}:inbox.total?{kind:'inbox',title:'核对待确认内容'}:{kind:'capture',title:'随手记下今天的新想法'};
    const seen=worker[0]?.last_seen_at;
    return {today,timezone:'Asia/Shanghai',classes,deadlines:planning.items,tasks,reviews:unread,review_count:reviews.unread_total,review_incomplete_count:reviews.incomplete_total,needs_confirmation:inbox.total,next_action:next,
      processing_online:Boolean(seen&&at.getTime()-Date.parse(seen)<90_000),published_at:worker[0]?.published_at||null};
  }
  async read(path: string,url: URL): Promise<any> {
    if (/^\/documents\/[^/]+$/.test(path)) return this.require('document',identifier(decodeURIComponent(path.split('/')[2])));
    if (path==='/dashboard') return this.dashboard(url);
    if (['/planning/today','/planning/upcoming'].includes(path)) return this.planning(url,path);
    if (path==='/courses') return this.list('course',url);
    if (/^\/courses\/[^/]+$/.test(path)) return this.require('course',identifier(decodeURIComponent(path.split('/')[2])));
    if (path==='/events') {
      const status=url.searchParams.get('status')||'active';
      if (!['active','all','done','cancelled'].includes(status)) throw new AppFault(400,'invalid_status','请选择事项状态。');
      const page=await this.list('event',url,status==='all'?'':status==='active'?'&payload->>status=not.in.(done,cancelled)':'&payload->>status=eq.'+status);
      const states=await this.taskStates();
      return {...page,items:page.items.map((e: any)=>{const event=this.eventTasks(e,states);return {...event,task_count:event.tasks.length,completed_task_count:event.tasks.filter((t: any)=>t.status==='done').length,deadline_count:e.deadlines.length};})};
    }
    if (/^\/events\/[^/]+$/.test(path)) {const [event,states]=await Promise.all([this.require('event',identifier(decodeURIComponent(path.split('/')[2]))),this.taskStates()]);return this.eventTasks(event,states);}
    if (path==='/inbox') {
      const status=url.searchParams.get('status')||'all',scope=url.searchParams.get('scope')||'all';
      const {offset,limit}=paging(url);
      const page=await this.store.rpc('inbox_page',{p_workspace:this.workspace,p_status:status,p_scope:scope,p_offset:offset,p_limit:limit});
      return {...page,items:page.items.map((row: any)=>presentInbox(dto(row),url))};
    }
    if (/^\/inbox\/[^/]+$/.test(path)) {
      const id=identifier(decodeURIComponent(path.split('/')[2])),[value,progress]=await Promise.all([this.require('inbox',id),this.store.one(this.workspace,'inbox_progress',id)]);
      return {...presentInbox(value,url),archive:progress?dto(progress):{archived:false,_cloud:{revision:0}}};
    }
    if (path==='/reviews') return this.reviewList(url);
    if (path==='/recent') return this.list('recent_item',url,'','payload->>occurred_at.desc,id.asc');
    if (/^\/raw\/[^/]+$/.test(path)) return this.require('raw_review',identifier(decodeURIComponent(path.split('/')[2])));
    if (path==='/school') {
      const course=url.searchParams.get('course_id');
      const scope=course?'?course_id='+identifier(course):'';
      const overview=await this.facade('/school'+scope),page=await this.list('lecture_item',url,course?'&payload->>course_id=eq.'+identifier(course):'','payload->>lecture_date.desc,id.asc');
      return {...overview,...page};
    }
    if(path==='/school/textbooks') {
      const course=url.searchParams.get('course_id');
      return this.list('textbook',url,course?'&payload->>course_id=eq.'+identifier(course):'','payload->>title.asc,id.asc');
    }
    if(/^\/school\/textbooks\/[^/]+$/.test(path))return this.require('textbook',identifier(decodeURIComponent(path.split('/')[3])));
    if(path==='/school/textbook'&&url.searchParams.has('course_id')) {
      const overview=await this.facade('/school?course_id='+identifier(url.searchParams.get('course_id')));
      return overview.textbook;
    }
    if(path==='/school/notes'&&url.searchParams.has('course_id'))
      return this.facade('/school/notes?course_id='+identifier(url.searchParams.get('course_id')));
    if (/^\/school\/lectures\/[^/]+$/.test(path)) return this.require('lecture',identifier(decodeURIComponent(path.split('/')[3])));
    if (/^\/school\/reviews\/[^/]+$/.test(path)) {
      const id=identifier(decodeURIComponent(path.split('/')[3])),[review,progress]=await Promise.all([this.require('review',id),this.store.one(this.workspace,'review_progress',id)]);
      return {...review,reading:progress?dto(progress):{completed:false,_cloud:{revision:0}}};
    }
    if (/^\/learning\/topics\/[^/]+$/.test(path)) return this.require('topic',identifier(decodeURIComponent(path.split('/')[3])));
    if (/^\/learning\/captures\/[^/]+$/.test(path)) return this.require('learning_capture',identifier(decodeURIComponent(path.split('/')[3])));
    if (['/learning','/raw','/school/textbook','/school/notes'].includes(path)||/^\/school\/(lectures|jobs)\/[^/]+\/(context|cost|section-suggestions|page-source)$/.test(path)||/^\/school\/jobs\/[^/]+$/.test(path)||/^\/learning\/retests\/[^/]+$/.test(path)) {
      return this.facade(path+(path.endsWith('/page-source')?'?document_id='+identifier(url.searchParams.get('document_id'))+'&page='+bounded(url.searchParams.get('page'),1,1,5000):''));
    }
    throw new AppFault(404,'not_found','这个入口暂时不可用，请从 Eric OS 导航打开。');
  }
}
