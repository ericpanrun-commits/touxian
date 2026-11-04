import { AppFault,CloudStore,ID,SHA,UUID,PART_BYTES,boundedBytes,boundedJson,hash,identifier,stable } from './store.ts';
import { fileResponse,manifest,readableResource,ticket,verifyResource,verifyTicket } from './files.ts';
import { visionRoute } from './vision.ts';
import { Views,dto,paging } from './views.ts';

const CAPTURE_FIELDS=['id','device_id','source','title','text_content','purpose','files','course_candidates','shared_at','received_at','state','raw_item_id','learning_capture_id','course_id','lecture_id','error','attempts','next_attempt_at','last_attempt_at','completed_at','vision_status','vision_provider','vision_analysis','vision_confidence','vision_processed_at','vision_error','revision','created_at','updated_at'];
const COMMAND_FIELDS=['id','resource_key','method','path','state','result','error','attempts','next_attempt_at','last_attempt_at','completed_at','revision','created_at','updated_at'];
function project(row: any,fields: string[]): any {return Object.fromEntries(fields.filter(key=>key in row).map(key=>[key,row[key]]));}
export function publicCapture(row: any): any {return project(row,CAPTURE_FIELDS);}
function commandDto(row: any): any {return project(row,COMMAND_FIELDS);}
function uuid(value: unknown): string {if(typeof value!=='string'||!UUID.test(value))throw new AppFault(400,'invalid_request','这次操作的标识无效，请重新打开页面。');return value;}
function integer(value: unknown,min=0,max=Number.MAX_SAFE_INTEGER): number {if(!Number.isSafeInteger(value)||Number(value)<min||Number(value)>max)throw new AppFault(400,'invalid_input','请核对填写内容。');return value as number;}
function text(value: unknown,max: number,required=false): string {if(typeof value!=='string'||value.length>max||(required&&!value.trim()))throw new AppFault(400,'invalid_input','请核对填写内容。');return value;}

export function captureInput(value: any,device: string,at=new Date()): any {
  if(!value||typeof value!=='object'||Array.isArray(value))throw new AppFault(400,'invalid_capture','内容不完整，请重新发送。');
  const files=value.files;
  if(!Array.isArray(files)||files.length>10)throw new AppFault(400,'invalid_files','一次最多发送 10 个文件。');
  const normalized=files.map((file: any)=>{
    const meta=manifest({...file,id:file.resource_id},50_000_000);
    if(meta.id!=='capture:'+value.id+':'+meta.sha256)throw new AppFault(400,'invalid_file','文件与这次收集不一致，请重新发送。');
    return {resource_id:meta.id,sha256:meta.sha256,bytes:meta.bytes,filename:meta.filename,mime:meta.mime,part_count:meta.part_count};
  });
  if(new Set(normalized.map((x: any)=>x.resource_id)).size!==files.length)throw new AppFault(400,'duplicate_file','同一份文件只需添加一次。');
  if(!['capture','lecture_note','textbook'].includes(value.purpose)||!Array.isArray(value.course_candidates)||value.course_candidates.length>10)throw new AppFault(400,'invalid_capture','请选择收集用途。');
  let candidates=value.course_candidates.map((x: any)=>({course_id:identifier(x.course_id),label:text(x.label||'',200),basis:'device_suggestion'}));
  if(value.selected_course_id!=null) {
    const selected=identifier(value.selected_course_id),label=candidates.find((x: any)=>x.course_id===selected)?.label||'';
    candidates=[{course_id:selected,label,basis:'owner_selected'},...candidates.filter((x: any)=>x.course_id!==selected)];
    if(candidates.length>10)throw new AppFault(400,'invalid_capture','课程线索过多，请只保留本次相关课程。');
  }
  const shared=text(value.shared_at,60,true),moment=Date.parse(shared);
  if(!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(shared)||!Number.isFinite(moment)||moment<Date.UTC(2020,0)||moment>at.getTime()+300_000)throw new AppFault(400,'invalid_time','记录时间无效，请核对设备时间。');
  const content=text(value.text_content||'',100_000);
  if(!content.trim()&&!files.length)throw new AppFault(400,'empty_capture','先写点内容，或选择要保存的文件。');
  return {id:uuid(value.id),device_id:device,source:'pwa',title:text(value.title,240,true),text_content:content,purpose:value.purpose,files:normalized,course_candidates:candidates,shared_at:shared};
}

