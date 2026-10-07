import { cloneElement, useId, useRef } from 'react';
import { LoaderCircle, Save } from 'lucide-react';
import { preventImeSubmit, useDraft, validTime } from './formHelpers';
import { SourceBlockPicker } from './DocumentSources';

export function Field({ label, help, error, name, children }) {
  const id = useId();
  const description = `${id}-description`;
  return (
    <div className="personal-field">
      <label htmlFor={id}>{label}</label>
      {cloneElement(children, { id, name, 'aria-invalid': Boolean(error), 'aria-describedby': error || help ? description : undefined })}
      {error || help ? <p id={description} className={error ? 'personal-field-error' : 'personal-field-help'}>{error || help}</p> : null}
    </div>
  );
}

export function SubmitButton({ busy, children }) {
  return <button type="submit" className="button button-primary" disabled={busy} aria-busy={busy}>
    {busy ? <LoaderCircle size={16} className="spin" /> : <Save size={16} />}<span>{children}</span>
  </button>;
}

export function CollectionForm({ busy, onSave, onCancel, onDirtyChange }) {
  const draft = useDraft({ title: '', original_text: '', category: '其他', source_time: '' });
  const submission = useRef(null);
  const { values, errors, field } = draft;
  const submit = (event) => {
    event.preventDefault();
    if (busy) return;
    const next = {};
    if (!values.title.trim() || values.title.length > 200) next.title = '请填写 1—200 字的事项标题。';
    if (!values.original_text.trim()) next.original_text = '请粘贴完整的原始通知。';
    else if (values.original_text.length > 100000) next.original_text = '原文超过 100,000 字，请分成独立通知再收集。';
    if (!values.category.trim() || values.category.length > 80) next.category = '请填写 1—80 字的分类。';
    if (values.source_time.trim() && !validTime(values.source_time, values.source_time.trim().length === 10 ? 'date' : 'minute')) next.source_time = '请填写 YYYY-MM-DD 或 YYYY-MM-DD HH:mm，也可以留空。';
    if (draft.validate(next)) {
      const payload = { ...values, source_time: values.source_time.trim() || null };
      const signature = JSON.stringify(payload);
      if (submission.current?.signature !== signature) submission.current = { signature, id: crypto.randomUUID() };
      onSave(payload, submission.current.id);
    }
  };
  return (
    <form ref={draft.formRef} noValidate onSubmit={submit} onKeyDown={preventImeSubmit} onChange={() => onDirtyChange(true)} className="personal-form personal-collection-form">
      <fieldset disabled={busy}>
        <Field name="title" label="事项标题" error={errors.title}><input value={values.title} onChange={(event) => field('title', event.target.value)} placeholder="输入这件事的名称" /></Field>
        <Field name="original_text" label="原始通知" help="保留原文和换行。保存后原文不能修改，事项内容仍可编辑。" error={errors.original_text}><textarea className="resize-none" rows={12} style={{ resize: 'none' }} value={values.original_text} onChange={(event) => field('original_text', event.target.value)} placeholder="粘贴一整段通知" /></Field>
        <div className="personal-form-grid">
          <Field name="category" label="分类" error={errors.category}><input value={values.category} onChange={(event) => field('category', event.target.value)} /></Field>
          <Field name="source_time" label="原消息时间（可选）" help="北京时间；不清楚时留空。" error={errors.source_time}><input value={values.source_time} onChange={(event) => field('source_time', event.target.value)} placeholder="YYYY-MM-DD HH:mm" /></Field>
        </div>
      </fieldset>
      <div className="personal-form-actions"><SubmitButton busy={busy}>保存并创建事项</SubmitButton><button type="button" className="button button-quiet" disabled={busy} onClick={onCancel}>返回事项</button></div>
    </form>
  );
}

