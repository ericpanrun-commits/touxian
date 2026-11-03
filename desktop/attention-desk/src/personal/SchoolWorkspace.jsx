import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, BookOpen, Plus } from 'lucide-react';
import { personalRequest } from './api';
import usePersonalResource from './usePersonalResource';
import { Field, SubmitButton } from './Forms';
import { useDraft, preventImeSubmit, validTime } from './formHelpers';
import { ConfirmDialog } from './Evidence';
import { SourceImage, SourceLink } from './DocumentSources';
import DeskSheet from './DeskSheet';
import PreviewEvidence from './PreviewEvidence';
import SourceCorrectionEditor from './SourceCorrectionEditor';
import {BookMetadataForm} from './TextbookForms';
import { noteFormat, preferredLectureReview, reviewHasContent, reviewHasNoContent, reviewStateLabel, reviewIssueMessage } from './schoolReading';

const BASE = '/school';
const STATES = { needs_sources:'待补教材', needs_review:'待核对', ready:'可复盘', accepted:'已采用',
  waiting:'等待处理', running:'处理中', completed:'处理完成', failed:'未完成', partial:'部分完成',
  recovery_needed:'需明确恢复', processing:'生成中', superseded:'已采用更新版本', rejected:'已拒绝' };
const yuan = value => value == null ? '费用未知' : `估算 ¥${Number(value).toFixed(4)}`;
const readLecture = () => new URLSearchParams(location.search).get('school_lecture');
function availableSections(lecture) {
  const books=lecture?.textbook?[lecture.textbook,...(lecture.course_id==='course-math-ii'?[]:lecture.textbook.companions||[])]:[];
  return books.flatMap(book=>(book.sections||[]).map(section=>({...section,textbook_title:book.title}))).filter((section,index,all)=>all.findIndex(item=>item.id===section.id)===index);
}

export function ReadStatus({ resource }) {
  return <>{resource.loading && !resource.data ? <p role="status" className="personal-loading">正在读取已保存内容…</p> : null}
    {resource.error ? <div role="alert" className="personal-field-error">{resource.error.message} <button type="button" className="personal-text-button" onClick={resource.retry}>重新读取</button></div> : null}</>;
}

export default function SchoolWorkspace({ onNavigate, onDirtyChange, onBusyChange, initialRawId,initialCourseId }) {
  const [dirty,setDirty]=useState(false);
  const reportDirty=useCallback(value=>{setDirty(value);onDirtyChange(value);},[onDirtyChange]);
  const [lectureId, setLectureId] = useState(readLecture);
  const [courseId,setCourseId]=useState(()=>initialCourseId||new URLSearchParams(location.search).get('school_course')||'course-math-ii');
  const courses=usePersonalResource('/courses?limit=100');
  const [page, setPage] = useState(0);
  const [creating, setCreating] = useState(Boolean(initialRawId));
  const [bookOpen, setBookOpen] = useState(false);
  const [source, setSource] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const pending = useRef(false);
  const overview = usePersonalResource(`${BASE}?offset=${page*20}&limit=20&course_id=${encodeURIComponent(courseId)}`, { refreshInterval:10000, paused:creating || bookOpen });
  const openLecture = (id,selectedCourse=courseId) => {
    const u = new URL(location.href);
    u.searchParams.set('school_course',selectedCourse);setCourseId(selectedCourse);
    if (id) u.searchParams.set('school_lecture',id); else u.searchParams.delete('school_lecture');
    history.pushState({},'',u); setLectureId(id); setError(''); setNotice('');
    window.scrollTo({top:0,behavior:'instant'});
  };
  useEffect(() => { const back=()=>{setLectureId(readLecture());setCourseId(new URLSearchParams(location.search).get('school_course')||'course-math-ii');}; window.addEventListener('popstate',back); return ()=>window.removeEventListener('popstate',back); },[]);
  useEffect(() => { document.querySelector('.school-workspace h1')?.focus({preventScroll:true}); },[lectureId]);
  const run = async (work, success='已保存。') => {
    if (pending.current) return null;
    pending.current=true; setBusy(true); onBusyChange(true); setError(''); setNotice('');
    try { const result=await work(); setNotice(success); overview.retry(); return result; }
    catch (e) { setError(e.message); return null; }
    finally { pending.current=false; setBusy(false); onBusyChange(false); }
  };
  const mutate = (path,body,method='POST',options={}) => personalRequest(`${BASE}${path}`,{...options,method,body});
  const closeEditor = action => {
    if(dirty)setDialog({title:'放弃未保存的输入？',message:'已保存的原件和课堂记录保留，当前输入尚未保存。',confirmLabel:'放弃输入并关闭',action:()=>{action();reportDirty(false);return true;}});
    else action();
  };
  return <div className="school-workspace">
    <div className="personal-feedback school-feedback" role="status" aria-live="polite">{notice}</div>
    {error && !creating && !bookOpen && !dialog ? <p className="personal-field-error" role="alert">{error} 输入和原件均保留，可再次操作或重新读取。</p> : null}
    {lectureId ? <LectureDetail key={lectureId} id={lectureId} busy={busy} run={run} mutate={mutate} onBack={course=>closeEditor(()=>openLecture(null,course||courseId))} onSource={setSource} onDialog={setDialog} onBook={course=>{if(course)setCourseId(course);setBookOpen(true);}} onNavigate={onNavigate} onDirtyChange={reportDirty} /> : <>
      <section className="personal-section"><div className="personal-section-heading"><div><p className="personal-meta">{courses.data?.items.find(course=>course.id===courseId)?.name||'课堂记录'}</p><h1 tabIndex={-1}>课堂</h1></div><button type="button" className="button button-primary" disabled={busy} onClick={()=>setCreating(true)}><Plus size={16}/>关联课堂笔记</button></div>
        <p className="personal-intro">把一次课堂笔记与教材联系起来，保留每次复盘和来源。</p>
        <ReadStatus resource={courses}/><Field name="school_course" label="课程"><select value={courseId} disabled={busy||dirty||creating||bookOpen} onChange={e=>{setPage(0);openLecture(null,e.target.value);}}>{courses.data?.items.map(course=><option key={course.id} value={course.id}>{course.name}</option>)}</select></Field><ReadStatus resource={overview}/>
        {overview.data ? <><BookSummary book={overview.data.textbook} busy={busy} onOpen={()=>setBookOpen(true)}/>
          <h2>课堂记录</h2>{overview.data.lectures.length ? <ul className="personal-event-list">{overview.data.lectures.map(l=><li key={l.id}><button type="button" className="personal-event-row" disabled={busy} onClick={()=>openLecture(l.id)}><span><small>{l.lecture_date || '课堂日期未提供'}</small><strong>{l.title}</strong><small>{l.note_count} 份笔记 · {l.review_count} 个复盘版本</small></span><span className="personal-status needs_review">{STATES[l.status] || '待核对'}</span></button></li>)}</ul> : <div className="personal-empty"><BookOpen size={24}/><h3>从一次真实课堂开始</h3><p>先保存笔记 PDF / 图片并关联这门课，再建立真实课次。</p><button type="button" className="button button-quiet" onClick={()=>onNavigate('eric-inbox')}>打开 Inbox</button></div>}
          <div className="personal-form-actions"><button type="button" className="button button-quiet" disabled={!page || busy} onClick={()=>setPage(p=>p-1)}>上一页</button><span className="personal-meta">共 {overview.data.total} 次课堂</span><button type="button" className="button button-quiet" disabled={(page+1)*20>=overview.data.total || busy} onClick={()=>setPage(p=>p+1)}>下一页</button></div></> : null}
      </section></>}
    {creating ? <DeskSheet title="关联课堂笔记" onClose={()=>closeEditor(()=>setCreating(false))} busy={busy}>{error?<p role="alert" className="personal-field-error">{error} 输入保留，请核对后重试。</p>:null}<LectureForm initialRawId={initialRawId} courseId={courseId} courseName={courses.data?.items.find(course=>course.id===courseId)?.name} busy={busy} onDirtyChange={reportDirty} onSave={async (payload,key)=>{
      const saved=await run(async()=>{ await mutate('/notes/register',{raw_item_id:payload.notes_raw_item_id,course_id:courseId,request_id:key.catalog}); return personalRequest(`${BASE}/lectures`,{method:'POST',headers:{'Idempotency-Key':key.create},body:payload}); },'课堂记录已保存。');
      if(saved){reportDirty(false);setCreating(false);openLecture(saved.id);}
    }}/></DeskSheet> : null}
    {bookOpen && overview.data ? <BookEditor error={error} initialBook={overview.data.course?.id===courseId?overview.data.textbook:null} courseId={courseId} courseName={courses.data?.items.find(course=>course.id===courseId)?.name} busy={busy} run={run} mutate={mutate} onDirtyChange={reportDirty} onClose={()=>closeEditor(()=>setBookOpen(false))} onSource={setSource}/> : null}
    {source ? <SourcePreview source={source} onClose={()=>setSource(null)}/> : null}
    {dialog ? <ConfirmDialog {...dialog} message={error?`${dialog.message} ${error} 可重试相同请求，避免重复调用。`:dialog.message} busy={busy} onCancel={()=>setDialog(null)} onConfirm={async()=>{const done=await dialog.action();if(done)setDialog(null);}}/> : null}
  </div>;
}

