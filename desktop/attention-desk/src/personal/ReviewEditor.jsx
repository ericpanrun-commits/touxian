import { useState } from 'react';
import CourseField from './CourseField';
import { ActionDeadlineField, KeepAction } from './ActionFields';
import { Field, SubmitButton } from './Forms';
import { exactSpan, preventImeSubmit, quoteSpan, useDraft, validTime } from './formHelpers';
import { emptyEvent, reviewDraft } from './reviewHelpers';
import { SourceBlockPicker, sourceLabel } from './DocumentSources';

function EvidenceEditor({ value, original, blocks, name, errors, onChange, onView, disabled, compact = false }) {
  const sourceText = span => blocks?.length ? blocks.find(b => b.block_id === span.block_id)?.text || '' : original;
  const editor = <div className="personal-review-evidence">
    <p className="personal-meta">依据引用 · 准确片段及位置</p>
    {value.map((span, index) => <div className="personal-review-quote" key={index}>
      <SourceBlockPicker blocks={blocks} value={span.block_id} name={`${name}.${index}.block_id`} label="引用来源" onChange={id => onChange(value.map((item, position) => position === index ? { ...quoteSpan(blocks.find(b => b.block_id === id)?.text || '', span.quote), block_id: id } : item))} />
      <Field label={`引用 ${index + 1}`} name={`${name}.${index}.quote`} error={errors[`${name}.${index}.quote`]}><textarea className="resize-none" rows={2} style={{ resize: 'none' }} value={span.quote} onChange={event => onChange(value.map((item, position) => position === index ? { ...quoteSpan(sourceText(span), event.target.value), ...(span.block_id ? { block_id: span.block_id } : {}) } : item))} /></Field>
      <div className="personal-form-grid">
        <Field label="开始位置（码点）" name={`${name}.${index}.start_offset`}><input inputMode="numeric" value={span.start_offset} onChange={event => onChange(value.map((item, position) => position === index ? { ...item, start_offset: Number(event.target.value) } : item))} /></Field>
        <Field label="结束位置（不包含）" name={`${name}.${index}.end_offset`}><input inputMode="numeric" value={span.end_offset} onChange={event => onChange(value.map((item, position) => position === index ? { ...item, end_offset: Number(event.target.value) } : item))} /></Field>
      </div>
      <div className="personal-evidence-links"><button type="button" className="personal-text-button" disabled={disabled || !exactSpan(sourceText(span), span)} onClick={() => onView(span)}>查看依据{span.block_id ? ` · ${sourceLabel(blocks.find(b => b.block_id === span.block_id) || {})}` : ''}</button><button type="button" className="personal-text-button" disabled={disabled} onClick={() => onChange(value.filter((_, position) => position !== index))}>移除这条引用</button></div>
      {!exactSpan(sourceText(span), span) ? <p className="personal-field-error">引用或位置不符合所选来源，请更正或移除。无效引用不能正式保存。</p> : null}
    </div>)}
    {!value.length ? <p className="personal-meta">未绑定引用，请人工核对该字段。</p> : null}
    <button type="button" className="personal-text-button" disabled={disabled || value.length >= 20} onClick={() => onChange([...value, { quote: '', start_offset: 0, end_offset: 0, ...(blocks?.length ? { block_id: blocks[0].block_id } : {}) }])}>添加原文引用</button>
  </div>;
  return compact ? <details className="wechat-advanced"><summary>{value.length ? `${value.length} 条原文依据 · 查看或调整` : '添加原文依据'}</summary>{editor}</details> : editor;
}

