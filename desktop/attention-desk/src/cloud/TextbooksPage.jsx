import {lazy,useEffect,useRef,useState} from 'react';
import usePersonalResource from '../personal/usePersonalResource';
import {personalRequest} from '../personal/api';
import {Field,SubmitButton} from '../personal/Forms';
import {ConfirmDialog} from '../personal/Evidence';
import {preventImeSubmit} from '../personal/formHelpers';
import {BookMetadataForm} from '../personal/TextbookForms';
import {SourceImage,SourceLink} from '../personal/DocumentSources';
import {local,subscribe,currentIdentity} from './client';

const BookSummary=lazy(()=>import('../personal/SchoolWorkspace').then(module=>({default:module.BookSummary})));
const ReadStatus=lazy(()=>import('../personal/SchoolWorkspace').then(module=>({default:module.ReadStatus})));
const SectionForm=lazy(()=>import('../personal/SchoolWorkspace').then(module=>({default:module.SectionForm})));

function useBookCommands(){
  const [commands,setCommands]=useState([]);
  useEffect(()=>{
    let active=true;
    const read=async()=>{const owner=currentIdentity()?.owner;if(!owner)return;const rows=await local.commands.where('owner').equals(owner).filter(item=>['/school/textbooks','/school/textbook/file','/school/sections'].includes(item.path)||/^\/school\/(sections|documents)\/.+\/(index|catalog)$/.test(item.path)).toArray();if(active&&currentIdentity()?.owner===owner)setCommands(rows);};
    void read();const off=subscribe(()=>void read());return()=>{active=false;off();};
  },[]);
  return commands;
}