export function BookSummary({ book,busy,onOpen }) {
  if(!book)return <div className="school-book-summary"><p>这门课尚未登记教材。</p><p className="personal-meta">笔记和原件可以先保存，之后再补教材与章节。</p>{onOpen?<button type="button" className="button button-quiet" disabled={busy} onClick={onOpen}>登记这门课的教材</button>:null}</div>;
  const publication=[book.publisher,book.isbn?`ISBN ${book.isbn}`:null].filter(Boolean).join(' · ');
  return <div className="school-book-summary"><p><strong>{book.title}</strong>{book.edition?` · ${book.edition}`:''}{book.editor?` · ${book.editor}`:''}</p>{publication?<p className="personal-meta">{publication}</p>:null}
    <p>{book.is_fixture ? '技术测试材料 · 不能代表真实教材' : book.document_id ? (book.real_acceptance==='accepted'?'教材与所选复盘的来源已核验':'教材原件已保存，识别文字仍需对照原页。') : '尚未关联教材 PDF 或章节扫描件'}</p>
    <p className="personal-meta">{book.document ? `PDF 共 ${book.document.page_count || '待登记'} 页；只解析你选定的章节。` : '课堂笔记和原件仍可查看；教材来源会在关联原件后显示。'}</p>
    {book.companions?.filter(b=>b.document).map(b=><div key={b.id}><p><strong>{b.title}</strong> · 已登记 {b.document.page_count} 个 PDF 页</p><p className="personal-meta">{b.library_metadata?.unconfirmed_metadata}</p><SourceLink className="personal-text-button" href={`/api/personal/attachments/${b.document.attachment_id}/original`}>查看教材原件</SourceLink></div>)}{onOpen?<button type="button" className="button button-quiet" disabled={busy} onClick={onOpen}>{book.document_id ? '查看教材与章节' : '关联教材原件'}</button>:null}</div>;
}

function LectureForm({ initialRawId,courseId,courseName,busy,onDirtyChange,onSave }) {
  const notes=usePersonalResource(`${BASE}/notes?course_id=${encodeURIComponent(courseId)}`);
  const draft=useDraft({title:'',lecture_date:'',notes_raw_item_id:initialRawId || ''});
  const keys=useRef({create:crypto.randomUUID(),catalog:crypto.randomUUID()});
  const dirty=Object.values(draft.values).some(Boolean);
  useEffect(()=>{onDirtyChange(dirty);},[dirty,onDirtyChange]);
  useEffect(()=>{ const choice=notes.data?.items.find(n=>n.raw_item_id===initialRawId); if(choice && !draft.values.title)draft.field('title',choice.title_hint || choice.filename.replace(/\.pdf$/i,'')); },[notes.data,initialRawId]);
  const submit=e=>{e.preventDefault();const v=draft.values;if(draft.validate({ ...(!v.title.trim()?{title:'请填写课堂主题。'}:{}),...(!v.notes_raw_item_id?{notes_raw_item_id:'请选择已关联这门课的笔记 PDF 或图片。'}:{}),...(v.lecture_date&&!validTime(v.lecture_date,'date')?{lecture_date:'请填写 YYYY-MM-DD，不确定可留空。'}:{})}))onSave({...v,course_id:courseId,learning_unit_id:null,lecture_date:v.lecture_date || null},keys.current);};
  return <form className="personal-form" ref={draft.formRef} noValidate onKeyDown={preventImeSubmit} onSubmit={submit}><ReadStatus resource={notes}/><fieldset disabled={busy}>
    <Field name="notes_raw_item_id" label="Inbox 课堂笔记" error={draft.errors.notes_raw_item_id}><select value={draft.values.notes_raw_item_id} onChange={e=>{draft.field('notes_raw_item_id',e.target.value);const n=notes.data?.items.find(x=>x.raw_item_id===e.target.value);if(n)draft.field('title',n.title_hint || n.filename.replace(/\.pdf$/i,''));}}><option value="">选择这门课的 PDF / 图片</option>{notes.data?.items.map(n=><option key={n.raw_item_id} value={n.raw_item_id}>{n.title_hint || n.filename}</option>)}</select></Field>
    <Field name="title" label="课堂主题" error={draft.errors.title}><input maxLength={200} value={draft.values.title} onChange={e=>draft.field('title',e.target.value)}/></Field>
    <Field name="lecture_date" label="实际课堂日期（可选）" help="YYYY-MM-DD；与上传时间分开记录，不确定请留空。" error={draft.errors.lecture_date}><input inputMode="numeric" placeholder="YYYY-MM-DD" value={draft.values.lecture_date} onChange={e=>draft.field('lecture_date',e.target.value)}/></Field>
    <p className="personal-meta">课程：{courseName||'当前课程'} · 章节可在课次保存后关联</p></fieldset><SubmitButton busy={busy}>保存课堂记录</SubmitButton></form>;
}

