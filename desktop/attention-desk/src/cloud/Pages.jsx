import {lazy,Suspense,useContext,useEffect,useRef,useState} from 'react';
import {ArrowRight,BookOpen,Check,Clock,FileText,Plus} from 'lucide-react';
import usePersonalResource from '../personal/usePersonalResource';
import {personalRequest} from '../personal/api';
import {Field,SubmitButton} from '../personal/Forms';
import {ConfirmDialog,OriginalNotice} from '../personal/Evidence';
import DeskSheet from '../personal/DeskSheet';
import {FileList,SourceImage,SourceLink} from '../personal/DocumentSources';
import CapturePage from './CapturePage';
import {local,subscribe,currentIdentity,request,progress,saveCommand,captureLocalFile} from './client';
import {Connectivity} from './Connectivity';
import TodayPage from './TodayPage';
import TaskRow from '../personal/TaskRow';
import CourseCard, {CourseArtwork} from '../personal/CourseCard';
import {courseDetails} from '../personal/coursePresentation';
const ReviewView=lazy(()=>import('../personal/SchoolWorkspace').then(module=>({default:module.ReviewView})));
const LectureDetail=lazy(()=>import('../personal/SchoolWorkspace').then(module=>({default:module.LectureDetail})));
const SourcePreview=lazy(()=>import('../personal/SchoolWorkspace').then(module=>({default:module.SourcePreview})));
const Learning=lazy(()=>import('../personal/LearningWorkspace'));
const Inbox=lazy(()=>import('../personal/EricInbox'));
const ReviewEditor=lazy(()=>import('../personal/ReviewEditor'));
const Textbooks=lazy(()=>import('./TextbooksPage'));
const LocalPortfolioNotice=lazy(()=>import('./LocalPortfolioNotice'));
const time=value=>value?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value)):'';
const date=value=>value?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'medium',timeStyle:'short'}).format(new Date(value)):'';
const STATES={local_pending:'待发送',uploading:'发送中',received:'已接收',processing:'整理中',completed:'已完成',failed:'处理未完成',needs_confirmation:'需要核对',pending:'等待处理',needs_review:'待核对',accepted:'已采用',rejected:'已拒绝',superseded:'历史版本'};
function Status({value}){return <span className={'personal-status '+(value==='completed'?'done':['failed','needs_confirmation'].includes(value)?'needs_review':'')}>{STATES[value]||'已保存'}</span>;}
function ReadState({resource}){const online=useContext(Connectivity),waiting=!online&&resource.error&&(resource.error.code==='connection_error'||resource.error.status>=500);return <>{resource.loading&&!resource.data?<p className="personal-meta" role="status">正在读取…</p>:null}{waiting?<p className="personal-meta" role="status">联网后会自动读取云端记录。</p>:resource.error?<p className="personal-field-error" role="alert">{resource.error.message} <button type="button" className="personal-text-button" onClick={resource.retry}>重新读取</button></p>:null}{resource.data?._offline?<p className="cloud-offline-note" role="status">显示此设备保存的内容 · {date(resource.data._offline.savedAt)}</p>:null}</>;}
function Empty({children}){return <p className="personal-meta cloud-empty">{children}</p>;}
function Back({onClick,children}){return <button type="button" className="personal-text-button cloud-back" onClick={onClick}>返回{children}</button>;}
function useLocal(){const [items,setItems]=useState({captures:[],commands:[]});useEffect(()=>{let active=true;const read=async()=>{const owner=currentIdentity()?.owner;if(!owner)return;const [captures,commands]=await Promise.all([local.outbox.where('owner').equals(owner).toArray(),local.commands.where('owner').equals(owner).toArray()]);if(active)setItems({captures,commands});};void read();const off=subscribe(()=>void read());return()=>{active=false;off();};},[]);return items;}
function TaskCheck({task,commands,onChange,onConfirm,onOpen,children,disabled=false}){
  const pending=commands.find(item=>item.resourceKey==='task_progress:'+task.id&&item.state==='local_pending');
  const conflict=task.progress?.application_state==='needs_confirmation';
  return <div><TaskRow task={task} checked={pending?pending.body.value.completed:task.status==='done'} disabled={disabled||Boolean(pending)||!task._cloud?.revision} onChange={()=>onChange(task)} onOpen={onOpen}>{children||task.title}{pending?<small role="status">已保存在此设备，正在同步</small>:conflict?<small role="status">{task.progress.message}</small>:null}</TaskRow>{conflict&&onConfirm?<button type="button" className="personal-text-button" disabled={disabled||Boolean(pending)} onClick={()=>onConfirm(task)}>{task.progress.completed?'核对后确认标为完成':'核对后确认恢复未完成'}</button>:null}</div>;
}
async function setTask(task,completed){return progress('task',task.id,task.progress?._cloud?.revision||0,{completed,source_revision:task._cloud.revision});}

