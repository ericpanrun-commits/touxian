import {useCallback,useEffect,useRef,useState,Suspense,lazy} from 'react';
import {Eye,EyeOff,LoaderCircle} from 'lucide-react';
import DeskChrome from '../personal/DeskChrome';
import {Field,SubmitButton} from '../personal/Forms';
import {setPersonalTransport,setPersonalFileResolver,setPersonalPdfResolver,setPersonalEditorDraftResolver} from '../personal/api';
import {ConfirmDialog} from '../personal/Evidence';
import {auth,restoreIdentity,currentIdentity,signOut,startSync,stopSync,subscribe,local,editorDraft} from './client';
import {cloudRequest,cloudFile,cloudPdfSource} from './transport';
import {guardHistory} from './navigation';
import {Connectivity} from './Connectivity';
import {getPendingUpdate,subscribeToWorkerUpdates,refreshToUpdate} from './register-worker';
const Pages=lazy(()=>import('./Pages'));
const PendingChanges=lazy(()=>import('./PendingChanges'));
setPersonalTransport(cloudRequest);setPersonalFileResolver(cloudFile);setPersonalPdfResolver(cloudPdfSource);
setPersonalEditorDraftResolver(editorDraft);

const names={today:'Today',capture:'Capture',inbox:'Inbox',courses:'课程',course:'课程',lecture:'课堂',reviews:'Review',review:'课堂复盘',recent:'最近',learning:'学习',tasks:'事项',event:'事项',textbooks:'教材',portfolio:'工程作品集',system:'设备与状态',more:'更多','raw-review':'待确认'};
function readRoute(){const query=new URLSearchParams(location.search),view=query.get('view')||query.get('personal_view')||(location.hash.startsWith('#learning-share=')?'learning':null);return {view:names[view]?view:'today',id:query.get('id')||query.get('inbox_item')||null,query:query.toString()};}