export function EventEditForm({ event, busy, onSave, onCancel, onDirtyChange }) {
  const draft = useDraft({ title: event.title, summary: event.summary, category: event.category });
  const { values, errors, field } = draft;
  const submit = (submitEvent) => {
    submitEvent.preventDefault();
    if (busy) return;
    const next = {};
    if (!values.title.trim() || values.title.length > 200) next.title = '请填写 1—200 字的标题。';
    if (!values.category.trim() || values.category.length > 80) next.category = '请填写 1—80 字的分类。';
    if (values.summary.length > 4000) next.summary = '摘要最多 4,000 字。';
    if (draft.validate(next)) onSave(values);
  };
  return <form ref={draft.formRef} className="personal-form" noValidate onSubmit={submit} onKeyDown={preventImeSubmit} onChange={() => onDirtyChange(true)}>
    <fieldset disabled={busy}>
      <Field name="title" label="事项标题" error={errors.title}><input value={values.title} onChange={(event) => field('title', event.target.value)} /></Field>
      <Field name="category" label="分类" error={errors.category}><input value={values.category} onChange={(event) => field('category', event.target.value)} /></Field>
      <Field name="summary" label="摘要（手动填写）" error={errors.summary}><textarea className="resize-none" rows={4} style={{ resize: 'none' }} value={values.summary} onChange={(event) => field('summary', event.target.value)} /></Field>
    </fieldset>
    <div className="personal-form-actions"><SubmitButton busy={busy}>保存事项内容</SubmitButton><button type="button" className="button button-quiet" disabled={busy} onClick={onCancel}>取消修改</button></div>
  </form>;
}

export function DeadlineForm({ deadline, evidence, original, blocks, busy, onSave, onCancel, onDirtyChange }) {
  const draft = useDraft({ label: deadline?.label || '', value: deadline?.value.replace('T', ' ') || '', precision: deadline?.precision || 'date',
    original_text: deadline?.original_text || '', confirmed_by_user: deadline?.confirmed_by_user ?? true, evidence_quote: evidence?.quote || '', block_id: evidence?.block_id || blocks?.[0]?.block_id || '' });
  const { values, errors, field } = draft;
  const submit = (event) => {
    event.preventDefault();
    if (busy) return;
    const next = {};
    if (!values.label.trim() || values.label.length > 180) next.label = '请填写 1—180 字的时间用途。';
    if (!validTime(values.value, values.precision)) next.value = values.precision === 'date' ? '请填写有效日期 YYYY-MM-DD。' : '请填写有效时间 YYYY-MM-DD HH:mm。';
    if (values.original_text.length > 1000) next.original_text = '日期表述最多 1,000 字。';
    else if (values.original_text && !original.includes(values.original_text)) next.original_text = '请复制原文中的日期表述，也可以留空。';
    const sourceText = blocks?.length ? blocks.find(b => b.block_id === values.block_id)?.text || '' : original;
    if (values.evidence_quote.length > 4000 || (values.evidence_quote && !sourceText.includes(values.evidence_quote))) next.evidence_quote = '请复制所选来源中的准确片段（最多 4,000 字）。';
    if (draft.validate(next)) {
      const { evidence_quote, block_id, ...payload } = values;
      onSave({ ...payload, evidence: evidence_quote ? { quote: evidence_quote, ...(block_id ? { block_id } : {}) } : null });
    }
  };
  return <form ref={draft.formRef} className="personal-form" noValidate onSubmit={submit} onKeyDown={preventImeSubmit} onChange={() => onDirtyChange(true)}>
    <fieldset disabled={busy}>
      <Field name="label" label="时间用途" error={errors.label}><input value={values.label} onChange={(event) => field('label', event.target.value)} placeholder="例如：报名材料提交" /></Field>
      <div className="personal-form-grid">
        <Field name="precision" label="精度"><select value={values.precision} onChange={(event) => field('precision', event.target.value)}><option value="date">仅日期</option><option value="minute">日期和时分</option></select></Field>
        <Field name="value" label="截止日期 / 时间" help={values.precision === 'date' ? '仅保存日期，不自动补成 23:59。' : '北京时间；请明确填写时分。'} error={errors.value}><input value={values.value} onChange={(event) => field('value', event.target.value)} placeholder={values.precision === 'date' ? 'YYYY-MM-DD' : 'YYYY-MM-DD HH:mm'} /></Field>
      </div>
      <Field name="original_text" label="原文日期表述（可选）" error={errors.original_text}><input value={values.original_text} onChange={(event) => field('original_text', event.target.value)} /></Field>
      <SourceBlockPicker blocks={blocks} value={values.block_id} name="block_id" label="引用来源" onChange={value => field('block_id', value)} />
      <Field name="evidence_quote" label="依据引用（可选）" help="复制原文中的片段，之后可点击查看依据。" error={errors.evidence_quote}><textarea className="resize-none" rows={3} style={{ resize: 'none' }} value={values.evidence_quote} onChange={(event) => field('evidence_quote', event.target.value)} /></Field>
      <label className="personal-checkbox-label"><input type="checkbox" checked={values.confirmed_by_user} onChange={(event) => field('confirmed_by_user', event.target.checked)} />此日期已经过我确认</label>
    </fieldset>
    <div className="personal-form-actions"><SubmitButton busy={busy}>保存截止时间</SubmitButton><button type="button" className="button button-quiet" disabled={busy} onClick={onCancel}>取消修改</button></div>
  </form>;
}