export default function Pages(props){const {route,navigate}=props;let page;
  if(route.view==='today')page=<Today {...props}/>;
  else if(route.view==='capture')page=<CapturePage {...props}/>;
  else if(['courses','course'].includes(route.view))page=<Courses {...props}/>;
  else if(route.view==='lecture')page=<Lecture {...props}/>;
  else if(route.view==='reviews')page=<Reviews {...props}/>;
  else if(route.view==='review')page=<Review {...props}/>;
  else if(route.view==='textbooks')page=<Textbooks {...props}/>;
  else if(route.view==='portfolio')page=<LocalPortfolioNotice/>;
  else if(route.view==='recent')page=<Recent {...props}/>;
  else if(route.view==='inbox')page=<><CaptureReceipts {...props} compact/><Inbox key={route.id||'list'} cloud onAdd={()=>navigate('capture')} onNavigate={(view,id)=>navigate(view==='review'?'raw-review':view,id)} onDirtyChange={props.onDirtyChange} onBusyChange={()=>{}}/></>;
  else if(route.view==='learning')page=<Learning cloud captureId={route.id} onNavigate={(view,id)=>navigate(view==='review'?'raw-review':view,id)} onDirtyChange={props.onDirtyChange} onBusyChange={()=>{}}/>;
  else if(route.view==='tasks')page=<Tasks {...props}/>;
  else if(route.view==='event')page=<EventDetail {...props}/>;
  else if(route.view==='raw-review')page=<RawReview {...props}/>;
  else if(route.view==='system')page=<System {...props}/>;
  else page=<section className="personal-section"><ul className="cloud-link-list">{[['courses','课程与课堂'],['reviews','Review'],['recent','最近收集与学习'],['learning','学习工作台'],['tasks','所有事项'],['textbooks','教材'],['portfolio','工程作品集'],['system','设备与状态']].map(([view,label])=><li key={view}><button type="button" onClick={()=>navigate(view)}>{label}<ArrowRight size={17}/></button></li>)}</ul></section>;
  return <Suspense fallback={<p role="status">正在打开…</p>}>{page}</Suspense>;
}

function Today({navigate,onNotice}){
  const resource=usePersonalResource('/dashboard',{refreshInterval:30_000});
  const courses=usePersonalResource('/courses?limit=100');
  const localItems=useLocal(),[error,setError]=useState('');
  const complete=async(task,completed=task.status!=='done')=>{setError('');try{await setTask(task,completed);onNotice('完成状态已保存，会自动同步。');}catch(value){setError(value.message);}};
  return <><ReadState resource={resource}/><TodayPage data={resource.data} resource={resource} courses={courses} navigate={navigate} localItems={localItems} TaskCheck={TaskCheck} onComplete={complete} taskError={error} ReadState={ReadState} receipts={<CaptureReceipts navigate={navigate} compact/>}/></>;
}