export default function CloudApp() {
  const [account,setAccount]=useState(null),[starting,setStarting]=useState(true),[error,setError]=useState(''),[route,setRoute]=useState(readRoute),[online,setOnline]=useState(navigator.onLine),[dirty,setDirty]=useState(false),[dialog,setDialog]=useState(null),[notice,setNotice]=useState(''),[queue,setQueue]=useState({captures:0,commands:0});
  const restoring=useRef(false),heading=useRef(null),dirtyRef=useRef(dirty);
  const [pendingUpdate,setPendingUpdate]=useState(getPendingUpdate);
  useEffect(()=>subscribeToWorkerUpdates(setPendingUpdate),[]);
  dirtyRef.current=dirty;
  const markDirty=useCallback(value=>{dirtyRef.current=value;setDirty(value);},[]);
  const restore=useCallback(async()=>{
    if(restoring.current)return;restoring.current=true;
    try {const identity=await restoreIdentity();setAccount(identity);setError('');if(identity)startSync();else stopSync();}
    catch(value){setError(value.message);if(value.status===401||value.status===403){setAccount(null);stopSync();}}
    finally {setStarting(false);restoring.current=false;}
  },[]);
  useEffect(()=>{
    void restore();
    const {data}=auth.auth.onAuthStateChange(()=>setTimeout(()=>void restore(),0));
    const connection=event=>{const connected=event.type==='offline'?false:navigator.onLine;setOnline(connected);if(connected)void restore();};
    const expired=event=>{stopSync();setAccount(null);setError(event.detail?.message||'请重新登录，已保存的内容会保留。');};
    window.addEventListener('online',connection);window.addEventListener('offline',connection);window.addEventListener('eric-auth-required',expired);
    return()=>{data.subscription.unsubscribe();stopSync();window.removeEventListener('online',connection);window.removeEventListener('offline',connection);window.removeEventListener('eric-auth-required',expired);};
  },[restore]);
  useEffect(()=>guardHistory({isDirty:()=>dirtyRef.current,onRoute:()=>setRoute(readRoute()),onConfirm:proceed=>setDialog({title:'离开当前编辑？',message:'当前修改尚未提交。Capture 草稿会留在此设备；其他未保存输入会放弃。',confirmLabel:'离开当前编辑',action:()=>{dirtyRef.current=false;setDirty(false);setDialog(null);proceed();}})}),[]);
  useEffect(()=>{
    if(!account)return;
    const update=async()=>{const [captures,commands]=await Promise.all([local.outbox.where('owner').equals(account.owner).filter(x=>['local_pending','uploading'].includes(x.state)).count(),local.commands.where('owner').equals(account.owner).filter(x=>['local_pending','pending','processing'].includes(x.state)).count()]);setQueue({captures,commands});};
    void update();return subscribe(()=>void update());
  },[account?.owner]);
  useEffect(()=>{document.title=names[route.view]+' · Eric OS';window.scrollTo({top:0,behavior:'instant'});requestAnimationFrame(()=>{heading.current?.focus({preventScroll:true});});},[route.view,route.id]);
  useEffect(()=>{const guard=event=>{if(dirty){event.preventDefault();event.returnValue='';}};window.addEventListener('beforeunload',guard);return()=>window.removeEventListener('beforeunload',guard);},[dirty]);
  const navigate=(view,id=null,options={})=>{
    const aliases={'eric-inbox':'inbox',school:'courses',list:'tasks',detail:'event','settings':'system'};
    view=aliases[view]||view;
    const move=()=>{const url=new URL(location.href);url.searchParams.set('view',names[view]?view:'today');url.searchParams.delete('id');url.searchParams.delete('inbox_item');if(id)url.searchParams.set(view==='inbox'?'inbox_item':'id',id);
      url.searchParams.delete('capture_purpose');url.searchParams.delete('capture_date');
      if(view==='capture'&&options.capture){
        if(['capture','lecture_note','textbook'].includes(options.capture.purpose))url.searchParams.set('capture_purpose',options.capture.purpose);
        if(/^\d{4}-\d\d-\d\d$/.test(options.capture.lectureDate||''))url.searchParams.set('capture_date',options.capture.lectureDate);
      }
      if(view==='inbox'&&options.inbox){
        if(['all','processing','needs_review','completed','archived'].includes(options.inbox.status))url.searchParams.set('inbox_status',options.inbox.status);
        if(['all','submitted'].includes(options.inbox.scope))url.searchParams.set('inbox_scope',options.inbox.scope);
        url.searchParams.delete('inbox_page');
      }
      history.pushState({},'',url);markDirty(false);setNotice('');setRoute(readRoute());setDialog(null);};
    if(dirty&&!options.saved)setDialog({title:'离开当前编辑？',message:'当前修改尚未提交。Capture 草稿会留在此设备；其他未保存输入会放弃。',confirmLabel:'离开当前编辑',action:move});else move();
  };
  const exit=()=>setDialog({title:'退出此设备？',message:queue.captures?'还有待发送的收集，原件将保留在此设备。请先完成发送，再退出可方便日后继续。':'其他设备仍保持登录，已经保存的资料会保留。',confirmLabel:'退出此设备',action:async()=>{try{await signOut();setAccount(null);setDialog(null);}catch(value){setDialog(null);setNotice(value.message||'尚未退出，请恢复连接后重试。');}}});
  const updateNotice = pendingUpdate ? (
    <div className="cloud-feedback" role="status" aria-live="polite">
      <span>{dirty ? '新版本已就绪，请先保存当前编辑。' : '新版本已就绪。'}</span>{' '}
      <button
        type="button"
        className="personal-text-button"
        disabled={dirty || Boolean(dialog)}
        onClick={() => {
          if (!dirtyRef.current && !dialog) refreshToUpdate();
        }}
      >刷新更新</button>
    </div>
  ) : null;
  if(starting)return <div className="desk-shell cloud-shell"><main className="cloud-auth"><p role="status"><LoaderCircle size={20} className="spin"/>正在打开 Eric OS…</p></main></div>;
  if(!account)return <div className="desk-shell cloud-shell"><Login error={error} onReady={restore} updateNotice={updateNotice}/></div>;
  return <div className="desk-shell cloud-shell" data-view={route.view}><div className="personal-workspace"><DeskChrome cloud mode={route.view} onNavigate={navigate}/><main key={account.owner+':'+account.device.id} className="personal-content" id="main-content">
    <a className="cloud-skip-link" href="#page-heading">跳到当前内容</a>
    <div className="cloud-connection" role="status">{!online?'当前离线，收集会保存在此设备。':queue.captures?`${queue.captures} 份收集正在自动发送。`:queue.commands?`${queue.commands} 项修改已保存，正在接续处理。`:null}</div>
    {updateNotice}
    {notice?<p className="cloud-feedback" role="status" aria-live="polite">{notice}</p>:null}
    <Suspense fallback={null}><PendingChanges navigate={navigate}/></Suspense>
    <h1 ref={heading} id="page-heading" className={`cloud-page-title${route.view==='today'?' sr-only':''}`} tabIndex={-1}>{names[route.view]}</h1>
    <Connectivity.Provider value={online}><Suspense fallback={<p className="personal-meta" role="status">正在打开…</p>}><Pages route={route} navigate={navigate} onDirtyChange={markDirty} onNotice={setNotice} account={account} onExit={exit} onRevoked={async()=>{await signOut({alreadyRevoked:true});setAccount(null);}}/></Suspense></Connectivity.Provider>
  </main>{dialog?<ConfirmDialog {...dialog} onCancel={()=>setDialog(null)} onConfirm={()=>void dialog.action()}/>:null}</div></div>;
}

