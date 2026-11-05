import {useEffect,useRef} from 'react';
import {Field,SubmitButton} from './Forms';
import {useDraft,preventImeSubmit} from './formHelpers';

export function BookMetadataForm({courseId,courseName,busy,onSave,onDirtyChange}) {
  const draft=useDraft({title:'',edition:'',editor:'',publisher:'',isbn:''});
  const requestId=useRef(crypto.randomUUID());
  const dirty=Object.values(draft.values).some(Boolean);
  useEffect(()=>{onDirtyChange(dirty);},[dirty,onDirtyChange]);
  const submit=async event=>{
    event.preventDefault();
    if(!draft.validate({...(!courseId?{title:'请先选择这本教材所属的课程。'}:{}),...(!draft.values.title.trim()?{title:'请按原件填写教材名称。'}:{})}))return;
    const body={course_id:courseId,...Object.fromEntries(Object.entries(draft.values).map(([key,value])=>[key,value.trim()||null]))};
    if(await onSave(body,requestId.current))onDirtyChange(false);
  };
  return <form className="personal-form" ref={draft.formRef} noValidate onKeyDown={preventImeSubmit} onSubmit={submit}>
    <h3>登记教材信息</h3><p className="personal-meta">课程：{courseName||'请先选择课程'}。这里只登记信息，原件在下一步关联。</p>
    <fieldset disabled={busy}><Field name="title" label="教材名称" error={draft.errors.title}><input maxLength={200} value={draft.values.title} onChange={e=>draft.field('title',e.target.value)}/></Field>
      <details className="desk-disclosure"><summary>版次与出版信息（可选）</summary><p className="personal-meta">按原件填写；不确定的内容可以留空。</p>
        {[['edition','版次',120],['editor','署名（主编 / 作者）',120],['publisher','出版社',120],['isbn','ISBN',40]].map(([name,label,max])=><Field key={name} name={name} label={label}><input maxLength={max} value={draft.values[name]} onChange={e=>draft.field(name,e.target.value)}/></Field>)}
      </details>
    </fieldset><SubmitButton busy={busy} disabled={!courseId}>保存教材信息</SubmitButton>
  </form>;
}