function Courses({route,navigate,onNotice}){
  const list=usePersonalResource('/courses?limit=100'),selected=route.view==='course'?route.id:null;
  const course=usePersonalResource(selected?'/courses/'+encodeURIComponent(selected):'/courses?limit=1');
  const [page,setPage]=useState(0),[creating,setCreating]=useState(false),[name,setName]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const lectures=usePersonalResource('/school?limit=20&offset='+(page*20)+(selected?'&course_id='+encodeURIComponent(selected):''));
  const add=async event=>{event.preventDefault();if(busy||!name.trim())return;setBusy(true);setError('');try{await saveCommand('/courses',{name:name.trim()},{target:'course:new',expectedRevision:0});setCreating(false);setName('');onNotice('课程已提交，处理完成后会出现在课程列表。');}catch(value){setError(value.message);}finally{setBusy(false);}};
  return <>{selected?<><Back onClick={()=>navigate('courses')}>课程</Back><ReadState resource={course}/>{course.data?<section className="personal-section course-detail-header"><CourseArtwork course={course.data}/><div><h2>{course.data.name}</h2><p className="personal-meta">{courseDetails(course.data)}</p></div><div className="personal-form-actions"><button type="button" className="button button-quiet" onClick={()=>navigate('capture',selected,{capture:{purpose:'lecture_note'}})}>上传课堂笔记</button><button type="button" className="button button-quiet" onClick={()=>navigate('textbooks',selected)}>教材与来源页</button></div></section>:null}<section className="personal-section"><h2>Lecture</h2><ReadState resource={lectures}/>{lectures.data?<>{lectures.data.items.length?<ul className="cloud-link-list">{lectures.data.items.map(item=><li key={item.id}><button type="button" onClick={()=>navigate('lecture',item.id)}><span><strong>{item.title}</strong><small>{item.lecture_date}</small></span><Status value={item.status}/></button></li>)}</ul>:<Empty>这门课还没有课堂记录。可以先上传笔记。</Empty>}<Pagination page={page} total={lectures.data.total} busy={lectures.loading} onPage={setPage}/></>:null}</section></>:<section className="personal-section"><div className="personal-section-heading"><h2>我的课程</h2><button type="button" className="personal-text-button" onClick={()=>setCreating(!creating)}><Plus size={16}/>添加课程</button></div><ReadState resource={list}/>{list.data?.items.length?<div className="today-course-grid course-catalog">{list.data.items.map(item=><CourseCard key={item.id} course={item} detail={courseDetails(item)} onOpen={()=>{setPage(0);navigate('course',item.id);}}/>)}</div>:list.data?<Empty>目前还没有课程，可以先添加课程或保存笔记。</Empty>:null}{creating?<form className="personal-form" noValidate onSubmit={add}><Field name="course_name" label="课程名称"><input value={name} maxLength={120} disabled={busy} onChange={e=>setName(e.target.value)}/></Field>{error?<p role="alert" className="personal-field-error">{error}</p>:null}<SubmitButton busy={busy}>保存课程</SubmitButton></form>:null}</section>}</>;
}

function Lecture({route,navigate,onNotice,onDirtyChange}){
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[source,setSource]=useState(null),[dialog,setDialog]=useState(null);
  const run=async(work,message)=>{if(busy)return null;setBusy(true);setError('');try{const result=await work();onNotice(result?.queued?'修改已保存，电脑上线后会自动接续处理。':message||'已保存。');return result;}catch(value){setError(value.message);return null;}finally{setBusy(false);}};
  const mutate=(path,body,method='POST',options={})=>personalRequest('/school'+path,{...options,method,body});
  return <div className="school-workspace">{error?<p className="personal-field-error" role="alert">{error}</p>:null}<LectureDetail key={route.id} cloud id={route.id} busy={busy} run={run} mutate={mutate} onBack={course=>navigate(course?'course':'courses',course||null)} onSource={setSource} onDialog={setDialog} onBook={course=>navigate('textbooks',course)} onNavigate={(view,id)=>navigate(view==='review'?'raw-review':view,id)} onDirtyChange={onDirtyChange}/>{source?<SourcePreview source={source} onClose={()=>setSource(null)}/>:null}{dialog?<ConfirmDialog {...dialog} busy={busy} onCancel={()=>setDialog(null)} onConfirm={async()=>{if(await dialog.action())setDialog(null);}}/>:null}</div>;
}
function Reviews({navigate}){
  const [page,setPage]=useState(0),r=usePersonalResource('/reviews?limit=20&offset='+(page*20));
  return <section className="personal-section"><h2>课堂复盘</h2><p className="personal-meta">每节课显示当前可读复盘，历史版本可在课堂中查看。</p><ReadState resource={r}/>{r.data?<>{r.data.items.length?<ul className="cloud-link-list">{r.data.items.map(item=><li key={item.id}><button type="button" onClick={()=>navigate('review',item.id)}><span><strong>{item.title}</strong><small>{item.lecture_date} · 第 {item.version} 版 · {item.has_content?(item.reading.completed?'已读':'待阅读'):(item.status==='processing'?'正在整理':'尚未完成')}{item.newer_than_adopted?' · 新版待核对':''}{item.newer_unfinished_version?` · 第 ${item.newer_unfinished_version} 版尚未完成`:''}</small></span><ArrowRight size={17}/></button></li>)}</ul>:<Empty>上传课堂笔记后，复盘会从这里继续。</Empty>}<Pagination page={page} total={r.data.total} busy={r.loading} onPage={setPage}/></>:null}</section>;
}

