import {useEffect,useRef,useState} from 'react';
import {Field,SubmitButton} from './Forms';
import {useDraft,preventImeSubmit} from './formHelpers';
import {personalEditorDraft} from './api';
import {reviewIssueMessage} from './schoolReading';

export default function SourceCorrectionEditor({artifact,lecture,busy,run,mutate,onSource,onSaved,onCorrected,onDirtyChange}) {
  const [plan,setPlan]=useState(()=>artifact.source_correction?{...artifact.source_correction,lectureVersion:artifact.source_correction.expected_version??lecture.version}:null);
  const [ready,setReady]=useState(false),[storageError,setStorageError]=useState(''),[submitted,setSubmitted]=useState(false);
  const [draftSaved,setDraftSaved]=useState(false),[editing,setEditing]=useState(false);
  const [saving,setSaving]=useState(false),inFlight=useRef(false);
  const draft=useDraft({}),draftContext=useRef(null),writeLane=useRef(Promise.resolve()),submission=useRef(null),alive=useRef(true),editGeneration=useRef(0);
  const pending=artifact.source_correction_pending,waiting=pending&&['local_pending','pending','processing'].includes(pending.state);
  const form=plan?'source-correction:'+artifact.id+':'+plan.draft_hash:null;
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  useEffect(()=>{if(pending&&['needs_confirmation','failed'].includes(pending.state))setSubmitted(false);},[pending?.state]);
  useEffect(()=>{if(!plan&&artifact.source_correction)setPlan({...artifact.source_correction,lectureVersion:artifact.source_correction.expected_version??lecture.version});},[plan,artifact.source_correction,lecture.version]);
  useEffect(()=>{
    if(!form)return;
    let active=true;
    const initial=Object.fromEntries(plan.fields.map(field=>[field.key,field.segments]));
    personalEditorDraft(form,{action:'context'}).then(async context=>{if(!active)return null;draftContext.current=context;return personalEditorDraft(form,{context});}).then(saved=>{
      if(!active)return;
      const sent=pending?.body?.draft_hash===plan.draft_hash?Object.fromEntries(pending.body.changes.map(change=>[change.key,change.segments])):null;
      const values={...initial,...sent,...saved?.values};
      for(const field of plan.fields)if(!Array.isArray(values[field.key])||values[field.key].length!==field.segments.length||values[field.key].some(text=>typeof text!=='string'))values[field.key]=field.segments;
      draft.setValues(values);submission.current=saved?.submission||(sent?{signature:JSON.stringify(pending.body.changes),id:pending.body.request_id}:null);
      setDraftSaved(!!saved);setEditing(!!saved);onDirtyChange(false);setReady(true);
    }).catch(()=>{if(active){draft.setValues(initial);setReady(true);setStorageError('此设备暂未能恢复校订草稿，请保留当前页面。');}});
    return()=>{active=false;};
  },[form]);
  const edit=(field,index,text)=>{
    const values={...draft.values,[field.key]:draft.values[field.key].map((value,i)=>i===index?text:value)};
    draft.setValues(values);onDirtyChange(true);setSubmitted(false);setEditing(true);setDraftSaved(false);
    const context=draftContext.current;
    const generation=++editGeneration.current;
    writeLane.current=writeLane.current.catch(()=>{}).then(()=>personalEditorDraft(form,{action:'write',context,value:{values,submission:submission.current}}));
    void writeLane.current.then(()=>{if(alive.current&&generation===editGeneration.current){setStorageError('');if(context){setDraftSaved(true);onDirtyChange(false);}}}).catch(()=>{if(alive.current&&generation===editGeneration.current)setStorageError('校订尚未保存到此设备，请保留当前页面后重试。');});
  };
  const save=async event=>{
    event.preventDefault();if(busy||waiting||submitted||!ready||inFlight.current)return;
    inFlight.current=true;setSaving(true);
    try{
    const changes=plan.fields.filter(field=>JSON.stringify(draft.values[field.key])!==JSON.stringify(field.segments)).map(field=>({key:field.key,segments:draft.values[field.key]}));
    if(!draft.validate(changes.length?{}:{correction:'请先填写校订后的说明。'}))return;
    const unchanged=plan.fields.findIndex(field=>!changes.some(change=>change.key===field.key));
    if(unchanged>=0&&!draft.validate({[unchanged===0?'correction':`correction_${unchanged}_0`]:'请核对并修改这一项说明，再另存校订版。'}))return;
    const signature=JSON.stringify(changes);
    if(submission.current?.signature!==signature)submission.current={signature,id:crypto.randomUUID()};
    try{
      await writeLane.current.catch(()=>{});
      await personalEditorDraft(form,{action:'write',context:draftContext.current,value:{values:draft.values,submission:submission.current}});
    }catch{setStorageError('此设备未能保存校订，请保留当前页面后重试。');return;}
    if(!alive.current)return;
    const result=await run(()=>mutate(`/reviews/${artifact.id}/source-correction`,{request_id:submission.current.id,expected_version:plan.lectureVersion,draft_hash:plan.draft_hash,changes},'POST',{expectedIdentity:draftContext.current}),
      '校订已保存；来源核验通过后会另存新版，旧版本保留。');
    if(result&&alive.current){setSubmitted(true);onDirtyChange(false);await personalEditorDraft(form,{action:'remove',context:draftContext.current}).catch(()=>{});onSaved();if(result.artifact_id)onCorrected?.(result.artifact_id);}
    }finally{inFlight.current=false;if(alive.current)setSaving(false);}
  };
  if(!plan)return null;
  const stale=!artifact.source_correction||artifact.source_correction.draft_hash!==plan.draft_hash||lecture.version!==plan.lectureVersion;
  const corrected=artifact.source_correction_result?.artifact_id;
  const available=lecture.artifacts?.find(item=>item.id===corrected);
  return <details className="desk-disclosure"><summary>对照来源校订这份草稿</summary>
    <p>只修改下面需要核对的说明。原文引用、公式和条件保留；通过完整核验后另存新版，再由你决定是否采用。校订不调用模型。</p>
    {available?<button type="button" className="personal-text-button" onClick={()=>onCorrected?.(corrected)}>阅读校订后的第 {available.version} 版</button>:corrected?<p role="status">校订已核验保存，正在同步新版。</p>:waiting||submitted?<p role="status">校订已保存，正在等待电脑完成来源核验。</p>:null}
    {editing&&!waiting&&!submitted?<p className="personal-meta" role="status">{draftSaved?'校订草稿已保存在此设备，尚未提交核验。':draftContext.current?'正在保存校订草稿…':'校订尚未提交，请先保存。'}</p>:null}
    {pending?.error?<p className="personal-field-error" role="alert">{reviewIssueMessage({message:pending.error.message||'校订尚未完成，输入已保留，请核对后再保存。'})}</p>:null}
    {stale?<p className="personal-field-error" role="alert">来源或课堂已有更新。这份校订草稿已保留，请重新核对来源后继续。</p>:null}
    {storageError?<p className="personal-field-error" role="alert">{storageError}</p>:null}
    {!ready?<p className="personal-meta" role="status">正在恢复校订草稿…</p>:<form ref={draft.formRef} className="personal-form" noValidate onSubmit={event=>void save(event)} onKeyDown={preventImeSubmit}>
      {plan.fields.map((field,i)=><div key={field.key}>
          <h3>{field.label}</h3>{field.issues.map((message,index)=><p className="personal-meta" key={index}>{reviewIssueMessage({message})}</p>)}
          <fieldset disabled={busy||saving||waiting||submitted||stale}>
          {field.segments.map((original,index)=>original||index===0?<Field key={index} name={i===0&&index===0?'correction':`correction_${i}_${index}`} label={field.label+(index?' · 引用后的说明':' · 校订文字')} error={draft.errors[i===0&&index===0?'correction':`correction_${i}_${index}`]}>
            <textarea rows={3} maxLength={field.max_length||600} className="resize-none" style={{resize:'none'}} value={draft.values[field.key]?.[index]||''} onChange={event=>edit(field,index,event.target.value)}/>
          </Field>:null)}
          </fieldset>
          <div className="school-citations">{[...new Set(field.source_ids)].map(id=>{const source=artifact.sources.find(item=>item.source_id===id);return source?<button type="button" key={id} className="personal-text-button" onClick={()=>onSource(source)}>{source.caption}</button>:null;})}</div>
        </div>)}
      <SubmitButton busy={busy||saving} disabled={waiting||submitted||stale}>核验并另存校订版</SubmitButton>
    </form>}
  </details>;
}