// A controlled set of existing guarded domain operations, never an arbitrary local HTTP proxy.
export async function commandInput(store: CloudStore,workspace: string,device: string,value: any): Promise<any> {
  if(!value||!['POST','PUT','PATCH'].includes(value.method)||!value.body||Array.isArray(value.body)||typeof value.body!=='object')throw new AppFault(400,'invalid_command','修改内容不完整，请核对后重试。');
  const path=text(value.path,512,true);
  let kind='',id='',match;
  if((match=/^\/events\/([A-Za-z0-9_.:-]+)(?:\/(status|restore|course|tasks|deadlines)(?:\/[A-Za-z0-9_.:-]+)?)?$/.exec(path))) {kind='event';id=match[1];}
  else if((match=/^\/inbox\/([A-Za-z0-9_.:-]+)\/(links|retry)$/.exec(path))) {kind='inbox';id=match[1];}
  else if((match=/^\/raw\/([A-Za-z0-9_.:-]+)\/(confirm|draft|cancel|resume)$/.exec(path))) {kind='raw_review';id=match[1];}
  else if((match=/^\/school\/reviews\/([A-Za-z0-9_.:-]+)\/(feedback|proposals|source-correction)$/.exec(path))) {
    const review=await store.one(workspace,'review',match[1]);if(review){kind='lecture';id=review.payload.lecture_id;}
  }
  else if((match=/^\/school\/lectures\/([A-Za-z0-9_.:-]+)\/(sections|generate|vision)$/.exec(path))) {kind='lecture';id=match[1];}
  else if((match=/^\/learning\/captures\/([A-Za-z0-9_.:-]+)\/confirm$/.exec(path))) {kind='learning_capture';id=match[1];}
  else if(path==='/courses') {kind='course';id='new:'+uuid(value.id);}
  else if(path==='/school/lectures') {kind='lecture';id='new:'+uuid(value.id);}
  else if(path==='/school/textbooks'&&value.method==='POST') {kind='course';id=identifier(value.body.course_id);}
  else if((path==='/school/textbook/file'&&value.method==='PUT')||(path==='/school/sections'&&value.method==='POST')) {
    kind='textbook';id=identifier(value.body.textbook_id);
    integer(value.body.expected_version,1);
  }
  else if((match=/^\/school\/sections\/([A-Za-z0-9_.:-]+)\/index$/.exec(path))&&value.method==='POST') {
    const section=await store.one(workspace,'document_section',match[1]);
    if(section){kind='textbook';id=identifier(section.payload.textbook_id);}
  }
  else if((match=/^\/school\/documents\/([A-Za-z0-9_.:-]+)\/catalog$/.exec(path))&&value.method==='POST') {kind='document';id=match[1];}
  else if(['/learning/history/confirm','/learning/analysis/authorization','/learning/answers','/learning/sync','/learning/external-reviews'].includes(path)) {kind='facade';id='/learning';}
  if(!kind||!id)throw new AppFault(400,'unsupported_action','请从当前页面提供的操作继续。');
  const expected=integer(value.expected_revision),resource_key=kind+':'+id;
  // Original domain versions are still checked again by their canonical Windows service.
  return {id:uuid(value.id),device_id:device,resource_key,method:value.method,path,body:value.body,expected_revision:expected};
}