function Review({route,navigate,onDirtyChange,onNotice}){
  const r=usePersonalResource('/school/reviews/'+encodeURIComponent(route.id)),review=r.data;
  const lecture=usePersonalResource(review?'/school/lectures/'+encodeURIComponent(review.lecture_id):'/school?limit=1');
  const [source,setSource]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const localItems=useLocal(),waiting=localItems.commands.some(item=>item.resourceKey==='review_progress:'+route.id&&item.state==='local_pending');
  const markRead=async()=>{if(!review||waiting)return;setError('');try{await progress('review',review.id,review.reading._cloud.revision,{completed:!review.reading.completed});onNotice('阅读进度已保存，会自动同步到其他设备。');}catch(value){setError(value.message);}};
  const run=async action=>{if(busy)return;setBusy(true);setError('');try{const value=await action();onNotice('修改已保存，正在接续处理。');return value;}catch(value){setError(value.message);return null;}finally{setBusy(false);}};
  return <><Back onClick={()=>navigate('reviews')}>Review</Back><ReadState resource={r}/>{review&&lecture.data?.id?<section className="personal-section cloud-reading"><div className="personal-section-heading"><h2>{lecture.data.title}</h2>{review.content?<button type="button" className="button button-quiet" disabled={waiting} onClick={()=>void markRead()}>{waiting?'正在保存阅读进度…':review.reading.completed?'标为未读':'标记已读'}</button>:null}</div>{error?<p role="alert" className="personal-field-error">{error}</p>:null}<ReviewView cloud artifactId={review.id} lecture={lecture.data} busy={busy} run={run} mutate={(path,body,method='POST',options={})=>personalRequest('/school'+path,{...options,method,body})} onSource={setSource} onSaved={()=>{r.retry();lecture.retry();}} onCorrected={id=>navigate('review',id)} onNavigate={(view,id)=>navigate(view==='review'?'raw-review':view,id)} onDirtyChange={onDirtyChange}/><button type="button" className="personal-text-button" onClick={()=>navigate('lecture',review.lecture_id)}>返回这节课的笔记与教材</button></section>:<ReadState resource={lecture}/>}<SourceSheet source={source} onClose={()=>setSource(null)}/></>;
}

function SourceSheet({source,onClose}){return source?<DeskSheet title="来源原页" onClose={onClose} wide><p><strong>{source.caption||source.filename||'原始材料'}</strong></p>{source.text?<blockquote className="personal-quote">{source.text}</blockquote>:null}{source.uncertainties?.length?<p className="personal-field-error">{source.uncertainties.join('；')}</p>:null}<SourceImage source={source}/>{source.original_url?<p><SourceLink className="personal-text-button" href={source.original_url} target="_blank" rel="noopener noreferrer">打开完整原件</SourceLink></p>:null}</DeskSheet>:null;}