export default function TextbooksPage({route,navigate,onNotice,onDirtyChange}) {
  const courseId=route.id||'',courses=usePersonalResource('/courses?limit=100');
  const library=usePersonalResource('/school/textbooks?limit=100'+(courseId?'&course_id='+encodeURIComponent(courseId):''),{refreshInterval:5000});
  const [selection,setSelection]=useState(null),[creating,setCreating]=useState(false),[createCourse,setCreateCourse]=useState(courseId);
  const [metaDirty,setMetaDirty]=useState(false),[sectionDirty,setSectionDirty]=useState(false),[fileDirty,setFileDirty]=useState(false);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const [discardMeta,setDiscardMeta]=useState(false);
  const pending=useRef(false),observed=useRef(new Set()),commands=useBookCommands();
  const dirty=metaDirty||sectionDirty||fileDirty;
  const books=(library.data?.items||[]).filter(book=>!courseId||book.course_id===courseId),book=books.find(item=>item.id===selection)||books[0];
  const courseName=id=>courses.data?.items.find(item=>item.id===id)?.name||'这门课';
  useEffect(()=>{onDirtyChange(dirty);return()=>onDirtyChange(false);},[dirty,onDirtyChange]);
  useEffect(()=>{if(!metaDirty)setCreateCourse(courseId);},[courseId,metaDirty]);
  useEffect(()=>{let changed=false;for(const item of commands)if(item.state==='completed'&&!observed.current.has(item.id)){observed.current.add(item.id);changed=true;}if(changed)library.retry();},[commands,library.retry]);
  const run=async(work,message)=>{
    if(pending.current)return null;pending.current=true;setBusy(true);setError('');
    try{const result=await work();onNotice(result?.queued?'修改已保存在此设备，联网和电脑上线后会自动继续。':message||'已保存。');library.retry();return result;}
    catch(value){setError(value.message);return null;}finally{pending.current=false;setBusy(false);}
  };
  const waiting=commands.filter(item=>['local_pending','pending','processing','needs_confirmation'].includes(item.state)&&(!courseId||item.body?.course_id===courseId||books.some(book=>item.resourceKey==='textbook:'+book.id)));
  return <>
    <section className="personal-section"><div className="personal-section-heading"><h2>教材与原页</h2><button type="button" className="button button-quiet" disabled={busy||sectionDirty||fileDirty} onClick={()=>{if(creating&&metaDirty)setDiscardMeta(true);else setCreating(value=>!value);}}>{creating?'关闭登记':'登记教材'}</button></div>
      <ReadStatus resource={courses}/><ReadStatus resource={library}/>
      <Field name="textbook_course" label="课程"><select value={courseId} disabled={busy||dirty} onChange={e=>{setSelection(null);setError('');navigate('textbooks',e.target.value||null);}}><option value="">全部课程</option>{courses.data?.items.map(course=><option key={course.id} value={course.id}>{course.name}</option>)}</select></Field>
      {waiting.length?<div role="status"><ul className="cloud-link-list">{waiting.map(item=><li key={item.id}><div><strong>{item.body?.title||'教材资料'}</strong><p className={item.state==='needs_confirmation'?'personal-field-error':'personal-meta'}>{item.error?.message||item.server?.error?.message||({local_pending:'已保存到此设备，联网后自动发送。',pending:'已提交，电脑上线后自动登记。',processing:'正在整理教材资料。',needs_confirmation:'需要核对，原输入已保留。'})[item.state]}</p></div></li>)}</ul></div>:null}
      {error?<p role="alert" className="personal-field-error">{error} 原件和输入均保留。</p>:null}
      {creating?<><Field name="new_textbook_course" label="新教材所属课程"><select value={createCourse} disabled={busy} onChange={e=>setCreateCourse(e.target.value)}><option value="">请选择课程</option>{courses.data?.items.map(course=><option key={course.id} value={course.id}>{course.name}</option>)}</select></Field><BookMetadataForm courseId={createCourse} courseName={courseName(createCourse)} busy={busy} onDirtyChange={setMetaDirty} onSave={async(body,key)=>{const saved=await run(()=>personalRequest('/school/textbooks',{method:'POST',headers:{'Idempotency-Key':key},body}),'教材信息已保存。');if(saved){setCreating(false);setMetaDirty(false);if(saved.id&&!saved.queued)setSelection(saved.id);}return Boolean(saved);}}/></>:null}
      {books.length?<Field name="textbook_volume" label="教材"><select value={book.id} disabled={busy||dirty} onChange={e=>{setSelection(e.target.value);setError('');}}>{books.map(item=><option value={item.id} key={item.id}>{courseId?'':courseName(item.course_id)+' · '}{item.title}</option>)}</select></Field>:library.data&&!library.loading?<p className="personal-meta">{courseId?'这门课尚未登记教材。':'目前没有已登记教材。'}课堂笔记可以先整理，之后再补原件。</p>:null}
      {book?<><p className="personal-meta">课程：{courseName(book.course_id)}</p><BookSummary book={book} busy={busy}/><div className="personal-form-actions"><button type="button" className="button button-quiet" disabled={busy||dirty} onClick={()=>navigate('capture',book.course_id,{capture:{purpose:'textbook'}})}>上传教材 PDF</button>{book.document?<SourceLink className="personal-text-button" href={'/api/personal/attachments/'+book.document.attachment_id+'/original'} target="_blank" rel="noopener noreferrer">打开完整教材 PDF</SourceLink>:null}</div>
        <p className="personal-meta">上传会先安全接收；电脑上线后可从下方关联已上传的 PDF。每个文件不超过 50 MB，较大的教材可先上传本次章节。</p>
        <BookFileForm key={book.id} book={book} busy={busy} run={run} onDirtyChange={setFileDirty}/>
        {book.document&&book.document.status!=='ready'?<button type="button" className="personal-text-button" disabled={busy} onClick={()=>void run(()=>personalRequest('/school/documents/'+book.document_id+'/catalog',{method:'POST',body:{retry:true}}),'正在恢复页数登记。')}>恢复页数登记</button>:null}
        {book.document?.status==='ready'?<details className="desk-disclosure"><summary>章节与本地识别</summary><SectionForm key={book.id} sourceVersion={book.version} busy={busy} onDirtyChange={setSectionDirty} onSave={body=>run(()=>personalRequest('/school/sections',{method:'POST',body:{...body,textbook_id:book.id,learning_unit_id:null}}),'章节范围已保存。')}/>{book.sections.length?<ul className="school-section-list">{book.sections.map(section=><li key={section.id}><strong>{section.title}</strong><p>PDF 第 {section.start_page}–{section.end_page} 页</p><button type="button" className="button button-quiet" disabled={busy} onClick={()=>void run(()=>personalRequest('/school/sections/'+section.id+'/index',{method:'POST',body:{retry:true}}),'正在识别所选章节。')}>识别 / 继续这一节</button></li>)}</ul>:<p className="personal-meta">按原件登记相关小节，不按课次顺序猜测章节。</p>}</details>:null}
      </>:null}
    </section>
    {book?.document?.page_count?<TextbookReader key={book.id+':'+book.document_id} book={book}/>:null}
    {discardMeta?<ConfirmDialog title="放弃尚未保存的教材信息？" message="已登记教材和原件保留，当前输入还未提交。" confirmLabel="放弃输入并关闭" busy={busy} onCancel={()=>setDiscardMeta(false)} onConfirm={()=>{setCreating(false);setMetaDirty(false);setDiscardMeta(false);}}/>:null}
  </>;
}

