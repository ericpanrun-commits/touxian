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

export function DeadlineForm({ deadline, evidence, original, blocks, busy, onSave, onCancel, onDirtyChange, requireEvidence = false, saveLabel = '保存截止时间' }) {
  const draft = useDraft({ label: deadline?.label || '', value: deadline?.value.replace('T', ' ') || '', precision: deadline?.precision || 'date',
    end_value: deadline?.end_value?.replace('T', ' ') || '', range_precision: deadline?.range_precision || 'date', timezone: deadline?.timezone || '', fold: deadline?.fold == null ? '' : String(deadline.fold), uncertain: Boolean(deadline?.uncertain),
    original_text: deadline?.original_text || '', confirmed_by_user: deadline?.confirmed_by_user ?? true, evidence_quote: evidence?.quote || '', block_id: evidence?.block_id || blocks?.[0]?.block_id || '' });
  const { values, errors, field } = draft;
  const unit = values.precision === 'range' ? values.range_precision : values.precision;
  const unknown = values.precision === 'unknown';
  const submit = (event) => {
    event.preventDefault();
    if (busy) return;
    const next = {};
    if (!values.label.trim() || values.label.length > 180) next.label = '请填写 1—180 字的时间用途。';
    if (!unknown && !validTime(values.value, unit)) next.value = unit === 'date' ? '请填写有效日期 YYYY-MM-DD。' : '请填写有效时间 YYYY-MM-DD HH:mm。';
    if (values.precision === 'range' && (!validTime(values.end_value, unit) || values.end_value.replace(' ', 'T') < values.value.replace(' ', 'T'))) next.end_value = '请填写有效且不早于开始值的结束日期 / 时间。';
    if (!unknown && values.timezone.trim()) { try { new Intl.DateTimeFormat('zh-CN', { timeZone: values.timezone.trim() }); } catch { next.timezone = '请填写有效的 IANA 时区，也可以留空。'; } }
    if (values.original_text.length > 1000) next.original_text = '日期表述最多 1,000 字。';
    else if (values.original_text && !original.includes(values.original_text)) next.original_text = '请复制原文中的日期表述，也可以留空。';
    const sourceText = blocks?.length ? blocks.find(b => b.block_id === values.block_id)?.text || '' : original;
    if (values.evidence_quote.length > 4000 || (values.evidence_quote && !sourceText.includes(values.evidence_quote))) next.evidence_quote = '请复制所选来源中的准确片段（最多 4,000 字）。';
    if (requireEvidence && !values.evidence_quote.trim()) next.evidence_quote = '来源变更需要准确引用，请复制新来源中的日期依据。';
    if (requireEvidence && (!values.confirmed_by_user || values.uncertain || unknown)) next.value = '请解决日期不确定性并明确确认，才能应用来源变更。';
    if (draft.validate(next)) {
      const { evidence_quote, block_id, ...payload } = values;
      onSave({ ...payload, value: unknown ? '' : payload.value, confirmed_by_user: unknown ? false : payload.confirmed_by_user, uncertain: unknown || payload.uncertain,
        timezone: payload.timezone.trim() || null, fold: payload.fold === '' ? null : Number(payload.fold),
        end_value: payload.precision === 'range' ? payload.end_value : null, range_precision: payload.precision === 'range' ? payload.range_precision : null,
        evidence: evidence_quote ? { quote: evidence_quote, ...(block_id ? { block_id } : {}) } : null });
    }
  };
  return <form ref={draft.formRef} className="personal-form" noValidate onSubmit={submit} onKeyDown={preventImeSubmit} onChange={() => onDirtyChange(true)}>
    <fieldset disabled={busy}>
      <Field name="label" label="时间用途" error={errors.label}><input value={values.label} onChange={(event) => field('label', event.target.value)} placeholder="例如：报名材料提交" /></Field>
      <div className="personal-form-grid">
        <Field name="precision" label="精度"><select value={values.precision} onChange={(event) => { field('precision', event.target.value); if (event.target.value === 'unknown') { field('confirmed_by_user', false); field('uncertain', true); } }}><option value="date">仅日期</option><option value="minute">日期和时分</option><option value="range">日期 / 时间范围</option><option value="unknown">未知 · 暂不安排提醒</option></select></Field>
        {!unknown ? <Field name="value" label={values.precision === 'range' ? '范围开始' : '截止日期 / 时间'} help={unit === 'date' ? '仅保存日期，不自动补成 23:59。' : '请明确填写时分和时区规则。'} error={errors.value}><input value={values.value} onChange={(event) => field('value', event.target.value)} placeholder={unit === 'date' ? 'YYYY-MM-DD' : 'YYYY-MM-DD HH:mm'} /></Field> : null}
      </div>
      {values.precision === 'range' ? <div className="personal-form-grid"><Field name="range_precision" label="范围精度"><select value={values.range_precision} onChange={event => field('range_precision', event.target.value)}><option value="date">全天日期范围 · 包含结束日</option><option value="minute">精确时间范围</option></select></Field><Field name="end_value" label="范围结束" error={errors.end_value}><input value={values.end_value} onChange={event => field('end_value', event.target.value)} placeholder={unit === 'date' ? 'YYYY-MM-DD' : 'YYYY-MM-DD HH:mm'} /></Field></div> : null}
      {!unknown ? <Field name="timezone" label="IANA 时区（可选）" error={errors.timezone} help="留空按当前系统时区解释；已有显式时区会保留。中国时间可填 Asia/Shanghai。"><input value={values.timezone} onChange={event => field('timezone', event.target.value)} placeholder="系统本地时区" /></Field> : null}
      {unit === 'minute' ? <Field name="fold" label="夏令时重复时刻" help="通常无需选择；本地时刻出现两次时才需要明确。"><select value={values.fold} onChange={event => field('fold', event.target.value)}><option value="">不指定（有歧义时拒绝保存）</option><option value="0">第一次出现</option><option value="1">第二次出现</option></select></Field> : null}
      <Field name="original_text" label="原文日期表述（可选）" error={errors.original_text}><input value={values.original_text} onChange={(event) => field('original_text', event.target.value)} /></Field>
      <SourceBlockPicker blocks={blocks} value={values.block_id} name="block_id" label="引用来源" onChange={value => field('block_id', value)} />
      <Field name="evidence_quote" label={requireEvidence ? '新来源的日期依据' : '依据引用（可选）'} help="复制原文中的片段，之后可点击查看依据。" error={errors.evidence_quote}><textarea className="resize-none" rows={3} style={{ resize: 'none' }} value={values.evidence_quote} onChange={(event) => field('evidence_quote', event.target.value)} /></Field>
      {!unknown ? <label className="personal-checkbox-label"><input type="checkbox" checked={values.uncertain} onChange={event => { field('uncertain', event.target.checked); if (event.target.checked) field('confirmed_by_user', false); }} />日期仍有不确定性 · 暂不安排提醒</label> : null}
      <label className="personal-checkbox-label"><input type="checkbox" checked={!unknown && values.confirmed_by_user} disabled={unknown || values.uncertain} onChange={(event) => field('confirmed_by_user', event.target.checked)} />此日期已经过我确认</label>
      {unknown ? <p className="personal-meta">保留未知精度，等待你补全日期；不进入日历或主动提醒。</p> : null}
    </fieldset>
    <div className="personal-form-actions"><SubmitButton busy={busy}>{saveLabel}</SubmitButton><button type="button" className="button button-quiet" disabled={busy} onClick={onCancel}>取消修改</button></div>
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
