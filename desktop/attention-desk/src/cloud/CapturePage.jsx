import {useEffect,useRef,useState} from 'react';
import {Camera,Paperclip,X} from 'lucide-react';
import {Field,SubmitButton} from '../personal/Forms';
import {preventImeSubmit,validTime} from '../personal/formHelpers';
import usePersonalResource from '../personal/usePersonalResource';
import {capture,local,currentIdentity} from './client';
import {storeFile,fileReference,releaseUnusedFile} from './binaryStore';

export default function CapturePage({route,navigate,onNotice,onDirtyChange}) {
  const [draft,setDraft]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const picker=useRef(null),camera=useRef(null),pending=useRef(false),saved=useRef(false),latest=useRef(null),owner=currentIdentity().owner;
  const courses=usePersonalResource('/courses?limit=100');
  latest.current=draft;
  const edit=patch=>{const value={...latest.current,...patch};latest.current=value;onDirtyChange(Boolean(value.title||value.text||value.files.length));setDraft(value);};
  useEffect(()=>{
    let alive=true;
    local.drafts.get([owner,'capture']).then(async value=>{
      if(value)for(const [index,file] of value.files.entries()){if(file.fileId){const stored=await local.files.get(file.fileId);value.files[index]=stored?.owner===owner?fileReference(stored):{...file,state:'missing'};}}
      const meaningful=value&&(value.title||value.text||value.files.length),params=new URLSearchParams(location.search);
      const purpose=params.get('capture_purpose'),lectureDate=params.get('capture_date');
      if(!meaningful)value={owner,form:'capture',requestId:crypto.randomUUID(),title:'',text:'',files:[],
        purpose:['capture','lecture_note','textbook'].includes(purpose)?purpose:'capture',courseId:route?.id||'',lectureDate:lectureDate||''};
      if(alive)setDraft(value);
    }).catch(()=>{if(alive)setError('设备暂时无法保存草稿，请检查可用空间后重新打开。');});
    return()=>{alive=false;if(latest.current&&!saved.current)void local.drafts.put(latest.current).catch(()=>{});onDirtyChange(false);};
  },[owner,route?.id]);
  useEffect(()=>{
    if(!draft||saved.current)return;
    const dirty=Boolean(draft.title||draft.text||draft.files.length);onDirtyChange(dirty);
    const timer=setTimeout(()=>{if(!saved.current&&latest.current)void local.drafts.put(latest.current).catch(()=>setError('草稿暂未保存，请保留当前页面并检查设备空间。'));},250);
    return()=>clearTimeout(timer);
  },[draft,onDirtyChange]);
  const add=async event=>{
    const input=event.target,selected=Array.from(input.files||[]);
    if(!selected.length||!draft)return;
    if(draft.files.length+selected.length>10||selected.some(f=>!f.size||f.size>50_000_000)){input.value='';setError('一次最多 10 个文件，每个文件不超过 50 MB。');return;}
    if(pending.current)return;
    pending.current=true;setBusy(true);setError('');
    onDirtyChange(true);
    const references=selected.map(file=>({fileId:crypto.randomUUID(),filename:file.name,mime:file.type,bytes:file.size,state:'importing'}));
    const value={...draft,purpose:draft.purpose==='capture'&&selected.some(f=>f.type==='application/pdf')?'lecture_note':draft.purpose,files:[...draft.files,...references]};
    latest.current=value;setDraft(value);
    try{
      await local.drafts.put(value);
      for(const [index,file] of selected.entries()){
        const stored=fileReference(await storeFile(local,owner,file,references[index].fileId));
        const updated={...latest.current,files:latest.current.files.map(item=>item.fileId===stored.fileId?stored:item)};
        latest.current=updated;setDraft(updated);await local.drafts.put(updated);
      }
    }catch(error){window.dispatchEvent(new CustomEvent('eric-local-save-error',{detail:{type:error.name,innerType:error.cause?.name||error.inner?.name||null,phase:'file_import'}}));setError('文件尚未完整保存在此设备，请保留原文件并重新选择。已经保存的文件与文字仍保留。');}
    finally{input.value='';pending.current=false;setBusy(false);}
  };
  const remove=async index=>{
    if(busy)return;const file=draft.files[index],updated={...draft,files:draft.files.filter((_,i)=>i!==index)};
    latest.current=updated;setDraft(updated);setError('');
    try{await local.drafts.put(updated);if(file.fileId)await releaseUnusedFile(local,owner,file.fileId);}
    catch{setError('尚未确认移除，请重新打开这份草稿核对。原文件仍保留。');}
  };
  const submit=async event=>{
    event.preventDefault();if(pending.current||!draft)return;
    if(!draft.text.trim()&&!draft.files.length){setError('先写点内容，或选择要保存的文件。');return;}
    if(draft.purpose==='lecture_note'&&draft.lectureDate&&!validTime(draft.lectureDate,'date')){setError('课堂日期请填写 YYYY-MM-DD；不确定可以留空。');return;}
    pending.current=true;setBusy(true);setError('');
    try {
      const files=draft.files.filter(file=>file.blob).map(file=>new File([file.blob],file.filename,{type:file.mime}));
      const fileRefs=draft.files.filter(file=>file.fileId);
      saved.current=true;
      const text=[draft.purpose==='lecture_note'&&draft.lectureDate?`课堂日期：${draft.lectureDate}`:'',draft.text].filter(Boolean).join('\n');
      const course=courses.data?.items.find(item=>item.id===draft.courseId);
      await capture({title:draft.title,text,purpose:draft.purpose,files,fileRefs,requestId:draft.requestId,
        selectedCourseId:draft.courseId||null,courseCandidates:draft.courseId?[{course_id:draft.courseId,label:course?.name||''}]:[]});
      onDirtyChange(false);navigate('recent',null,{saved:true});onNotice('已保存在此设备，系统会自动发送；收到云端确认后显示“已接收”。');
    } catch(value){saved.current=false;setError(value.message);}
    finally {pending.current=false;setBusy(false);}
  };
  if(!draft)return <section className="personal-section"><p role="status">{error||'正在恢复草稿…'}</p></section>;
  return <section className="personal-section cloud-capture"><p className="personal-intro">先收好，之后接着整理。</p><form className="personal-form" noValidate onKeyDown={preventImeSubmit} onSubmit={submit}>
    <fieldset disabled={busy}><Field name="capture_title" label="标题（可选）"><input value={draft.title} maxLength={240} onChange={e=>edit({title:e.target.value})} placeholder="例如：今天的高数笔记"/></Field>
    <Field name="capture_text" label="内容"><textarea rows={7} className="resize-none" maxLength={100000} value={draft.text} onChange={e=>edit({text:e.target.value})} placeholder="记下一件事，或补充文件说明。"/></Field>
    <div className="personal-form-actions"><button type="button" className="button button-quiet" onClick={()=>picker.current.click()}><Paperclip size={17}/>选择图片 / PDF</button><button type="button" className="button button-quiet" onClick={()=>camera.current.click()}><Camera size={17}/>拍照</button></div>
    <input ref={picker} type="file" multiple accept="image/jpeg,image/png,image/webp,image/heic,image/heif,application/pdf,text/plain" hidden aria-label="选择收集文件" onChange={add}/><input ref={camera} type="file" accept="image/*" capture="environment" hidden aria-label="拍摄收集图片" onChange={add}/>
    {draft.files.length?<ul className="cloud-files">{draft.files.map((file,index)=><li key={file.fileId||index}><span><strong>{file.filename}</strong><small>{(file.bytes/1_000_000).toFixed(1)} MB{file.state==='importing'?(busy?' · 正在保存文件内容':' · 保存未完成，请重新选择'):file.state==='missing'?' · 请重新选择原文件':''}</small></span><button type="button" className="desk-icon-button" disabled={busy} aria-label={'移除 '+file.filename} onClick={()=>void remove(index)}><X size={17}/></button></li>)}</ul>:null}
    <Field name="capture_purpose" label="保存用途"><select value={draft.purpose} onChange={e=>edit({purpose:e.target.value})}><option value="capture">随手 Capture</option><option value="lecture_note">课堂笔记</option><option value="textbook">教材资料</option></select></Field>
    <Field name="capture_course" label="课程（可选）" help="明确选择后会直接沿用；不确定可留空，让系统判断。"><select value={draft.courseId||''} onChange={e=>edit({courseId:e.target.value})}><option value="">让 Eric OS 判断</option>{draft.courseId&&!courses.data?.items.some(item=>item.id===draft.courseId)?<option value={draft.courseId}>原来选定的课程</option>:null}{courses.data?.items.map(course=><option key={course.id} value={course.id}>{course.name}</option>)}</select></Field>
    {draft.purpose==='lecture_note'?<Field name="capture_lecture_date" label="实际课堂日期（可选）" help="与上传时间分开记录。YYYY-MM-DD；不确定可以留空。"><input inputMode="numeric" maxLength={10} placeholder="YYYY-MM-DD" value={draft.lectureDate||''} onChange={e=>edit({lectureDate:e.target.value})}/></Field>:null}</fieldset>
    {error?<p className="personal-field-error" role="alert">{error}</p>:null}<SubmitButton busy={busy}>保存到 Eric OS</SubmitButton><p className="personal-meta">断网也能先保存。离开页面或切换网络后，已保存的内容仍会保留。</p>
  </form></section>;
}