function BookFileForm({book,busy,run,onDirtyChange}) {
  const [page,setPage]=useState(0),[choice,setChoice]=useState(null),[confirmed,setConfirmed]=useState(false),[role,setRole]=useState('full_textbook'),[version,setVersion]=useState(book.version),[error,setError]=useState('');
  const raw=usePersonalResource('/inbox?limit=20&offset='+(page*20)+'&status=all&scope=submitted');
  const options=(raw.data?.items||[]).filter(item=>item.input_kind==='pdf'&&(!item.course?.id||item.course.id===book.course_id));
  const dirty=Boolean(choice||confirmed),stale=version!==book.version;
  useEffect(()=>{onDirtyChange(dirty);return()=>onDirtyChange(false);},[dirty,onDirtyChange]);
  useEffect(()=>{if(!dirty)setVersion(book.version);},[book.version,dirty]);
  const submit=async event=>{
    event.preventDefault();setError('');if(!choice)return setError('请选择已经上传的教材 PDF。');if(!confirmed)return setError('请先核对所选原件与教材信息。');if(stale)return setError('教材已有更新，请先核对当前版本。');
    const saved=await run(()=>personalRequest('/school/textbook/file',{method:'PUT',body:{textbook_id:book.id,raw_item_id:choice.id,expected_version:version,file_role:role,edition_confirmed:true}}),'教材原件已关联。');
    if(saved){setChoice(null);setConfirmed(false);onDirtyChange(false);}
  };
  return <details className="desk-disclosure"><summary>{book.document_id?'关联其他已上传 PDF':'关联已上传 PDF'}</summary><form className="personal-form" noValidate onKeyDown={preventImeSubmit} onSubmit={submit}>
    <ReadStatus resource={raw}/><fieldset disabled={busy}><Field name="saved_textbook_pdf" label="已上传的 PDF"><select value={choice?.id||''} onChange={e=>{setChoice(options.find(item=>item.id===e.target.value)||null);setConfirmed(false);}}><option value="">选择原件</option>{choice&&!options.some(item=>item.id===choice.id)?<option value={choice.id}>{choice.title}</option>:null}{options.map(item=><option value={item.id} key={item.id}>{item.title}</option>)}</select></Field>
      <div className="personal-form-actions"><button type="button" className="button button-quiet" disabled={!page||raw.loading} onClick={()=>setPage(value=>value-1)}>较新的记录</button><button type="button" className="button button-quiet" disabled={!raw.data||(page+1)*20>=raw.data.total||raw.loading} onClick={()=>setPage(value=>value+1)}>更早的记录</button></div>
      <Field name="saved_textbook_role" label="文件内容"><select value={role} onChange={e=>setRole(e.target.value)}><option value="full_textbook">完整教材</option><option value="chapter_scan">章节扫描件</option></select></Field>
      <label className="personal-check"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>已核对所选原件与这门课的教材信息；不确定的版本不作确认。</label>
    </fieldset>{stale?<p className="personal-field-error">教材刚有更新，选择的原件仍保留。<button type="button" className="personal-text-button" disabled={busy} onClick={()=>{setVersion(book.version);setConfirmed(false);setError('');}}>核对当前教材后继续</button></p>:null}{error?<p role="alert" className="personal-field-error">{error}</p>:null}<SubmitButton busy={busy} disabled={stale}>确认关联教材原件</SubmitButton>
  </form></details>;
}

function TextbookReader({book}) {
  const [page,setPage]=useState('1'),[visible,setVisible]=useState(1),[error,setError]=useState('');
  return <section className="personal-section"><h2>{book.title} · 第 {visible} 页</h2><form className="personal-form cloud-page-picker" noValidate onKeyDown={preventImeSubmit} onSubmit={event=>{event.preventDefault();const value=Number(page);if(!Number.isSafeInteger(value)||value<1||value>book.document.page_count){setError('请输入 1–'+book.document.page_count+' 之间的 PDF 页码。');return;}setError('');setVisible(value);}}><Field name="physical_page" label="PDF 页码" error={error}><input inputMode="numeric" value={page} onChange={e=>setPage(e.target.value)}/></Field><button type="submit" className="button button-quiet">查看这一页</button></form><SourceImage source={{document_id:book.document.id,page:visible}}/></section>;
}