function BookEditor({ initialBook,courseId,courseName,busy,run,mutate,onClose,onDirtyChange,onSource,error }) {
  const bookResource=usePersonalResource(`${BASE}/textbook?course_id=${encodeURIComponent(courseId)}`,{refreshInterval:3000,paused:busy});
  const book=bookResource.data || initialBook;
  const [sectionDirty,setSectionDirty]=useState(false);
  const [file,setFile]=useState(null),[confirmed,setConfirmed]=useState(false),[large,setLarge]=useState(false),[role,setRole]=useState('full_textbook');
  const [uploadError,setUploadError]=useState(''),[jobId,setJobId]=useState(null),[fileVersion,setFileVersion]=useState(book?.version);
  const key=useRef(crypto.randomUUID()), receipt=useRef(null);
  const dirty=Boolean(file) || sectionDirty;
  useEffect(()=>{onDirtyChange(dirty);},[dirty,onDirtyChange]);
  useEffect(()=>{if(!file)setFileVersion(book?.version);},[book?.version,file]);
  if(!book)return <DeskSheet title="登记课程教材" busy={busy} onClose={onClose}><ReadStatus resource={bookResource}/>{error?<p role="alert" className="personal-field-error">{error}</p>:null}{!bookResource.loading&&!bookResource.error?<BookMetadataForm courseId={courseId} courseName={courseName} busy={busy} onDirtyChange={onDirtyChange} onSave={async(body,key)=>{const saved=await run(()=>mutate('/textbooks',body,'POST',{headers:{'Idempotency-Key':key}}),'教材信息已保存。');if(saved)bookResource.retry();return Boolean(saved);}}/>:null}</DeskSheet>;
  const upload=async e=>{e.preventDefault();setUploadError('');if(!file)return setUploadError('请选择一份 PDF。');if(fileVersion!==book.version)return setUploadError('教材已有更新，请先核对当前版本。');if(!confirmed)return setUploadError('请核对书名、版次和 ISBN 后确认。');if(file.size>250_000_000)return setUploadError('PDF 超过 250 MB，请提供当前章节扫描件。');if(file.size>15_000_000&&!large)return setUploadError('文件超过 15 MB，请确认继续保存。');
    const result=await run(async()=>{let saved=receipt.current;if(!saved){const form=new FormData();form.append('files',file);form.append('title_hint',book.title);form.append('confirm_large',String(large));saved=await personalRequest('/collect-files?inbox=1&purpose=textbook',{method:'POST',headers:{'Idempotency-Key':key.current},body:form,timeoutMs:300000});receipt.current=saved;}return mutate('/textbook/file',{textbook_id:book.id,raw_item_id:saved.submission_id,expected_version:fileVersion,file_role:role,edition_confirmed:true},'PUT');},'原教材已保存，正在本机登记 PDF 页数。');
    if(result){setFile(null);setJobId(result.job.id);bookResource.retry();}
  };
  return <DeskSheet title="教材与章节" onClose={onClose} busy={busy}>{error?<p role="alert" className="personal-field-error">{error} 原件和输入保留。</p>:null}<BookSummary book={book} busy={busy} onOpen={()=>document.getElementById('school-book-upload')?.focus()}/>
    {book.course_id==='course-math-ii'?<div className="school-job"><h3>已整理好的 Eric OS 教材目录</h3><p className="personal-meta">按目录中的 manifest 核验并登记上下册，保留教材库原件，不再次复制大文件。</p><button type="button" className="button button-quiet" disabled={busy} onClick={async()=>{const saved=await run(()=>mutate('/textbook/register-canonical',{}),'上下册已核验并登记，原件仍在 Eric OS 教材库。');if(saved)bookResource.retry();}}>登记 / 核对教材目录中的上下册</button></div>:null}
    <form className="personal-form" id="school-book-upload" tabIndex={-1} noValidate onSubmit={upload} onKeyDown={preventImeSubmit}><h3>{book.document_id?'替换教材文件（原件与历史引用保留）':'保存教材原件'}</h3><fieldset disabled={busy}>
      <Field name="textbook_pdf" label="合法取得的指定版本 PDF" error={uploadError}><input type="file" accept="application/pdf,.pdf" onChange={e=>{setFile(e.target.files?.[0] || null);setFileVersion(book.version);setConfirmed(false);key.current=crypto.randomUUID();receipt.current=null;}}/></Field>
      <Field name="textbook_role" label="文件内容"><select value={role} onChange={e=>setRole(e.target.value)}><option value="full_textbook">完整教材</option><option value="chapter_scan">当前章节扫描件</option></select></Field>
      <label className="personal-check"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>已核对为上述教材及已登记版本，不用其他版本替代</label>
      {file?.size>15_000_000?<label className="personal-check"><input type="checkbox" checked={large} onChange={e=>setLarge(e.target.checked)}/>确认保存较大的原件；不会整本调用 AI</label>:null}</fieldset>{file&&fileVersion!==book.version?<p className="personal-field-error">教材刚有更新，已选文件仍保留。<button type="button" className="personal-text-button" disabled={busy} onClick={()=>{setFileVersion(book.version);setConfirmed(false);setUploadError('');}}>核对当前教材后继续</button></p>:null}<SubmitButton busy={busy} disabled={Boolean(file)&&fileVersion!==book.version}>保存并登记页数</SubmitButton></form>
    {jobId?<JobStatus id={jobId}/>:null}
    {book.document ? <><h3>当前 PDF</h3><p>原件已保存 · {book.document.page_count || '待登记'} 页 · {book.document.status==='ready'?'结构可读取':'结构待确认'}</p><a className="personal-text-button" href={`/api/personal/attachments/${book.document.attachment_id}/original`}>下载完整原件</a>
      {book.document.status!=='ready'?<button type="button" className="button button-quiet" disabled={busy} onClick={async()=>{const j=await run(()=>mutate(`/documents/${book.document_id}/catalog`,{request_id:crypto.randomUUID(),retry:true}),'已恢复本地页数登记。');if(j)setJobId(j.id);}}>恢复页数登记</button>:<><SectionForm sourceVersion={book.version} busy={busy} onDirtyChange={setSectionDirty} onSave={async payload=>Boolean(await run(()=>mutate('/sections',{...payload,textbook_id:book.id,learning_unit_id:null}),'章节范围已保存。'))}/><h3>只处理当前章节</h3>{book.sections.length ? <ul className="school-section-list">{book.sections.map(s=><li key={s.id}><strong>{s.title}</strong><p>PDF 第 {s.start_page}–{s.end_page} 页</p><div className="personal-form-actions"><button type="button" className="button button-quiet" disabled={busy} onClick={async()=>{const j=await run(()=>mutate(`/sections/${s.id}/index`,{request_id:crypto.randomUUID(),retry:true}),'已安排本地章节解析。');if(j)setJobId(j.id);}}>解析 / 恢复本章节</button><button type="button" className="personal-text-button" onClick={()=>onSource({document_id:s.document_id,page:s.start_page,caption:`${book.title} · ${s.title} · PDF 第 ${s.start_page} 页`,original_url:`/api/personal/attachments/${book.document.attachment_id}/original`})}>查看起始页</button></div></li>)}</ul>:<p>根据实际 PDF 页码添加本次相关章节；不会默认按课次顺序对应教材。</p>}</>}
    </> : null}
  </DeskSheet>;
}