export default function ReviewEditor({ bundle, busy: mutating, onConfirm, onSaveDraft, onDirtyChange, onView, onCancel, candidateId }) {
  const [creatingCourse, setCreatingCourse] = useState(false);
  const busy = mutating || creatingCourse;
  const draft = useDraft(() => reviewDraft(bundle, candidateId));
  const original = bundle.snapshot.original_text;
  const blocks = bundle.snapshot.blocks;
  const warnings = bundle.extraction?.validation?.warnings || bundle.snapshot.document_warnings || [];
  const edit = (action) => { draft.setValues(action); onDirtyChange(true); };
  const eventEdit = (index, fields) => edit(current => ({ ...current, events: current.events.map((value, position) => position === index ? { ...value, ...fields } : value) }));
  const childEdit = (eventIndex, group, index, fields) => edit(current => ({ ...current, events: current.events.map((value, position) => position === eventIndex
    ? { ...value, [group]: value[group].map((child, childIndex) => childIndex === index ? { ...child, ...fields } : child) } : value) }));
  const submit = event => {
    event.preventDefault();
    if (busy) return;
    const errors = {};
    const validateEvidence = (value, path) => value.forEach((span, index) => { if (!exactSpan(blocks?.length ? blocks.find(b => b.block_id === span.block_id)?.text || '' : original, span)) errors[`${path}.${index}.quote`] = '引用内容与位置必须精确对应所选来源。'; });
    if (!draft.values.events.some(value => value.selected)) errors['events.0.title'] = '请至少选择一个事项，或取消本次整理。';
    draft.values.events.forEach((value, index) => {
      if (!value.selected) return;
      const path = `events.${index}`;
      if (!value.title.trim() || Array.from(value.title).length > 200) errors[`${path}.title`] = '请填写 1—200 字的事项标题。';
      if (!value.category.trim() || Array.from(value.category).length > 80) errors[`${path}.category`] = '请填写 1—80 字的分类。';
      if (value.summary.length > 4000) errors[`${path}.summary`] = '摘要最多 4,000 字。';
      if (bundle.snapshot.wechat && value.participation === 'yes' && value.relevance !== 'yes') errors[`${path}.relevance`] = '请先确认与你相关，再选择执行。';
      value.deadlines.forEach((deadline, childIndex) => {
        if (!deadline.selected) return;
        const child = `${path}.deadlines.${childIndex}`;
        if (!deadline.role.trim()) errors[`${child}.role`] = '请填写日期用途。';
        const unit = deadline.precision === 'range' ? deadline.range_precision : deadline.precision;
        if (deadline.precision === 'unknown' || !validTime(deadline.value, unit)) errors[`${child}.value`] = '请填写包含年份的有效日期；分钟精度还需时分。';
        if (deadline.precision === 'range' && (!validTime(deadline.end_value || '', unit) || deadline.end_value < deadline.value)) errors[`${child}.end_value`] = '请填写不早于开始值的结束日期。';
        if (deadline.original_text && !original.includes(deadline.original_text)) errors[`${child}.original_text`] = '请复制准确原文表述，或留空。';
        if (!deadline.confirmed_by_user) errors[`${child}.confirmed_by_user`] = '请明确确认这个日期。';
        validateEvidence(deadline.evidence, `${child}.evidence`);
      });
      value.actions.forEach((action, childIndex) => {
        if (!action.selected) return;
        const child = `${path}.actions.${childIndex}`;
        if (!action.title.trim() || action.title.length > 300) errors[`${child}.title`] = '请填写 1—300 字的行动名称。';
        if (action.deadline_id && !value.deadlines.some(deadline => deadline.selected && deadline.id === action.deadline_id)) errors[`${child}.deadline_id`] = '请选择已保留的日期或不关联。';
        validateEvidence(action.evidence, `${child}.evidence`);
      });
      if (value.audience) validateEvidence(value.audience.evidence, `${path}.audience.evidence`);
      ['eligibility', 'materials', 'locations'].forEach(group => value[group].forEach((fact, childIndex) => validateEvidence(fact.evidence, `${path}.${group}.${childIndex}.evidence`)));
    });
    if (warnings.length && !draft.values.acknowledged_warnings) errors.acknowledged_warnings = '请核对并确认以上需要人工确认的内容。';
    if (draft.validate(errors)) onConfirm({ ...draft.values, events: draft.values.events.filter(value => value.selected).map(value => ({ ...value,
      deadlines: value.deadlines.filter(deadline => deadline.selected), actions: value.actions.filter(action => action.selected) })), expected_version: bundle.version, extraction_id: bundle.extraction_id });
  };
  const fieldWarning = path => warnings.filter(warning => warning.field_path === path);
  const renderWarnings = path => fieldWarning(path).length ? <ul className="personal-review-warnings">{fieldWarning(path).map((warning, index) => <li key={index}>{warning.message}</li>)}</ul> : null;
  return <form ref={draft.formRef} className="personal-form personal-review-form" noValidate onSubmit={submit} onKeyDown={preventImeSubmit} onChange={event => { onDirtyChange(true); draft.clearError(event.target.name); }}>
    {bundle.snapshot.wechat?.analysis?.length ? <div className="planning-source-update"><strong>微信分析建议 · 等待你的确认</strong>{bundle.snapshot.wechat.analysis.map(item => <p key={item.id}>{item.title} · {item.relevant === true ? '可能与你相关' : item.relevant === false ? '角色或范围不符' : '相关性未知'} · 模型置信度 {Math.round(item.confidence * 100)}%<br />{item.reason}</p>)}</div> : null}
    {warnings.length ? <div className="personal-review-warning-summary" role="status"><strong>{warnings.length} 项需要你核对</strong><p>年份、引用、附件和参与意愿都需要人工判断。确认后才会正式保存。</p><ul>{warnings.filter(w => w.field_path === 'sources').map((w, i) => <li key={i}>{w.message}{w.sources?.map(s => <button key={s.block_id} type="button" className="personal-text-button" onClick={() => { const b = blocks.find(b => b.block_id === s.block_id); const span = quoteSpan(b.text, s.date_text); onView({ ...span, block_id: b.block_id }); }}>查看 {s.date_text} 依据</button>)}</li>)}</ul></div> : null}
    <fieldset disabled={busy}>
      {draft.values.events.map((value, index) => {
        const path = `events.${index}`, candidatePath = `events.${value.id}`;
        return <section className="personal-review-event" key={value.id}>
          <div className="personal-section-heading"><h2>候选事项 {index + 1}</h2><span className="personal-status needs_review">待确认 · 尚未创建</span></div>
          <label className="personal-checkbox-label"><input type="checkbox" checked={value.selected} onChange={event => eventEdit(index, { selected: event.target.checked })} />保留这个候选事项</label>
          <Field label="事项标题" name={`${path}.title`} error={draft.errors[`${path}.title`]}><input value={value.title} onChange={event => eventEdit(index, { title: event.target.value })} /></Field>
          <Field label={bundle.snapshot.wechat ? '事项类型' : '分类'} name={`${path}.category`} error={draft.errors[`${path}.category`]}>{bundle.snapshot.wechat ? <select value={value.category} onChange={event => eventEdit(index, { category: event.target.value })}>{![ 'task', 'deadline', 'event', 'notice' ].includes(value.category) ? <option value={value.category}>{value.category}</option> : null}<option value="task">任务</option><option value="deadline">截止事项</option><option value="event">活动</option><option value="notice">通知</option></select> : <input value={value.category} onChange={event => eventEdit(index, { category: event.target.value })} />}</Field>
          <CourseField name={path + ".course_id"} value={value.course_id} suggestion={bundle.course_suggestions?.[value.id] || bundle.course_suggestions?._default} onBusyChange={setCreatingCourse} onDraftChange={() => onDirtyChange(true)} onChange={course_id => eventEdit(index, { course_id })} />
          <Field label="摘要" name={`${path}.summary`} error={draft.errors[`${path}.summary`]}><textarea className="resize-none" rows={3} style={{ resize: 'none' }} value={value.summary} onChange={event => eventEdit(index, { summary: event.target.value })} /></Field>
          <div className="personal-form-grid">
            <Field label="是否与我相关" name={`${path}.relevance`} error={draft.errors[`${path}.relevance`]}><select value={value.relevance} onChange={event => eventEdit(index, { relevance: event.target.value })}><option value="unclear">尚未确定</option><option value="yes">与我相关</option><option value="no">与我无关</option></select></Field>
            <Field label="是否参加 / 执行" name={`${path}.participation`} help="选择参加才会创建正式任务；稍后决定只保存行动方案。"><select value={value.participation} onChange={event => eventEdit(index, { participation: event.target.value })}><option value="later">稍后决定</option><option value="yes">参加 / 执行</option><option value="no">不参加 / 忽略</option></select></Field>
          </div>
          {renderWarnings(candidatePath + '.participation')}
          <Field label="通知性质" name={`${path}.notice_kind`} help="变更和取消通知不会自动覆盖或取消已有事项。"><select value={value.notice_kind} onChange={event => eventEdit(index, { notice_kind: event.target.value })}><option value="new">新事项</option><option value="update">变更 / 延期</option><option value="cancellation">取消通知</option><option value="information">资料 / 信息</option></select></Field>
          {renderWarnings(candidatePath + '.notice_kind')}
          {value.audience ? <div className="personal-review-child"><Field label="受众" name={`${path}.audience.text`}><input value={value.audience.text} onChange={event => eventEdit(index, { audience: { ...value.audience, text: event.target.value } })} /></Field><EvidenceEditor value={value.audience.evidence} original={original} blocks={blocks} name={`${path}.audience.evidence`} errors={draft.errors} onChange={evidence => eventEdit(index, { audience: { ...value.audience, evidence } })} onView={onView} disabled={busy} compact={Boolean(bundle.snapshot.wechat)} />{renderWarnings(candidatePath + '.audience')}</div> : null}

          <h3>截止时间</h3>
          {value.deadlines.map((deadline, childIndex) => {
            const child = `${path}.deadlines.${childIndex}`;
            return <div className="personal-review-child" key={deadline.id}>
              <label className="personal-checkbox-label"><input type="checkbox" checked={deadline.selected} onChange={event => childEdit(index, 'deadlines', childIndex, { selected: event.target.checked })} />保留截止时间：{deadline.role}</label>
              {deadline.relation !== 'current' ? <p className="personal-meta">{deadline.relation === 'previous' ? '原先日期 · 默认不创建' : '已取消日期 · 默认不创建'}</p> : null}
              <Field label="时间用途" name={`${child}.role`} error={draft.errors[`${child}.role`]}><input value={deadline.role} onChange={event => childEdit(index, 'deadlines', childIndex, { role: event.target.value })} /></Field>
              <div className="personal-form-grid"><Field label="精度" name={`${child}.precision`}><select value={deadline.precision} onChange={event => childEdit(index, 'deadlines', childIndex, { precision: event.target.value, end_value: event.target.value === 'range' ? deadline.end_value || '' : null, range_precision: event.target.value === 'range' ? 'date' : null, confirmed_by_user: false })}><option value="unknown">待核对</option><option value="date">仅日期</option><option value="minute">日期和时分</option><option value="range">日期 / 时间范围</option></select></Field>
                <Field label="截止日期 / 时间" name={`${child}.value`} help={deadline.precision === 'date' ? 'YYYY-MM-DD；没有具体时刻，不补 23:59。' : 'YYYY-MM-DD HH:mm；北京时间。'} error={draft.errors[`${child}.value`]}><input placeholder={deadline.precision === 'date' ? 'YYYY-MM-DD · 请确认年份' : 'YYYY-MM-DD HH:mm'} value={deadline.value.replace('T', ' ')} onChange={event => childEdit(index, 'deadlines', childIndex, { value: event.target.value, confirmed_by_user: false })} /></Field></div>
              <Field label="原文日期表述" name={`${child}.original_text`} error={draft.errors[`${child}.original_text`]}><input value={deadline.original_text} onChange={event => childEdit(index, 'deadlines', childIndex, { original_text: event.target.value })} /></Field>
              {deadline.precision === 'range' ? <div className="personal-form-grid"><Field label="范围精度" name={`${child}.range_precision`}><select value={deadline.range_precision || 'date'} onChange={event => childEdit(index, 'deadlines', childIndex, { range_precision: event.target.value, confirmed_by_user: false })}><option value="date">仅日期</option><option value="minute">日期和时分</option></select></Field><Field label="结束日期 / 时间" name={`${child}.end_value`} error={draft.errors[`${child}.end_value`]}><input value={deadline.end_value || ''} onChange={event => childEdit(index, 'deadlines', childIndex, { end_value: event.target.value, confirmed_by_user: false })} /></Field></div> : null}
              {renderWarnings(candidatePath + '.deadlines.' + deadline.id)}
              <EvidenceEditor value={deadline.evidence} original={original} blocks={blocks} name={`${child}.evidence`} errors={draft.errors} onChange={evidence => childEdit(index, 'deadlines', childIndex, { evidence })} onView={onView} disabled={busy} compact={Boolean(bundle.snapshot.wechat)} />
              <label className="personal-checkbox-label"><input name={`${child}.confirmed_by_user`} type="checkbox" checked={deadline.confirmed_by_user} onChange={event => childEdit(index, 'deadlines', childIndex, { confirmed_by_user: event.target.checked })} />我已核对并确认此日期</label>
              {draft.errors[`${child}.confirmed_by_user`] ? <p className="personal-field-error">{draft.errors[`${child}.confirmed_by_user`]}</p> : null}
            </div>;
          })}
          <button type="button" className="personal-text-button" disabled={busy || value.deadlines.length >= 40} onClick={() => eventEdit(index, { deadlines: [...value.deadlines, { id: 'manual-' + crypto.randomUUID(), selected: true, role: '截止时间', value: '', precision: 'date', original_text: '', evidence: [], confirmed_by_user: false, relation: 'current' }] })}>添加截止时间</button>

          <h3>行动候选</h3><p className="personal-meta">原文明确要求与辅助建议分别保留。稍后决定或不参加时，不创建正式任务。</p>
          {value.actions.map((action, childIndex) => {
            const child = `${path}.actions.${childIndex}`;
            return <div className="personal-review-child" key={action.id}>
              <KeepAction action={action} onChange={selected => childEdit(index, 'actions', childIndex, { selected })} />
              <Field label="行动名称" name={`${child}.title`} error={draft.errors[`${child}.title`]}><input value={action.title} onChange={event => childEdit(index, 'actions', childIndex, { title: event.target.value })} /></Field>
              <div className="personal-form-grid"><Field label="行动来源" name={`${child}.kind`}><select value={action.kind} onChange={event => childEdit(index, 'actions', childIndex, { kind: event.target.value })}><option value="explicit">原文明确要求</option><option value="suggested">辅助建议</option><option value="manual">我自行添加</option></select></Field>
                <ActionDeadlineField action={action} deadlines={value.deadlines} name={`${child}.deadline_id`} error={draft.errors[`${child}.deadline_id`]} onChange={deadline_id => childEdit(index, 'actions', childIndex, { deadline_id })} /></div>
              {renderWarnings(candidatePath + '.actions.' + action.id)}
              <EvidenceEditor value={action.evidence} original={original} blocks={blocks} name={`${child}.evidence`} errors={draft.errors} onChange={evidence => childEdit(index, 'actions', childIndex, { evidence })} onView={onView} disabled={busy} compact={Boolean(bundle.snapshot.wechat)} />
            </div>;
          })}
          <button type="button" className="personal-text-button" disabled={busy || value.actions.length >= 40} onClick={() => eventEdit(index, { actions: [...value.actions, { id: 'manual-' + crypto.randomUUID(), selected: true, title: '新行动', kind: 'manual', deadline_id: null, evidence: [] }] })}>添加行动</button>

          {['eligibility', 'materials', 'locations'].map(group => <div key={group}><h3>{{ eligibility: '资格条件', materials: '所需材料', locations: '地点' }[group]}</h3>
            {value[group].map((fact, childIndex) => <div className="personal-review-child" key={childIndex}>
              <Field label={{ eligibility: '资格表述', materials: '材料名称', locations: '地点表述' }[group]} name={`${path}.${group}.${childIndex}.text`}><input value={fact.text} onChange={event => childEdit(index, group, childIndex, { text: event.target.value })} /></Field>
              {renderWarnings(`${candidatePath}.${group}.${childIndex}`)}<EvidenceEditor value={fact.evidence} original={original} blocks={blocks} name={`${path}.${group}.${childIndex}.evidence`} errors={draft.errors} onChange={evidence => childEdit(index, group, childIndex, { evidence })} onView={onView} disabled={busy} compact={Boolean(bundle.snapshot.wechat)} />
              <button type="button" className="personal-text-button" disabled={busy} onClick={() => eventEdit(index, { [group]: value[group].filter((_, position) => position !== childIndex) })}>移除这项</button>
            </div>)}
            <button type="button" className="personal-text-button" disabled={busy || value[group].length >= 30} onClick={() => eventEdit(index, { [group]: [...value[group], { text: '', evidence: [] }] })}>添加{{ eligibility: '资格条件', materials: '材料', locations: '地点' }[group]}</button>
          </div>)}
          <Field label="AI 不确定项 / 我的补充" name={`${path}.uncertainties`} help="每行一项；原分析记录仍保留。"><textarea className="resize-none" rows={3} style={{ resize: 'none' }} value={value.uncertainties.join('\n')} onChange={event => eventEdit(index, { uncertainties: event.target.value.split('\n').filter(Boolean) })} /></Field>
        </section>;
      })}
      <button type="button" className="personal-text-button" disabled={busy || draft.values.events.length >= 8} onClick={() => edit(current => ({ ...current, events: [...current.events, emptyEvent()] }))}>添加候选事项</button>
      {warnings.length ? <><label className="personal-checkbox-label"><input name="acknowledged_warnings" type="checkbox" checked={draft.values.acknowledged_warnings} onChange={event => edit(current => ({ ...current, acknowledged_warnings: event.target.checked }))} />我已核对年份、原文引用、参与决定和其他不确定项</label>{draft.errors.acknowledged_warnings ? <p className="personal-field-error">{draft.errors.acknowledged_warnings}</p> : null}</> : null}
    </fieldset>
    <div className="personal-form-actions"><SubmitButton busy={busy}>确认并创建所选事项</SubmitButton><button type="button" className="button button-quiet" disabled={busy} onClick={() => onSaveDraft(draft.values)}>保存预览草稿</button><button type="button" className="personal-text-button" disabled={busy} onClick={onCancel}>取消本次整理</button></div>
  </form>;
}