function CaptureReceipts({navigate,compact=false}){
  const cloud=usePersonalResource('/captures?limit='+(compact?5:50),{refreshInterval:15000}),stored=useLocal();
  const items=new Map(stored.captures.map(item=>[item.id,item]));
  for(const value of cloud.data?.items||[]){const old=items.get(value.id),localProblem=old?.error&&!value.received_at;if(!old?.server||(value.revision||0)>=(old.server.revision||0))items.set(value.id,{...old,id:value.id,title:value.title,createdAt:value.created_at,state:localProblem?old.state:value.state,server:value,error:localProblem?old.error:value.error,files:old?.files||value.files});}
  const sorted=[...items.values()].sort((a,b)=>b.createdAt.localeCompare(a.createdAt)).slice(0,compact?5:50);
  return <section className="personal-section"><div className="personal-section-heading"><h2>最近收集</h2>{compact?<button type="button" className="personal-text-button" onClick={()=>navigate('recent')}>查看全部</button>:null}</div><ReadState resource={cloud}/>{sorted.length?<ul className="cloud-link-list">{sorted.map(item=>{
    const needsContext=item.state==='needs_confirmation'&&item.server?.learning_capture_id;
    return <li key={item.id}><div className="cloud-receipt"><strong>{item.title}</strong><small>{date(item.createdAt)}{item.server?.received_at?' · 云端已接收':''}</small>
      {item.error?<p className="personal-field-error">{item.error.message}</p>:null}
      {item.error?.code==='previous_device'&&!item.server?.received_at?<LocalCaptureCopy item={item}/>:null}
      {needsContext?<button type="button" className="personal-text-button" onClick={()=>navigate('learning',item.server.learning_capture_id)}>{item.server.purpose==='lecture_note'?'继续整理课堂笔记':'核对学习记录'}</button>:null}
      {item.server?.lecture_id?<button type="button" className="personal-text-button" onClick={()=>navigate('lecture',item.server.lecture_id)}>查看课堂</button>:null}
      {item.server?.raw_item_id?<button type="button" className="personal-text-button" onClick={()=>navigate('inbox',item.server.raw_item_id)}>{needsContext?'查看原件':'查看内容与关联'}</button>:null}
      {item.server?.received_at&&item.server.files?.map(file=><p key={file.resource_id}><SourceLink className="personal-text-button" href={'/api/personal/cloud/files/'+encodeURIComponent(file.resource_id)} target="_blank" rel="noopener noreferrer">{file.filename}</SourceLink></p>)}
    </div><Status value={item.state}/></li>;
  })}</ul>:!cloud.loading?<Empty>你的 Capture 和课堂笔记会出现在这里。</Empty>:null}</section>;
}

function LocalCaptureCopy({item}){
  const [copies,setCopies]=useState({}),[busy,setBusy]=useState(null),[error,setError]=useState(''),urls=useRef([]);
  useEffect(()=>()=>urls.current.forEach(url=>URL.revokeObjectURL(url)),[]);
  const prepare=async index=>{setBusy(index);setError('');try{const file=await captureLocalFile(item.id,index),url=URL.createObjectURL(file.blob);urls.current.push(url);setCopies(value=>({...value,[index]:{url,filename:file.filename}}));}catch(value){setError(value.message);}finally{setBusy(null);}};
  return <details className="desk-disclosure"><summary>查看本机保留内容</summary><p className="personal-meta">此前登录留下的内容仍在此设备中。先核对最近收集；尚未接收的原件可以取回，之后重新保存。</p>{item.text?<Field name={'retained_text_'+item.id} label="保留的文字"><textarea className="resize-none" readOnly value={item.text} rows={4}/></Field>:null}{item.files.map((file,index)=><p key={file.fileId||index}>{copies[index]?<a className="personal-text-button" href={copies[index].url} download={copies[index].filename}>下载 {file.filename}</a>:<button type="button" className="personal-text-button" disabled={busy!==null} onClick={()=>void prepare(index)}>{busy===index?'正在读取原件…':'取回 '+file.filename}</button>}</p>)}{error?<p className="personal-field-error" role="alert">{error}</p>:null}</details>;
}

function Recent(props){const [page,setPage]=useState(0),r=usePersonalResource('/recent?limit=20&offset='+(page*20));return <><CaptureReceipts {...props}/><section className="personal-section"><h2>最近的学习内容</h2><ReadState resource={r}/>{r.data?<>{r.data.items.length?<ul className="cloud-link-list">{r.data.items.map(item=><li key={item._cloud.id}><button type="button" onClick={()=>props.navigate(({review:'review',lecture:'lecture',inbox:'inbox'})[item.kind],item.id)}><span><strong>{item.title}</strong><small>{date(item.occurred_at)}</small></span><ArrowRight size={17}/></button></li>)}</ul>:<Empty>已保存的学习内容会出现在这里。</Empty>}<Pagination page={page} total={r.data.total} busy={r.loading} onPage={setPage}/></>:null}</section></>;}