export function SectionForm({ busy,onSave,onDirtyChange,sourceVersion }) {
  const d=useDraft({title:'',start_page:'',end_page:''});
  const [version,setVersion]=useState(sourceVersion),dirty=Object.values(d.values).some(Boolean),stale=version!==sourceVersion;
  useEffect(()=>{onDirtyChange(dirty);},[dirty,onDirtyChange]);
  useEffect(()=>{if(!dirty)setVersion(sourceVersion);},[sourceVersion,dirty]);
  return <form className="personal-form" ref={d.formRef} noValidate onKeyDown={preventImeSubmit} onSubmit={async e=>{e.preventDefault();if(stale){d.validate({title:'教材原件已有更新，请核对当前原件后再保存。'});return;}const a=Number(d.values.start_page),b=Number(d.values.end_page);if(d.validate({...(!d.values.title.trim()?{title:'请填写实际章节名称。'}:{}),...(!Number.isSafeInteger(a)||a<1?{start_page:'请填写 PDF 起始页。'}:{}),...(!Number.isSafeInteger(b)||b<a||b-a>=80?{end_page:'结束页需大于等于起始页，最多 80 页。'}:{})})){if(await onSave({title:d.values.title,start_page:a,end_page:b,...(version==null?{}:{expected_version:version})})){d.field('title','');d.field('start_page','');d.field('end_page','');onDirtyChange(false);}}}}><h3>登记本次章节范围</h3><fieldset disabled={busy}><Field name="title" label="实际章节 / 小节名称" error={d.errors.title}><input value={d.values.title} maxLength={200} onChange={e=>d.field('title',e.target.value)}/></Field><div className="personal-form-grid"><Field name="start_page" label="PDF 起始页" error={d.errors.start_page}><input inputMode="numeric" value={d.values.start_page} onChange={e=>d.field('start_page',e.target.value)}/></Field><Field name="end_page" label="PDF 结束页" error={d.errors.end_page}><input inputMode="numeric" value={d.values.end_page} onChange={e=>d.field('end_page',e.target.value)}/></Field></div></fieldset>{stale?<p className="personal-field-error">教材原件已有更新，已填写的章节仍保留。<button type="button" className="personal-text-button" disabled={busy} onClick={()=>setVersion(sourceVersion)}>核对当前原件后继续</button></p>:null}<SubmitButton busy={busy} disabled={stale}>保存章节范围</SubmitButton></form>;
}