export function TaskForm({ deadlines, original, blocks, busy, onSave, onCancel, onDirtyChange }) {
  const draft = useDraft({ title: '', origin: 'manual', deadline_id: '', evidence_quote: '', block_id: blocks?.[0]?.block_id || '' });
  const { values, errors, field } = draft;
  const submit = (event) => {
    event.preventDefault();
    if (busy) return;
    const next = {};
    if (!values.title.trim() || values.title.length > 300) next.title = '请填写 1—300 字的任务名称。';
    const sourceText = blocks?.length ? blocks.find(b => b.block_id === values.block_id)?.text || '' : original;
    if (values.evidence_quote.length > 4000 || (values.evidence_quote && !sourceText.includes(values.evidence_quote))) next.evidence_quote = '请复制所选来源中的准确片段（最多 4,000 字）。';
    if (draft.validate(next)) {
      const { evidence_quote, block_id, ...payload } = values;
      onSave({ ...payload, deadline_id: payload.deadline_id || null, evidence: evidence_quote ? { quote: evidence_quote, ...(block_id ? { block_id } : {}) } : null });
    }
  };
  return <form ref={draft.formRef} className="personal-form" noValidate onSubmit={submit} onKeyDown={preventImeSubmit} onChange={() => onDirtyChange(true)}>
    <fieldset disabled={busy}>
      <Field name="title" label="任务名称" error={errors.title}><input value={values.title} onChange={(event) => field('title', event.target.value)} /></Field>
      <div className="personal-form-grid">
        <Field name="origin" label="任务来源"><select value={values.origin} onChange={(event) => field('origin', event.target.value)}><option value="manual">我自行添加</option><option value="explicit">原文明确要求</option><option value="suggested">辅助建议</option></select></Field>
        <Field name="deadline_id" label="关联截止时间"><select value={values.deadline_id} onChange={(event) => field('deadline_id', event.target.value)}><option value="">不绑定截止时间</option>{deadlines.map((deadline) => <option key={deadline.id} value={deadline.id}>{deadline.label} · {deadline.value.replace('T', ' ')}</option>)}</select></Field>
      </div>
      <SourceBlockPicker blocks={blocks} value={values.block_id} name="block_id" label="引用来源" onChange={value => field('block_id', value)} />
      <Field name="evidence_quote" label="依据引用（可选）" help="手动任务可不引用；有引用时保留准确原文。" error={errors.evidence_quote}><textarea className="resize-none" rows={3} style={{ resize: 'none' }} value={values.evidence_quote} onChange={(event) => field('evidence_quote', event.target.value)} /></Field>
    </fieldset>
    <div className="personal-form-actions"><SubmitButton busy={busy}>保存任务</SubmitButton><button type="button" className="button button-quiet" disabled={busy} onClick={onCancel}>取消添加</button></div>
  </form>;
}