function Tasks({navigate}){const [page,setPage]=useState(0),r=usePersonalResource('/events?status=active&limit=20&offset='+(page*20));return <section className="personal-section"><h2>未完成事项</h2><ReadState resource={r}/>{r.data?<>{r.data.items.length?<ul className="cloud-link-list">{r.data.items.map(item=><li key={item.id}><button type="button" onClick={()=>navigate('event',item.id)}><span><strong>{item.title}</strong><small>{item.completed_task_count}/{item.task_count} 个任务完成</small></span><ArrowRight size={17}/></button></li>)}</ul>:<Empty>当前没有未完成事项。</Empty>}<Pagination page={page} total={r.data.total} busy={r.loading} onPage={setPage}/></>:null}</section>;}

function EventDetail({route,navigate,onNotice}){
  const r=usePersonalResource('/events/'+encodeURIComponent(route.id)),event=r.data,[error,setError]=useState(''),stored=useLocal();
  const waiting=stored.commands.some(item=>item.resourceKey==='event:'+route.id&&['local_pending','pending','processing'].includes(item.state));
  const toggleTask=async(task,completed=task.status!=='done')=>{try{setError('');await setTask(task,completed);onNotice('任务状态已保存，会自动同步。');}catch(value){setError(value.message);}};
  const change=async(path,body,method='POST')=>{try{setError('');await personalRequest(path,{method,body});onNotice('修改已保存，正在接续处理。');}catch(value){setError(value.message);}};
  return <><Back onClick={()=>navigate('tasks')}>事项</Back><ReadState resource={r}/>{event?<section className="personal-section cloud-reading"><h2>{event.title}</h2>{event.summary?<p>{event.summary}</p>:null}{error?<p role="alert" className="personal-field-error">{error}</p>:null}{waiting?<p role="status" className="personal-meta">修改已保存，正在接续处理。</p>:null}<ul className="cloud-task-list">{event.tasks.map(task=><li key={task.id}><TaskCheck task={task} commands={stored.commands} disabled={waiting} onChange={task=>void toggleTask(task)} onConfirm={task=>void toggleTask(task,task.progress.completed)}/></li>)}</ul>{event.deadlines.length?<><h3>时间</h3><ul>{event.deadlines.map(deadline=><li key={deadline.id}>{deadline.label||deadline.role||'截止时间'} · {deadline.value||'待确认'}</li>)}</ul></>:null}<div className="personal-form-actions"><button type="button" className="button button-quiet" disabled={waiting||event.status==='done'||event.tasks.some(task=>task.status!=='done'||task.progress?.application_state==='needs_confirmation')} onClick={()=>void change('/events/'+event.id+'/status',{expected_version:event.version,status:'done',confirm_incomplete:false})}><Check size={16}/>完成事项</button>{['done','cancelled'].includes(event.status)?<button type="button" className="button button-quiet" disabled={waiting} onClick={()=>void change('/events/'+event.id+'/restore',{expected_version:event.version})}>恢复事项</button>:null}<button type="button" className="personal-text-button" onClick={()=>navigate('inbox',event.raw_item_id)}>查看原始通知与依据</button></div></section>:null}</>;
}

function RawReview({route,navigate,onNotice,onDirtyChange}){
  const r=usePersonalResource('/raw/'+encodeURIComponent(route.id)),bundle=r.data,[busy,setBusy]=useState(false),[error,setError]=useState(''),[selected,setSelected]=useState(null);
  const save=async(path,body,method='POST')=>{if(busy)return;setBusy(true);setError('');try{await personalRequest(path,{method,body});onDirtyChange(false);navigate('inbox',null,{saved:true});onNotice('修改已保存，处理完成后会同步到其他设备。');}catch(value){setError(value.message);}finally{setBusy(false);}};
  return <><Back onClick={()=>navigate('inbox')}>Inbox</Back><ReadState resource={r}/>{bundle?<section className="personal-section cloud-reading"><h2>{bundle.title_hint||'核对这份内容'}</h2>{error?<p className="personal-field-error" role="alert">{error}</p>:null}{bundle.status==='confirmed'?<p>这份内容已确认，正式事项已经保留。</p>:<ReviewEditor bundle={bundle} busy={busy} onDirtyChange={onDirtyChange} onView={setSelected} onConfirm={payload=>void save('/raw/'+route.id+'/confirm',{...payload,expected_version:bundle.version})} onSaveDraft={draft=>void save('/raw/'+route.id+'/draft',{expected_version:bundle.version,draft},'PUT')} onCancel={()=>navigate('inbox')}/>}<details className="desk-disclosure"><summary>查看原始材料</summary><OriginalNotice snapshot={bundle.snapshot} selectedEvidence={selected}/><FileList snapshot={bundle.snapshot}/></details></section>:null}</>;
}