function Login({error:externalError,onReady,updateNotice}) {
  const [email,setEmail]=useState(''),[password,setPassword]=useState(''),[show,setShow]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const pending=useRef(false),emailRef=useRef(null);
  const submit=async event=>{
    event.preventDefault();if(pending.current)return;
    if(!email.trim()||!password){setError('请输入邮箱和密码。');emailRef.current?.focus();return;}
    pending.current=true;setBusy(true);setError('');
    try {const {error}=await auth.auth.signInWithPassword({email:email.trim(),password});if(error)throw error;setPassword('');await onReady();}
    catch(value){window.dispatchEvent(new CustomEvent('eric-auth-error',{detail:{type:value.name,code:value.code||'unavailable'}}));setError(value.name==='AuthRetryableFetchError'||value instanceof TypeError?'暂时无法连接，请检查网络后重试。':value.code==='invalid_credentials'?'邮箱或密码不正确，请核对后重试。':'登录暂未完成，请稍后重试；已有资料会保留。');}
    finally{pending.current=false;setBusy(false);}
  };
  return <main className="cloud-auth"><section className="personal-section cloud-login-panel"><h1>Eric OS</h1><p className="personal-intro">课程、笔记与今天要做的事，随时接着用。</p>{updateNotice}<form className="personal-form" noValidate onSubmit={submit}>
    <fieldset disabled={busy}><Field name="owner_email" label="邮箱"><input ref={emailRef} type="email" autoComplete="username" inputMode="email" value={email} onChange={e=>setEmail(e.target.value)}/></Field>
    <div className="cloud-password"><Field name="owner_password" label="密码"><input type={show?'text':'password'} autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)}/></Field><button type="button" className="desk-icon-button" aria-label={show?'隐藏密码':'显示密码'} aria-pressed={show} onClick={()=>setShow(v=>!v)}>{show?<EyeOff size={18}/>:<Eye size={18}/>}</button></div></fieldset>
    {error||externalError?<p className="personal-field-error" role="alert">{error||externalError}</p>:null}<SubmitButton busy={busy}>登录 Eric OS</SubmitButton><p className="personal-meta">此设备将保持登录，你可以在“设备与状态”中单独撤销访问。</p>
  </form>{externalError?<button type="button" className="personal-text-button" onClick={()=>void signOut()}>退出当前账号，重新登录</button>:null}</section></main>;
}