export function createHandler(options: {base:string;secret:string;visionSecret?:string;origins?:string[];fetcher?:typeof fetch;clock?:()=>Date;officialHealth?:(workspace:string)=>Promise<any>}) {
  const store=new CloudStore(options),origins=new Set(options.origins||[]);
  function json(value: any,status=200): Response {return Response.json(value,{status,headers:{'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'}});}
  async function route(req: Request,url: URL,path: string): Promise<Response> {
    if(req.method==='GET'&&path==='/health')return json({service:'eric-app-api',version:1,ready:true});
    const file=/^\/files\/([A-Za-z0-9_.:%-]+)\/content$/.exec(path);
    if(file&&['GET','HEAD'].includes(req.method)&&url.searchParams.has('ticket')) {
      const id=identifier(decodeURIComponent(file[1])),claim=await verifyTicket(store,url.searchParams.get('ticket')||'',id);
      return fileResponse(store,req,claim.w,id);
    }
    if(path.startsWith('/worker/')) return workerRoute(req,url,path);
    if(path==='/session'&&req.method==='POST') {
      const p=await store.principal(req),body=await boundedJson(req,2000);
      if(!['windows','iphone','ipad','web'].includes(body.platform))throw new AppFault(400,'invalid_device','设备类型无效。');
      const device=await store.rpc('device_session',{p_workspace:p.workspace.id,p_user:p.user.id,p_session:p.sessionId,p_label:text(body.label,80,true),p_platform:body.platform});
      return json({device:device[0],timezone:p.workspace.timezone,account:{id:p.user.id,email:p.user.email}});
    }
    const principal=await store.session(req),workspace=principal.workspace.id,views=new Views(store,workspace);
    if(path==='/devices'&&req.method==='GET')return json({items:await store.db('eric_app_devices?workspace_id=eq.'+workspace+'&select=id,label,platform,created_at,last_seen_at,revoked_at&order=created_at.desc&limit=200'),current:principal.device.id});
    if(path==='/devices/revoke'&&req.method==='POST') {const body=await boundedJson(req,1000);return json({revoked:await store.rpc('revoke_device',{p_workspace:workspace,p_device:uuid(body.id)})});}
    if(path==='/processors'&&req.method==='GET')return json({items:await store.db('eric_app_processors?workspace_id=eq.'+workspace+'&select=id,label,created_at,last_seen_at,revoked_at&order=created_at.desc&limit=20')});
    if(path==='/processors/revoke'&&req.method==='POST') {const body=await boundedJson(req,1000);await store.db('eric_app_processors?workspace_id=eq.'+workspace+'&id=eq.'+uuid(body.id),'PATCH',{revoked_at:store.clock().toISOString()});return json({revoked:true});}
    if(path==='/changes'&&req.method==='GET') {
      const after=integer(Number(url.searchParams.get('after')||0)),items=await store.db('eric_app_changes?workspace_id=eq.'+workspace+'&revision=gt.'+after+'&order=revision.asc&limit=200&select=revision,kind,record_id');
      return json({items,cursor:items.at(-1)?.revision||after,more:items.length===200});
    }
    if(path==='/activity'&&req.method==='GET'){
      const [commands,tasks]=await Promise.all([store.rpc('pending_changes',{p_workspace:workspace}),store.db('eric_app_records?workspace_id=eq.'+workspace+'&kind=eq.task_progress&deleted=eq.false&payload->>application_state=eq.needs_confirmation&order=revision.desc&limit=100')]);
      return json({items:[...commands.map(commandDto),...tasks.map((row: any)=>({id:'task-progress:'+row.id,resource_key:'event:'+row.payload.event_id,path:'/events/'+row.payload.event_id+'/tasks/'+row.id,state:'needs_confirmation',result:{message:row.payload.message},revision:row.revision}))]});
    }
    if(path==='/system'&&req.method==='GET') {
      const [worker,pending,latest]=await Promise.all([store.db('eric_app_workers?workspace_id=eq.'+workspace+'&limit=1'),store.db('eric_app_captures?workspace_id=eq.'+workspace+'&state=in.(received,processing,failed,needs_confirmation)&select=id,state&limit=1000'),store.db('eric_app_changes?workspace_id=eq.'+workspace+'&order=revision.desc&limit=1&select=revision')]);
      const online=Boolean(worker[0]&&store.clock().getTime()-Date.parse(worker[0].last_seen_at)<90_000);
      return json({cloud_available:true,processing_online:online,last_processed_at:worker[0]?.published_at||null,cursor:latest[0]?.revision||0,waiting:pending.filter((x: any)=>['received','processing'].includes(x.state)).length});
    }
    if(path==='/captures'&&req.method==='POST') {
      const value=captureInput(await boundedJson(req),principal.device.id,store.clock());
      const selected=value.course_candidates.find((x: any)=>x.basis==='owner_selected');
      if(selected&&!await store.one(workspace,'course',selected.course_id))throw new AppFault(400,'invalid_course','这门课尚未同步，请从课程列表重新选择；原件仍保存在此设备。');
      const request_hash=await hash(stable(value));
      const rows=await store.rpc('reserve_capture',{p_workspace:workspace,p_capture:{...value,request_hash}});
      for(const file of value.files)await store.rpc('register_resource',{p_workspace:workspace,p_resource:manifest({...file,id:file.resource_id},50_000_000)});
      return json(publicCapture(rows[0]),201);
    }
    if(path==='/captures'&&req.method==='GET') {const {limit,offset}=paging(url);return json({items:(await store.db('eric_app_captures?workspace_id=eq.'+workspace+'&order=created_at.desc,id.desc&offset='+offset+'&limit='+limit)).map(publicCapture),offset,limit});}
    let match;
    if((match=/^\/captures\/([a-f0-9-]+)(\/commit)?$/.exec(path))) {
      const id=uuid(match[1]);
      if(!match[2]&&req.method==='GET') {const rows=await store.db('eric_app_captures?workspace_id=eq.'+workspace+'&id=eq.'+id+'&limit=1');if(!rows[0])throw new AppFault(404,'capture_not_found','没有找到这次收集。');return json(publicCapture(rows[0]));}
      if(match[2]&&req.method==='POST')return json(publicCapture((await store.rpc('commit_capture',{p_workspace:workspace,p_id:id}))[0]));
    }
    if((match=/^\/files\/([A-Za-z0-9_.:%-]+)\/(ticket|parts|parts\/\d+|finish)$/.exec(path))) {
      const id=identifier(decodeURIComponent(match[1]));
      if(match[2]==='ticket'&&req.method==='POST') {
        let resolved=id;const page=/^attachment-page:([A-Za-z0-9_.:-]+):(\d+):image$/.exec(id);
        if(page){const documents=await store.rows(workspace,'document','&payload->>attachment_id=eq.'+encodeURIComponent(page[1]));if(documents.length!==1||Number(page[2])<1||Number(page[2])>documents[0].payload.page_count)throw new AppFault(404,'page_not_ready','本页预览尚未准备，可以先打开完整原件。');resolved='page:'+documents[0].id+':'+Number(page[2])+':image';}
        await readableResource(store,workspace,resolved);const token=await ticket(store,principal,resolved);return json({url:store.base+'/functions/v1/eric-app-api/files/'+encodeURIComponent(resolved)+'/content?ticket='+token,expires_in:600});
      }
      const resource=(await store.db('eric_app_resources?workspace_id=eq.'+workspace+'&id=eq.'+encodeURIComponent(id)+'&limit=1'))[0];
      if(!resource)throw new AppFault(404,'file_not_found','尚未登记这份文件。');
      // Browser writes are limited to files belonging to an unfinished capture from this workspace.
      const captureId=/^capture:([a-f0-9-]{36}):[a-f0-9]{64}$/.exec(id)?.[1];
      const captures=captureId?await store.db('eric_app_captures?workspace_id=eq.'+workspace+'&id=eq.'+captureId+'&source=eq.pwa&limit=1'):[];
      if(!captures[0]||captures[0].device_id!==principal.device.id||!captures[0].files.some((f: any)=>f.resource_id===id))throw new AppFault(403,'file_not_writable','这份文件不能从此设备修改。');
      if(match[2]==='parts'&&req.method==='GET')return json({complete:resource.complete,items:resource.complete?[]:await store.db('eric_app_resource_parts?workspace_id=eq.'+workspace+'&resource_id=eq.'+encodeURIComponent(id)+'&select=part_index,sha256,bytes&order=part_index.asc&limit=128')});
      if(match[2].startsWith('parts/')&&req.method==='PUT') {
        if(!resource.complete)await store.putPart(workspace,id,integer(Number(match[2].split('/')[1]),0,127),await boundedBytes(req,PART_BYTES));
        return json({saved:true});
      }
      if(match[2]==='finish'&&req.method==='POST') {
        if(!resource.complete)await verifyResource(store,workspace,resource);
        return json((await store.rpc('finish_resource',{p_workspace:workspace,p_id:id}))[0]);
      }
    }
    if(path==='/commands'&&req.method==='POST') {
      const value=await commandInput(store,workspace,principal.device.id,await boundedJson(req)),request_hash=await hash(stable(value));
      return json(commandDto((await store.rpc('enqueue_command',{p_workspace:workspace,p_command:{...value,request_hash}}))[0]),202);
    }
    if((match=/^\/commands\/([a-f0-9-]+)$/.exec(path))&&req.method==='GET') {const rows=await store.db('eric_app_commands?workspace_id=eq.'+workspace+'&id=eq.'+uuid(match[1])+'&limit=1');if(!rows[0])throw new AppFault(404,'not_found','没有找到这次修改。');return json(commandDto(rows[0]));}
    if((match=/^\/progress\/(review|inbox|task)\/([A-Za-z0-9_.:%-]+)$/.exec(path))&&req.method==='PUT') {
      const body=await boundedJson(req,2000),id=identifier(decodeURIComponent(match[2])),kind=match[1]+'_progress';
      const data={kind,id,value:body.value,expected_revision:integer(body.expected_revision),request_id:uuid(body.request_id)};
      if(match[1]==='task') {
        if(!data.value||Object.keys(data.value).length!==2||typeof data.value.completed!=='boolean')throw new AppFault(400,'invalid_progress','请重新核对这项任务。');
        return json(dto(await store.rpc('task_progress',{p_workspace:workspace,p_device:principal.device.id,p_id:id,p_expected:data.expected_revision,
          p_source:integer(data.value.source_revision,1),p_request:data.request_id,p_hash:await hash(stable(data)),p_completed:data.value.completed})));
      }
      return json(dto(await store.rpc('set_progress',{p_workspace:workspace,p_device:principal.device.id,p_kind:kind,p_id:id,p_expected:data.expected_revision,p_request:data.request_id,p_hash:await hash(stable(data)),p_value:data.value})));
    }
    if(req.method==='GET')return json(await views.read(path,url));
    throw new AppFault(404,'not_found','这个操作暂时不可用，请返回当前资料重试。');
  }

  async function workerRoute(req: Request,url: URL,path: string): Promise<Response> {
    if(path.startsWith('/worker/vision/')) return visionRoute(store,req,url,path,options.visionSecret||'',json);
    const principal=await store.worker(req),workspace=principal.workspace.id;
    if(path==='/worker/official-health'&&req.method==='GET') {
      if(!options.officialHealth)throw new AppFault(503,'source_health_not_configured','官网健康状态暂时无法读取。');
      return json(await options.officialHealth(workspace));
    }
    if(path==='/worker/hello'&&req.method==='POST') {const body=await boundedJson(req,2000);await store.rpc('begin_publish',{p_workspace:workspace,p_epoch:uuid(body.epoch)});await store.db('eric_app_processors?id=eq.'+principal.device.id,'PATCH',{last_seen_at:store.clock().toISOString()});return json({workspace_id:workspace,part_bytes:PART_BYTES,record_catalogue:true});}
    if(path==='/worker/resources'&&req.method==='GET') {const {offset}=paging(url);return json({items:await store.db('eric_app_resources?workspace_id=eq.'+workspace+'&complete=eq.true&select=id,sha256,bytes&order=id.asc&limit=500&offset='+offset),offset,limit:500});}
    if(path==='/worker/records'&&req.method==='GET') {const {offset}=paging(url);return json({items:await store.db('eric_app_records?workspace_id=eq.'+workspace+'&deleted=eq.false&source_epoch=not.is.null&select=kind,id,source_hash&order=kind.asc,id.asc&limit=500&offset='+offset),offset,limit:500});}
    if(path==='/worker/task-progress'&&req.method==='GET')return json({items:await store.db('eric_app_records?workspace_id=eq.'+workspace+'&kind=eq.task_progress&deleted=eq.false&payload->>application_state=eq.pending&order=revision.asc&limit=100')});
    if(path==='/worker/task-progress/ack'&&req.method==='POST') {const body=await boundedJson(req,2000);return json({applied:await store.rpc('ack_task',{p_workspace:workspace,p_id:identifier(body.id),p_revision:integer(body.revision),p_state:body.state,p_message:text(body.message||'',500),p_epoch:uuid(body.epoch),p_generation:integer(body.generation)})});}
    if(path==='/worker/heartbeat'&&req.method==='POST') {const body=await boundedJson(req,1000);await store.rpc('heartbeat',{p_workspace:workspace,p_epoch:uuid(body.epoch),p_phase:body.phase,p_published:body.published===true});return json({connected:true});}
    if(path==='/worker/receipts'&&req.method==='POST') {const body=await boundedJson(req);return json({linked:await store.rpc('link_receipts',{p_workspace:workspace,p_items:body.items})});}
    if(path==='/worker/publish'&&req.method==='POST') {const body=await boundedJson(req,8_000_000);return json({revision:await store.rpc('publish_records',{p_workspace:workspace,p_records:body.records,p_epoch:uuid(body.epoch),p_generation:integer(body.generation)})});}
    if(path==='/worker/capture-results'&&req.method==='POST') {const body=await boundedJson(req);return json({updated:await store.rpc('update_capture_results',{p_workspace:workspace,p_updates:body.items,p_epoch:uuid(body.epoch),p_generation:integer(body.generation)})});}
    if(path==='/worker/claim'&&req.method==='POST') {const body=await boundedJson(req,1000);return json({items:await store.rpc('claim_work',{p_workspace:workspace,p_kind:body.kind,p_limit:integer(body.limit||3,1,5)})});}
    if(path==='/worker/finish'&&req.method==='POST') {const body=await boundedJson(req,8_000_000);return json(await store.rpc('finish_work',{p_workspace:workspace,p_kind:body.kind,p_id:uuid(body.id),p_token:uuid(body.token),p_state:body.state,p_result:body.result,p_records:body.records||[],p_epoch:uuid(body.epoch),p_generation:integer(body.generation)}));}
    if(path==='/worker/renew'&&req.method==='POST') {const body=await boundedJson(req,1000);return json({renewed:await store.rpc('renew_work',{p_workspace:workspace,p_kind:body.kind,p_id:uuid(body.id),p_token:uuid(body.token)})});}
    if(path==='/worker/resources'&&req.method==='POST') {
      const body=await boundedJson(req,2000),value=manifest(body,512_000_000,true),resource=(await store.rpc('register_resource',{p_workspace:workspace,p_resource:value}))[0];
      const parts=resource.complete?[]:await store.db('eric_app_resource_parts?workspace_id=eq.'+workspace+'&resource_id=eq.'+encodeURIComponent(value.id)+'&order=part_index.asc&limit=128&select=part_index,sha256,bytes');
      return json({...resource,parts});
    }
    const match=/^\/worker\/files\/([A-Za-z0-9_.:%-]+)\/(parts\/\d+|finish|manifest|content)$/.exec(path);
    if(match) {
      const id=identifier(decodeURIComponent(match[1]));
      if(match[2]==='content'&&req.method==='GET')return fileResponse(store,req,workspace,id);
      if(match[2]==='manifest'&&req.method==='GET')return json((await readableResource(store,workspace,id)).resource);
      if(match[2].startsWith('parts/')&&req.method==='PUT') {await store.putPart(workspace,id,integer(Number(match[2].split('/')[1]),0,127),await boundedBytes(req,PART_BYTES));return json({saved:true});}
      // The trusted Windows publisher verifies the whole original and each ordered part locally.
      if(match[2]==='finish'&&req.method==='POST')return json((await store.rpc('finish_resource',{p_workspace:workspace,p_id:id}))[0]);
    }
    throw new AppFault(404,'not_found','此同步操作不存在。');
  }

  return async function handler(req: Request): Promise<Response> {
    const origin=req.headers.get('Origin'),allowed=origin&&origins.has(origin);
    let response: Response;
    try {
      if(origin&&!allowed)throw new AppFault(403,'origin_not_allowed','请使用正式 Eric OS 地址访问。');
      const url=new URL(req.url),path=url.pathname.replace(/^.*\/eric-app-api/,'')||'/';
      response=req.method==='OPTIONS'?new Response(null,{status:204}):await route(req,url,path);
    } catch(error) {
      const fault=error instanceof AppFault?error:new AppFault(503,'temporarily_unavailable','暂时无法确认结果，已保存的内容会保留，请稍后重试。');
      response=json({detail:{code:fault.code,message:fault.publicMessage}},fault.status);
    }
    if(allowed) {
      response.headers.set('Access-Control-Allow-Origin',origin!);response.headers.set('Vary','Origin');
      response.headers.set('Access-Control-Allow-Methods','GET,HEAD,POST,PUT,OPTIONS');
      response.headers.set('Access-Control-Allow-Headers','authorization,apikey,content-type,x-client-info,idempotency-key,range');
      response.headers.set('Access-Control-Expose-Headers','Content-Range,Accept-Ranges,Content-Length');response.headers.set('Access-Control-Max-Age','600');
    }
    return response;
  };
}