function System({account,onExit,onRevoked}){
  const processors=usePersonalResource('/processors');
  const status=usePersonalResource('/system',{refreshInterval:15000}),devices=usePersonalResource('/devices'),[dialog,setDialog]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const revoke=async()=>{if(busy)return;setBusy(true);setError('');try{await request(dialog.processor?'/processors/revoke':'/devices/revoke',{method:'POST',body:{id:dialog.id}});setDialog(null);devices.retry();processors.retry();if(!dialog.processor&&dialog.id===account.device.id)await onRevoked();}catch(value){setError(value.message);}finally{setBusy(false);}};
  return <><section className="personal-section"><h2>当前状态</h2><ReadState resource={status}/>{status.data?<dl className="cloud-facts"><div><dt>云端接收</dt><dd>可以接收 Capture 与文件</dd></div><div><dt>整理电脑</dt><dd>{status.data.processing_online?'最近已连接':'当前离线，已收到的内容会保留'}</dd></div><div><dt>资料最近同步</dt><dd>{date(status.data.last_processed_at)||'正在准备'}</dd></div></dl>:null}</section><section className="personal-section"><h2>你的设备</h2><p className="personal-meta">{account.email}</p><ReadState resource={devices}/>{error?<p className="personal-field-error" role="alert">{error}</p>:null}<ul className="cloud-link-list">{devices.data?.items.filter(device=>!device.revoked_at).map(device=><li key={device.id}><span><strong>{device.label}{device.id===account.device.id?' · 当前设备':''}</strong><small>最近连接：{date(device.last_seen_at)}</small></span><button type="button" className="personal-text-button" onClick={()=>setDialog(device)}>撤销访问</button></li>)}</ul><button type="button" className="personal-text-button" onClick={onExit}>退出当前设备</button></section><section className="personal-section"><h2>整理电脑</h2><p className="personal-meta">自动接续笔记识别与资料整理。云端接收与已同步资料可独立使用。</p><ReadState resource={processors}/><ul className="cloud-link-list">{processors.data?.items.filter(item=>!item.revoked_at).map(item=><li key={item.id}><span><strong>{item.label}</strong><small>最近连接：{date(item.last_seen_at)}</small></span><button type="button" className="personal-text-button" onClick={()=>setDialog({...item,processor:true})}>撤销这台电脑</button></li>)}</ul></section><section className="personal-section"><h2>安装到桌面或主屏幕</h2><p>Windows：在 Edge 或 Chrome 的菜单中选择安装 Eric OS。</p><p>iPhone / iPad：用 Safari 打开此地址，点“共享”，再选择“添加到主屏幕”。</p><p className="personal-meta">安装后仍使用同一个账号，已有 Shortcuts 可以继续分享课堂笔记。</p></section>{dialog?<ConfirmDialog title={'撤销 '+dialog.label+' 的访问？'} message={dialog.processor?"这台电脑将停止接续处理，需要重新建立连接才能继续。云端原件和其他设备仍可使用。":"此设备将需要重新登录。已经保存的资料和其他设备的访问会保留。"} confirmLabel="撤销此设备" busy={busy} onCancel={()=>setDialog(null)} onConfirm={()=>void revoke()}/>:null}</>;
}

function Pagination({page,total,busy,onPage}){if(total<=20)return null;return <nav className="personal-form-actions cloud-pagination" aria-label="分页"><button type="button" className="button button-quiet" disabled={!page||busy} onClick={()=>onPage(page-1)}>上一页</button><span>{page*20+1}–{Math.min(total,(page+1)*20)} / {total}</span><button type="button" className="button button-quiet" disabled={(page+1)*20>=total||busy} onClick={()=>onPage(page+1)}>下一页</button></nav>;}