export function LectureDetail({ id,busy,run,mutate,onBack,onSource,onDialog,onBook,onNavigate,onDirtyChange,cloud=false }) {
  const resource=usePersonalResource(`${BASE}/lectures/${id}`,{refreshInterval:5000,paused:busy});
  const context=usePersonalResource(`${BASE}/lectures/${id}/context`,{paused:busy});
  const cost=usePersonalResource(`${BASE}/lectures/${id}/cost`,{paused:busy});
  const [reviewId,setReviewId]=useState(null),[jobId,setJobId]=useState(null),[mapping,setMapping]=useState(false),[reviewDirty,setReviewDirty]=useState(false),[mappingDirty,setMappingDirty]=useState(false);
  const lecture=resource.data;
  const selectedReviewId=preferredLectureReview(lecture,reviewId);
  const selectedReviewRef=useRef(selectedReviewId);selectedReviewRef.current=selectedReviewId;
  const reportReviewDirty=useCallback(value=>{setReviewDirty(value);if(value)setReviewId(current=>current||selectedReviewRef.current);},[]);
  const selectedReview=lecture?.artifacts.find(a=>a.id===selectedReviewId);
  const readableReview=lecture?.artifacts.find(reviewHasContent);
  const latestReview=lecture?.artifacts[0];
  const notesOnlyProfile=context.data?.review_profile==='general-course-v1';
  const missingRequired=!notesOnlyProfile&&Boolean(context.data?.missing.length);
  const courseName=lecture?.course?.name||context.data?.course?.name||(lecture?.course_id==='course-math-ii'?'高等数学 II':'当前课程');
  const selectedReviewDone=lecture?.artifacts.find(a=>a.id===selectedReviewId)?.status!=='processing';
  // Once reading starts, background versions must not replace this view or
  // discard feedback. Unknown legacy summaries remain unpinned until refreshed.
  useEffect(()=>{if(!reviewId&&reviewHasContent(selectedReview))setReviewId(selectedReview.id);},[reviewId,selectedReview]);
  useEffect(()=>{onDirtyChange(reviewDirty||mappingDirty);},[reviewDirty,mappingDirty,onDirtyChange]);
  const chooseReview=next=>{
    if(next===selectedReviewId)return;
    const apply=()=>{setReviewId(next);setReviewDirty(false);return true;};
    if(reviewDirty)onDialog({title:'放弃尚未保存的反馈？',message:'切换复盘版本后，这段反馈不会保留。可以取消并先保存反馈。',confirmLabel:'放弃反馈并切换',action:async()=>apply()});
    else apply();
  };
  const refresh=()=>{resource.retry();context.retry();cost.retry();};
  const exportContext=()=>void run(async()=>{const value=await personalRequest(`${BASE}/lectures/${id}/context`);const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=`lecture-${id}-evidence.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);return value;},'本次证据说明已导出。');
  const requestVision=(docId,page)=>{const requestId=crypto.randomUUID();onDialog({title:'核对这一页内容？',message:'只把选定的原始页发送给 DeepSeek 视觉模型，保存转写并标记待人工核对。失败后不会自动重复。明确重新核对可能产生新费用。',confirmLabel:'发送这一页并核对',action:async()=>{const job=await run(()=>mutate(`/lectures/${id}/vision`,{request_id:requestId,document_id:docId,pages:[page],confirm_cost:true,force:true}),'已安排本页视觉核对。');if(job){if(!job.queued)setJobId(job.id);refresh();}return Boolean(job);}});};
  const openSource=source=>onSource({...source,onVision:()=>requestVision(source.document_id,source.page)});
  const generate=force=>{const requestId=crypto.randomUUID();onDialog({title:force?'重新生成课堂复盘？':'生成课堂复盘？',confirmLabel:'发送有限上下文并生成',message:`${notesOnlyProfile?'将本次已保存笔记与可用的关联资料发给 DeepSeek；缺少的教材或课前记录不会补写。':'将本次笔记、课前预习表现和选定教材证据发给 DeepSeek。'}先核对证据再组织复盘；校验未通过时最多定向修复一次。最多核对 ${cost.data?.max_images || 0} 张页面图像、${cost.data?.max_requests || 3} 次请求，${yuan(cost.data?.estimated_cost_cny)}。这是预算参考，实际记录用量；未知结果不会自动重试。旧版本保留。`,action:async()=>{const result=await run(()=>mutate(`/lectures/${id}/generate`,{expected_version:lecture.version,request_id:requestId,confirm_cost:true,use_vision:true,force}),'复盘已安排，离开页面也会保留进度。');if(result){if(result.job?.id)setJobId(result.job.id);if(result.artifact_id&&!reviewDirty)setReviewId(result.artifact_id);refresh();}return Boolean(result);}});};
  return <><section className="personal-section"><button type="button" className="personal-text-button" disabled={busy} onClick={()=>onBack(lecture?.course_id)}><ArrowLeft size={15}/>返回课堂</button><ReadStatus resource={resource}/>{lecture?<>
    <p className="personal-meta">{courseName} · {lecture.lecture_date || '课堂日期未提供'}</p>{cloud?<h2>{lecture.title}</h2>:<h1 tabIndex={-1}>{lecture.title}</h1>}<p className="personal-intro">{STATES[lecture.status] || '待核对'} · 日期按实际课堂记录</p>
    <div className="personal-form-actions">{cloud?<button type="button" className="button button-quiet" onClick={exportContext}>导出本次证据说明</button>:<a className="button button-quiet" href={`/api/personal/school/lectures/${id}/context-pack`}>导出复盘上下文</a>}<button type="button" className="button button-primary" disabled={busy || !context.data || missingRequired} onClick={()=>generate(false)}>生成课堂复盘</button></div>
    {context.data?.before_lecture && !lecture.artifacts.length ? <PreviewEvidence data={context.data.before_lecture} compact /> : null}
    <ReadStatus resource={context}/>{context.data?.missing.length ? <div className="eric-inbox-quality"><strong>{notesOnlyProfile?'本次可先整理课堂笔记':'教材复盘尚未就绪'}</strong>{context.data.missing.map(x=><p key={x}>{x}</p>)}<p>{notesOnlyProfile?'生成结果会注明仅依据笔记，资料不足的部分保留为空。':'原始笔记可正常查看，上下文可以导出；不把缺失证据当作已完成。'}</p><button type="button" className="personal-text-button" onClick={()=>onBook(lecture.course_id)}>查看教材与章节</button></div>:null}
    {context.data?.is_fixture?<p className="eric-inbox-quality">技术 fixture 测试 · 真实教材最终验收 Pending</p>:null}
    <p className="personal-meta">{cost.data?`${yuan(cost.data.estimated_cost_cny)} · 本地解析 ¥0 · 未使用向量服务`:'费用参考正在读取。'} · 本页的生成按钮会先让你确认本次范围与费用。</p>
    {lecture.ai_usage?<p className="personal-meta">本课累计素材与复盘费用：{yuan(lecture.ai_usage.estimated_cost_cny)} · {lecture.ai_usage.images} 张云端图像 / {lecture.ai_usage.image_calls} 次图像请求 · {lecture.ai_usage.text_calls} 次文字请求。单个复盘版本显示该次增量成本。</p>:null}
    {jobId?<JobStatus id={jobId} onComplete={job=>{if(job.result?.artifact_id&&!reviewDirty)setReviewId(job.result.artifact_id);refresh();}}/>:null}
    <details className="desk-disclosure"><summary>处理记录与恢复</summary>{lecture.jobs.length?lecture.jobs.map(j=><JobStatus key={j.id} id={j.id}/>):<p>尚未请求模型。</p>}</details>
  </>:null}</section>
  {lecture?<><section className="personal-section"><h2>原始课堂笔记</h2>{lecture.notes.map(d=><NoteDocument key={d.id} doc={d} lecture={lecture} busy={busy} onSource={openSource} onVision={page=>requestVision(d.id,page)}/>)}</section>
    <section className="personal-section"><div className="personal-section-heading"><h2>对应教材</h2><button type="button" className="personal-text-button" disabled={busy} onClick={()=>onBook(lecture.course_id)}>教材与章节</button></div><p>{lecture.textbook?.title||'这门课尚未登记教材，课堂笔记仍可先整理。'}</p>{lecture.sections.filter(s=>s.link_status==='confirmed').length?<ul>{lecture.sections.filter(s=>s.link_status==='confirmed').map(s=><li key={s.id}>{s.title} · PDF 第 {s.start_page}–{s.end_page} 页<SectionPageSource lectureId={id} section={s} onSource={openSource}/></li>)}</ul>:<p className="personal-meta">尚未确认教材小节；课次顺序不会自动对应章节顺序。</p>}
      <button type="button" className="button button-quiet" disabled={busy || !availableSections(lecture).length} onClick={()=>setMapping(v=>!v)}>确认 / 修改章节关联</button>{mapping?<SectionMapping lecture={lecture} busy={busy} onDirtyChange={setMappingDirty} onSave={async ids=>{const saved=await run(()=>mutate(`/lectures/${id}/sections`,{expected_version:lecture.version,section_ids:ids},'PUT'),'章节关联已确认。');if(saved){setMapping(false);setMappingDirty(false);refresh();}}}/>:null}
      {!cloud?<RetrievalSearch id={id} onSource={openSource}/>:null}</section>
    <section className="personal-section"><div className="personal-section-heading"><h2>课堂复盘</h2>{(lecture.artifacts.some(a=>a.generated_at) || lecture.jobs.some(j=>['failed','recovery_needed'].includes(j.status)))?<button type="button" className="button button-quiet" disabled={busy || !context.data || missingRequired} onClick={()=>generate(true)}>保留旧版并重新生成</button>:null}</div>
      {lecture.artifacts.length?<Field name="review_revision" label="复盘版本"><select value={selectedReviewId} onChange={e=>chooseReview(e.target.value)}>{lecture.artifacts.map(a=><option key={a.id} value={a.id}>第 {a.version} 版 · {reviewStateLabel(a,STATES)}</option>)}</select></Field>:<p>尚无复盘。生成后会保存十部分结构、来源和用量，每次重新生成保留旧版。</p>}
      {reviewHasContent(selectedReview)&&latestReview?.id!==selectedReviewId&&reviewHasNoContent(latestReview)?<p className="personal-meta">第 {latestReview.version} 版{['processing','waiting','running'].includes(latestReview.status)?'仍在生成':'尚未完成'}，正在显示可阅读的第 {selectedReview.version} 版。其他记录可在上方切换查看。</p>:null}
      {reviewHasNoContent(selectedReview)&&readableReview?<button type="button" className="personal-text-button" onClick={()=>chooseReview(readableReview.id)}>阅读已有第 {readableReview.version} 版复盘</button>:null}
      {lecture.artifacts.length?<ReviewView key={selectedReviewId+':'+(selectedReviewDone?'saved':'pending')} artifactId={selectedReviewId} lecture={lecture} busy={busy} run={run} mutate={mutate} onSource={openSource} onSaved={refresh} onCorrected={id=>{setReviewId(id);refresh();}} onNavigate={onNavigate} onDirtyChange={reportReviewDirty}/>:null}
    </section><section className="personal-section"><h2>本次证据范围</h2><p className="personal-meta">有限选取；识别文字和 AI 转写均需对照原件。</p>{context.data?.sources.map(s=><button type="button" className="school-source-link personal-text-button" key={s.source_id} onClick={()=>openSource(s)}>{s.caption} · {s.quality==='text_extracted'?'PDF 文字':'请核对原页'}</button>)}</section></>:null}</>;
}

function NoteDocument({ doc,busy,onSource,onVision }) {
  const [page,setPage]=useState(1);
  const format=noteFormat(doc),image=format==='image',pages=Number.isSafeInteger(doc.page_count)&&doc.page_count>0?doc.page_count:null;
  const source={document_id:doc.id,page,caption:`${doc.title} · ${image?'原图':`${format==='pdf'?'PDF ':''}第 ${page} 页`}`,original_url:`/api/personal/attachments/${doc.attachment_id}/original`};
  return <div className="school-note"><h3>{doc.title}</h3><p className="personal-meta">{image?'原图已保存':`原始${format==='pdf'?' PDF':''}文件已保存${pages?` · 共 ${pages} 页`:''}`}。识别结果仍需对照原件核对。</p><div className="personal-form-actions"><SourceLink className="personal-text-button" href={source.original_url}>{image?'下载原图':format==='pdf'?'下载原始 PDF':'下载原件'}</SourceLink><button type="button" className="personal-text-button" onClick={()=>onSource(source)}>{image?'查看原图':'查看当前原页'}</button><button type="button" className="button button-quiet" disabled={busy} onClick={()=>onVision(page)}>用视觉模型核对本页</button></div><SourceImage source={source}/>{!image?<div className="personal-form-actions"><button type="button" className="button button-quiet" disabled={page===1} onClick={()=>setPage(p=>p-1)}>上一页</button><span>{pages?`第 ${page} / ${pages} 页`:'页数尚未确认'}</span><button type="button" className="button button-quiet" disabled={!pages||page>=pages} onClick={()=>setPage(p=>p+1)}>下一页</button></div>:null}</div>;
}

function SectionMapping({ lecture,busy,onDirtyChange,onSave }) {
  const [ids,setIds]=useState(lecture.sections.filter(s=>s.link_status==='confirmed').map(s=>s.id));
  const [error,setError]=useState('');
  const suggestions=usePersonalResource(`${BASE}/lectures/${lecture.id}/section-suggestions`);
  return <form className="personal-form" noValidate onKeyDown={preventImeSubmit} onSubmit={e=>{e.preventDefault();if(!ids.length||ids.length>4)setError('请选择 1–4 个当前教材小节。');else onSave(ids);}}><p className="personal-meta">关键词建议仅供参考：{suggestions.data?.items.map(x=>x.section.title).join('、') || '暂无匹配建议'}。由你确认关联。</p><fieldset disabled={busy}><legend>本次相关小节</legend>{availableSections(lecture).map(s=><label key={s.id} className="personal-check"><input type="checkbox" checked={ids.includes(s.id)} onChange={e=>{setIds(a=>e.target.checked?[...a,s.id]:a.filter(x=>x!==s.id));onDirtyChange(true);}}/>{lecture.course_id!=='course-math-ii'?s.textbook_title+' · ':''}{s.title} · PDF 第 {s.start_page}–{s.end_page} 页</label>)}</fieldset>{error?<p role="alert" className="personal-field-error">{error}</p>:null}<SubmitButton busy={busy}>确认章节关联</SubmitButton></form>;
}

function RetrievalSearch({ id,onSource }) {
  const [query,setQuery]=useState(''),[committed,setCommitted]=useState('');
  const input=useRef(null);
  const resource=usePersonalResource(`${BASE}/lectures/${id}/retrieve?q=${encodeURIComponent(committed || '极限')}`,{paused:!committed});
  return <details className="desk-disclosure"><summary>查找本次章节证据</summary><form className="personal-form" noValidate onKeyDown={preventImeSubmit} onSubmit={e=>{e.preventDefault();if(query.trim())setCommitted(query.trim());}}><Field name="school_query" label="教材关键词"><input ref={input} type="search" maxLength={2000} value={query} onChange={e=>{setQuery(e.target.value);if(!e.target.value)setCommitted('');}}/></Field><div className="personal-form-actions"><button type="submit" className="button button-quiet" disabled={!query.trim()}>检索当前章节</button>{query?<button type="button" className="personal-text-button" onClick={()=>{setQuery('');setCommitted('');input.current?.focus();}}>清除检索词</button>:null}</div></form>{committed?<><ReadStatus resource={resource}/>{resource.data?.reason?<p>{resource.data.reason}</p>:null}{resource.data?.items.map(s=><div key={s.source_id}><button type="button" className="school-source-link personal-text-button" onClick={()=>onSource(s)}>{s.caption}</button><p>{s.text}</p></div>)}</>:null}</details>;
}

function JobStatus({ id,onComplete }) {
  const resource=usePersonalResource(`${BASE}/jobs/${id}`,{refreshInterval:3000});
  const seen=useRef(null), job=resource.data;
  useEffect(()=>{if(job && !['waiting','running'].includes(job.status) && seen.current!==job.status){seen.current=job.status;onComplete?.(job);}},[job]);
  return <div className="school-job" aria-live="polite"><ReadStatus resource={resource}/>{job?<><p><strong>{STATES[job.status] || '待核对'}</strong> · {job.phase}</p>{job.errors?.map((e,i)=><p key={i} className="personal-field-error">{e.page?`PDF 第 ${e.page} 页：`:''}{e.message}</p>)}{job.result?.total?<p>已处理 {job.result.processed || 0} / {job.result.total} 页 · 复用 {job.result.cached || 0} 页</p>:null}<p className="personal-meta">{yuan(job.cost?.estimated_cost_cny)} · {job.cost?.image_calls || 0} 次图像请求 / {job.cost?.images || 0} 张图片 · {job.cost?.text_calls || 0} 次文字请求。未知结果不会自动重复。</p></>:null}</div>;
}

export function SourcePreview({ source,onClose }) {
  return <DeskSheet title="来源原页" onClose={onClose} wide><p><strong>{source.caption}</strong></p>{source.quality?<p className="personal-meta">识别质量：{source.quality==='text_extracted'?'PDF 内置文字':'待核对原件'} · 派生文字不代表数学内容已确认</p>:null}{source.text?<blockquote className="personal-quote">{source.text}</blockquote>:null}{source.uncertainties?.length?<div className="eric-inbox-quality"><strong>需要核对的具体内容</strong>{source.uncertainties.map((text,i)=><p key={i}>{text}</p>)}</div>:null}<SourceImage source={{document_id:source.document_id,page:source.page}}/>{source.original_url?<SourceLink className="personal-text-button" href={source.original_url}>下载已保存原始 PDF</SourceLink>:null}<p className="personal-meta">原件保存与内容理解分开；文字或公式识别失败也可以查看本页。</p>{source.onVision?<button type="button" className="button button-quiet" onClick={source.onVision}>用视觉模型核对本页</button>:null}<p className="personal-meta">来源标识：{source.source_id || source.document_id}{source.block_id?` · ${source.block_id}`:''}</p></DeskSheet>;
}

function Citations({ items,sources,onSource }) {
  return <div className="school-citations">{items.map(c=>{const s=sources.find(x=>x.source_id===c.source_id);return s?<button type="button" className="personal-text-button" key={c.source_id} onClick={()=>onSource(s)}>{s.caption}</button>:<span key={c.source_id}>来源暂不可读取，请重新打开复盘。</span>;})}</div>;
}

const REVIEW_ISSUE_AREAS = {core_concepts:'本节核心',notes_taught:'课堂笔记',must_master:'必须掌握',method_cards:'方法卡片',textbook_supplement:'教材补充',understanding_gaps:'理解核对',connections:'旧知识联系',self_tests:'自测答案',suggested_actions:'复习建议',notes_facts:'笔记证据',textbook_facts:'教材证据',formulas:'数学表达',conditions:'使用条件'};

function ReviewIssues({ issues }) {
  if (!issues?.length) return null;
  return <div className="eric-inbox-quality"><strong>需要核对的具体项目</strong><ul>{issues.slice(0,8).map((item,i)=><li key={i}>{REVIEW_ISSUE_AREAS[item.path?.split('/')[1]] || '复盘内容'}：{reviewIssueMessage(item)}</li>)}</ul>{issues.length>8?<p>另有 {issues.length-8} 项需要核对，原页和处理记录均保留。</p>:null}<p>自动调用已停止，可先查看原页与已有版本。</p></div>;
}

export function ReviewView({ artifactId,lecture,busy,run,mutate,onSource,onSaved,onCorrected,onNavigate,onDirtyChange }) {
  const resource=usePersonalResource(`${BASE}/reviews/${artifactId}`);
  const a=resource.data,c=a?.content;
  const [feedback,setFeedback]=useState('');
  useEffect(()=>{onDirtyChange(Boolean(feedback));},[feedback,onDirtyChange]);
  const cite=items=><Citations items={items} sources={a.sources} onSource={onSource}/>;
  const claim=(item,i)=><li key={i}><p><span className="personal-source-badge">{({notes:'当前笔记',textbook:'教材',inference:'AI 推断',uncertain:'识别不确定'})[item.provenance]}</span> {item.text}</p>{cite(item.citations)}</li>;
  const respond=async action=>{const r=await run(()=>mutate(`/reviews/${artifactId}/feedback`,{action,feedback,expected_lecture_version:lecture.version}),action==='accept'?'已记录采用此版本；尚未创建任务。':action==='reject'?'已拒绝此版本，原件和旧版保留。':'反馈已保存。');if(r){setFeedback('');resource.retry();onSaved();}};
  return <><ReadStatus resource={resource}/>{a?<><p className="personal-meta">第 {a.version} 版 · {STATES[a.status] || '待核对'} · {a.provider==='source_checked'?'来源校订':a.model} · {a.generated_at?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'medium',timeStyle:'short'}).format(new Date(a.generated_at)):'尚未完成'} · {yuan(a.cost?.estimated_cost_cny)}</p>{a.is_fixture?<p className="eric-inbox-quality">技术测试复盘，不能代表真实教材最终验收。</p>:null}{a.lecture_version_changed?<p className="personal-field-error">课次来源关联已变化，请核对这一版的来源范围。</p>:null}{a.error?<p role="alert" className="personal-field-error">{a.error.message}</p>:null}
    {a.input_versions?.source_checked_revision?<div className="eric-inbox-quality"><strong>来源校订版</strong><p>基于已保存的模型草稿，经来源校订后另存。原草稿与修改记录保留，本版仍需你核对后采用。</p></div>:null}
    {a.preview_evidence_changed ? <p className="personal-field-error">这份复盘生成后，课前预习记录有更新。旧版保留，可重新生成以读取新记录。</p> : null}
    <PreviewEvidence data={c?.before_lecture} />
    {c?.source_scope?<p className="eric-inbox-quality">{c.source_scope==='notes_only'?'本次复盘仅引用当前提供的笔记，未引用教材。':'本次复盘引用了笔记与已关联教材，识别不确定的内容仍需对照原页。'}</p>:null}
    <ReviewIssues issues={a.error?.issues}/>
    {!c?<SourceCorrectionEditor artifact={a} lecture={lecture} busy={busy} run={run} mutate={mutate} onSource={onSource} onSaved={()=>{resource.retry();onSaved();}} onCorrected={onCorrected} onDirtyChange={onDirtyChange}/>:null}
    {a.generation?.validation_status==='passed'&&a.generation?.mode!=='source_correction'?<div className="eric-inbox-quality"><strong>自动复盘已生成，待你核对采用</strong><p>先核对证据，再组织复盘；公式与条件保留来源原文。</p>{a.generation.validation?.uncertain_fact_ids?.length?<p>原页仍有不确定内容，已保留在“可能需要补充理解”中，请先对照来源核对。</p>:null}{a.generation.repair_used?<p>本次仅对未通过的具体项目修复过一次，之后已重新核验。</p>:null}</div>:null}
    {c?<div className="school-review"><section><h3>1 · 本节核心</h3>{c.core_concepts.length?<ul>{c.core_concepts.map((x,i)=><li key={i}><strong>{x.name}</strong><ul>{claim(x.explanation,0)}</ul></li>)}</ul>:<p>本次未提炼独立概念，可先对照笔记原页。</p>}</section><section><h3>2 · 课堂笔记记录了什么</h3><ul>{c.notes_taught.map(claim)}</ul></section><section><h3>3 · {c.source_scope?'复习重点':'必须掌握'}</h3>{c.must_master.length?<ul>{c.must_master.map(claim)}</ul>:<p>当前资料未提出额外复习重点。</p>}</section>
      <section><h3>4 · 方法卡片</h3>{c.method_cards.length?c.method_cards.map((x,i)=><div className="school-method" key={i}><p><strong>识别信号：</strong>{x.signal}</p><p><strong>候选方法：</strong>{x.method}</p><p><strong>适用条件：</strong>{x.conditions}</p><p><strong>常见误用：</strong>{x.common_misuse}</p>{cite(x.citations)}</div>):<p>当前资料不足以整理可靠的方法卡片。</p>}</section>
      <section><h3>5 · 教材补充</h3><p className="personal-meta">当前可识别笔记未见对应记录，不据此推断教师是否讲授。</p>{c.textbook_supplement.length?<ul>{c.textbook_supplement.map(claim)}</ul>:<p>本次未提出额外教材补充。</p>}</section><section><h3>6 · 可能需要补充理解</h3>{c.understanding_gaps.length?<ul>{c.understanding_gaps.map(claim)}</ul>:<p>当前证据不足以提出具体缺口。</p>}</section><section><h3>7 · 与旧知识的联系</h3>{c.connections.length?<ul>{c.connections.map(claim)}</ul>:<p>本次未提出额外联系。</p>}</section>
      <section><h3>8 · 你的易错点</h3><p>{c.mistakes.message}</p>{c.mistakes.items.length?<ul>{c.mistakes.items.map(claim)}</ul>:null}</section><section><h3>9 · 快速自测</h3>{c.self_tests.length?c.self_tests.map((x,i)=><div key={i}><p><strong>{i+1}. {x.question}</strong></p><details className="desk-disclosure"><summary>查看参考答案与来源</summary><p>{x.answer}</p>{cite(x.citations)}</details></div>):<p>当前资料没有足够的答案依据，本次未编写自测题。</p>}</section>
      <section><h3>10 · 复习建议</h3><p className="personal-meta">建议由你决定。采用复盘不会自动创建正式任务。</p>{c.suggested_actions.length?<><ul>{c.suggested_actions.map((x,i)=><li key={i}><p>{x.text} · 建议 {x.duration_minutes} 分钟</p><p className="personal-meta">{x.rationale.text}</p>{cite(x.rationale.citations)}</li>)}</ul><button type="button" className="button button-quiet" disabled={busy} onClick={async()=>{const r=await run(()=>mutate(`/reviews/${artifactId}/proposals`,{expected_version:lecture.version}),'建议已进入现有确认流程。');if(r && !r.queued && r.snapshot_id)onNavigate('review',r.snapshot_id);}}>送到待确认</button></>:<p>本次未提出行动建议。</p>}</section>
      <section><h3>核对与反馈</h3><Field name="review_feedback" label="反馈（可选）"><textarea rows={3} className="resize-none" maxLength={2000} value={feedback} onChange={e=>setFeedback(e.target.value)} style={{resize:'none'}}/></Field><div className="personal-form-actions"><button type="button" className="button button-primary" disabled={busy || a.adopted} onClick={()=>respond('accept')}>{a.adopted?'已采用此版':'采用此版'}</button><button type="button" className="button button-quiet" disabled={busy} onClick={()=>respond('comment')}>保存反馈</button><button type="button" className="personal-text-button" disabled={busy} onClick={()=>respond('reject')}>拒绝此版</button></div>{a.feedback.length?<details className="desk-disclosure"><summary>反馈历史 · {a.feedback.length} 条</summary>{a.feedback.map(f=><p key={f.id}>{({accept:'采用',reject:'拒绝',comment:'反馈'})[f.action]} · {f.feedback || '未附文字'}</p>)}</details>:null}</section>
    </div>:<p>此版本尚未完成，原件与已有复盘保留。请查看处理记录；不会自动重复请求模型。</p>}</>:null}</>;
}

// A bounded page selector within an explicitly confirmed section. The backend
// supplies the physical page identity and caption, including unreadable pages.
function SectionPageSource({ lectureId,section,onSource }) {
  const [page,setPage]=useState(String(section.start_page));
  const [selected,setSelected]=useState(section.start_page),[error,setError]=useState('');
  const resource=usePersonalResource(`${BASE}/lectures/${lectureId}/page-source?document_id=${encodeURIComponent(section.document_id)}&page=${selected}`);
  return <details className="desk-disclosure"><summary>查看教材原页 / 核对扫描内容</summary><form className="personal-form" noValidate onKeyDown={preventImeSubmit} onSubmit={e=>{e.preventDefault();const n=Number(page);if(!Number.isSafeInteger(n)||n<section.start_page||n>section.end_page){setError(`请选择 PDF 第 ${section.start_page}–${section.end_page} 页。`);return;}setError('');setSelected(n);}}><Field name="textbook_page" label="本小节 PDF 页码" error={error}><input inputMode="numeric" value={page} onChange={e=>setPage(e.target.value)}/></Field><button type="submit" className="button button-quiet">定位这一页</button></form><ReadStatus resource={resource}/>{resource.data?<button type="button" className="school-source-link personal-text-button" onClick={()=>onSource(resource.data)}>{resource.data.caption}</button>:null}</details>;
}
