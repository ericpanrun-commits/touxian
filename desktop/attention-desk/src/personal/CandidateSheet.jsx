import { useEffect, useRef, useState } from 'react';
import { Check, LoaderCircle, Pencil } from 'lucide-react';
import DeskSheet from './DeskSheet';
import CourseField from './CourseField';
import { ConfirmDialog } from './Evidence';
import { Field, SubmitButton } from './Forms';
import { exactSpan, preventImeSubmit, useDraft, validTime } from './formHelpers';
import { confirmationSummary, editReviewDate, reviewDraft, wechatConfirmation } from './reviewHelpers';
import { ActionDeadlineField, KeepAction } from './ActionFields';
import { personalRequest } from './api';
import usePersonalResource from './usePersonalResource';
import { ReadStatus } from './PlanningViews';
import WeChatEvidence from './WeChatEvidence';
import { displayWechatTime, wechatCategory } from './wechatHelpers';

function CandidateForm({ bundle, candidate, initialMode, onClose, onSaved, onCreated, onBusyChange }) {
  const [mode, setMode] = useState(initialMode);
  const conversion = Boolean(bundle.fyi_conversion);
  const confirming = mode === 'confirm' || conversion;
  const draft = useDraft(() => conversion ? structuredClone(bundle.draft) : wechatConfirmation(bundle, candidate) || reviewDraft(bundle, candidate.id));
  const [dirty, setDirty] = useState(false);
  const [discard, setDiscard] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [dateImpact, setDateImpact] = useState('');
  const pending = useRef(false);
  const value = draft.values.events[0];
  const warnings = bundle.extraction?.validation?.warnings || [];
  const edit = fields => { draft.setValues(current => ({ ...current, events: [{ ...current.events[0], ...fields }] })); setDirty(true); };
  const editDate = (id, fields) => {
    const result = editReviewDate(value, id, fields);
    setDateImpact(result.affected.length ? `取消日期后，这些行动将未设截止日期：${result.affected.map(action => action.title).join('；')}。可在行动中重新关联。` : '');
    edit(result.event);
  };
  const close = () => { if (pending.current) return; if (dirty) setDiscard(true); else onClose(); };
  useEffect(() => {
    const leave = event => { if (dirty || pending.current) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', leave);
    return () => window.removeEventListener('beforeunload', leave);
  }, [dirty]);
  const save = async confirm => {
    if (pending.current || !value) return;
    const errors = {};
    if (!value.title.trim() || Array.from(value.title).length > 200) errors.title = '请填写 1–200 字的标题。';
    if (value.summary.length > 4000) errors.summary = '摘要最多 4,000 字。';
    if (confirm && value.participation === 'yes' && value.relevance !== 'yes') errors.relevance = '请确认与你相关，再选择执行行动。';
    if (confirm) value.deadlines.forEach((date, index) => {
      if (!date.selected) return;
      const precision = date.precision === 'range' ? date.range_precision : date.precision;
      if (date.precision === 'unknown' || !validTime(date.value, precision)) errors[`date-${index}`] = '请填写包含年份的有效日期，或取消保留。';
      if (date.precision === 'range' && (!validTime(date.end_value || '', precision) || date.end_value.replace(' ', 'T') < date.value.replace(' ', 'T'))) errors[`end-${index}`] = '结束日期应不早于开始日期。';
    });
    if (value.actions.some(action => action.selected && (!action.title.trim() || action.title.length > 300))) errors.action = '每条行动请填写 1–300 字。';
    if (value.actions.some(action => action.selected && action.deadline_id && !value.deadlines.some(date => date.selected && date.id === action.deadline_id))) errors.action = '请选择已保留的日期或不关联。';
    if (value.locations.some(place => place.text.length > 500)) errors.location = '地点最多 500 字。';
    if (confirm && warnings.length && !draft.values.acknowledged_warnings) errors.warnings = '请核对下方提示后勾选确认。';
    if (confirm) {
      const spans = [...value.deadlines.filter(date => date.selected).flatMap(date => date.evidence), ...value.actions.filter(action => action.selected).flatMap(action => action.evidence), ...['locations', 'eligibility', 'materials'].flatMap(group => value[group].flatMap(fact => fact.evidence)), ...(value.audience?.evidence || [])];
      if (spans.some(span => !exactSpan(bundle.snapshot.blocks?.find(block => block.block_id === span.block_id)?.text || bundle.snapshot.original_text, span))) errors.evidence = '保存的引用与原文不匹配，请打开完整核对。';
    }
    if (!draft.validate(errors)) return;
    const event = { ...value, selected: true, deadlines: value.deadlines.map(date => ({ ...date, value: date.value.replace(' ', 'T'), confirmed_by_user: confirm && date.selected ? true : date.confirmed_by_user })) };
    pending.current = true; setBusy(true); onBusyChange(true); setError('');
    try {
      if (confirm) {
        const endpoint = conversion ? `/wechat/fyi/${encodeURIComponent(candidate.id)}/convert` : `/raw/${encodeURIComponent(candidate.snapshot_id)}/confirm`;
        const saved = await personalRequest(endpoint, { method: 'POST', body: { expected_version: bundle.version, ...(conversion ? { fyi_version: bundle.fyi_version } : {}), extraction_id: bundle.extraction_id, acknowledged_warnings: draft.values.acknowledged_warnings || !warnings.length, events: [{ ...event, deadlines: event.deadlines.filter(date => date.selected), actions: event.actions.filter(action => action.selected) }] } });
        setDirty(false); onCreated(saved.events);
      } else {
        const all = reviewDraft(bundle);
        const events = all.events.some(item => item.id === event.id) ? all.events.map(item => item.id === event.id ? event : item) : [...all.events, event];
        await personalRequest(`/raw/${encodeURIComponent(candidate.snapshot_id)}/draft`, { method: 'PUT', body: { expected_version: bundle.version, draft: { ...all, events, acknowledged_warnings: draft.values.acknowledged_warnings } } });
        setDirty(false); onSaved();
      }
    } catch (failure) { setError(failure.message); }
    finally { pending.current = false; setBusy(false); onBusyChange(false); }
  };
  const source = <><p className="personal-meta desk-source-line">{candidate.chat_name}{candidate.messages?.[0]?.source_time ? ` · ${displayWechatTime(candidate.messages[0].source_time)}` : ''}</p><details className="desk-disclosure"><summary>原始微信与 AI 分析</summary><WeChatEvidence candidate={candidate} onClose={() => {}} /></details></>;
  return <><DeskSheet title={conversion ? '转为事项' : mode === 'detail' ? '通知详情' : mode === 'edit' ? '编辑通知' : '确认通知'} onClose={close} busy={busy}>
    {error ? <p className="personal-field-error" role="alert">{error} 当前输入已保留。</p> : null}
    {!value ? <p>这条通知已被处理，请关闭后刷新待确认列表。</p> : mode === 'detail' ? <><span className="personal-status needs_review">待确认 · {wechatCategory(value.category)}</span><h3 className="desk-detail-title">{value.title}</h3><p className="personal-summary">{value.summary}</p>
      <dl className="desk-detail-facts">{value.course_id || bundle.course_suggestions?.[value.id] ? <div><dt>课程</dt><dd>{value.course_id ? "已选择课程，确认前可修改" : `${bundle.course_suggestions[value.id].name} · 建议，待你确认`}</dd></div> : null}{value.deadlines.filter(date => date.selected || date.original_text).map(date => <div key={date.id}><dt>{date.role}</dt><dd>{date.value?.replace('T', ' ') || `${date.original_text} · 待核对`}</dd></div>)}{value.locations.length ? <div><dt>地点</dt><dd>{value.locations.map(place => place.text).join('、')}</dd></div> : null}{value.actions.length ? <div><dt>行动</dt><dd>{value.actions.map(action => action.title).join('；')}</dd></div> : null}</dl>{source}<footer className="desk-sheet-footer"><button type="button" className="button button-quiet" onClick={() => setMode('edit')}><Pencil size={15} />编辑</button><button type="button" className="button button-primary" onClick={() => setMode('confirm')}><Check size={15} />确认</button></footer></> :
      <form ref={draft.formRef} className="personal-form desk-candidate-form" noValidate onKeyDown={preventImeSubmit} onSubmit={event => { event.preventDefault(); save(confirming); }}>
        <fieldset disabled={busy}><Field name="title" label="标题" error={draft.errors.title}><input value={value.title} onChange={event => edit({ title: event.target.value })} /></Field>
          <Field name="category" label="类型"><select value={value.category} onChange={event => edit({ category: event.target.value })}>{!['event', 'deadline', 'task', 'notice'].includes(value.category) ? <option value={value.category}>{wechatCategory(value.category)}</option> : null}<option value="event">活动 Event</option><option value="deadline">截止事项 Deadline</option><option value="task">任务 Task</option><option value="notice">通知 Notice</option></select></Field>
          <CourseField value={value.course_id} suggestion={bundle.course_suggestions?.[value.id] || bundle.course_suggestions?._default} onDraftChange={() => setDirty(true)} onChange={course_id => edit({ course_id })} onBusyChange={value => { pending.current = value; setBusy(value); onBusyChange(value); }} />
          <div className="desk-form-section"><div className="personal-section-heading"><h3>日期 / 截止时间</h3><button type="button" className="personal-text-button" disabled={value.deadlines.length >= 20} onClick={() => edit({ deadlines: [...value.deadlines, { id: 'manual-' + crypto.randomUUID(), selected: true, role: value.category === 'event' ? '活动时间' : '截止时间', value: '', precision: 'date', end_value: null, range_precision: null, original_text: '', evidence: [], confirmed_by_user: false, relation: 'current' }] })}>添加日期</button></div>
            {!value.deadlines.length ? <p className="personal-meta">{candidate.analysis.time_text ? `${candidate.analysis.time_text} · 原文日期尚不明确，暂不加入日历。` : '未提供日期，可留空。'}</p> : null}
            {value.deadlines.map((date, index) => <div className="desk-date-field" key={date.id}><label className="personal-checkbox-label"><input type="checkbox" checked={date.selected} onChange={event => editDate(date.id, { selected: event.target.checked })} />保留此日期：{date.role}{date.relation !== 'current' ? ' · 原先或已取消' : ''}</label>
              <Field name={`date-${index}`} label={date.role} error={draft.errors[`date-${index}`]} help={date.original_text ? `原文：${date.original_text}` : 'YYYY-MM-DD 或 YYYY-MM-DD HH:mm；不补造年份或时刻。'}><input value={date.value.replace('T', ' ')} placeholder="YYYY-MM-DD" onChange={event => editDate(date.id, { value: event.target.value, precision: date.precision === 'range' ? 'range' : event.target.value.trim().length > 10 ? 'minute' : 'date', selected: Boolean(event.target.value.trim()), confirmed_by_user: false })} /></Field>
              {date.precision === 'range' ? <Field name={`end-${index}`} label="结束日期 / 时间" error={draft.errors[`end-${index}`]}><input value={date.end_value || ''} onChange={event => edit({ deadlines: value.deadlines.map((item, i) => i === index ? { ...item, end_value: event.target.value } : item) })} /></Field> : null}</div>)}
          </div>
          {dateImpact ? <p role="status" className="personal-meta">{dateImpact}</p> : null}
          <Field name="location" label="地点" error={draft.errors.location}><input placeholder="可留空" value={value.locations[0]?.text || ''} onChange={event => edit({ locations: event.target.value ? [{ ...(value.locations[0] || { evidence: [] }), text: event.target.value }, ...value.locations.slice(1)] : value.locations.slice(1) })} /></Field>
          <div className="desk-form-section"><div className="personal-section-heading"><h3>行动</h3><button type="button" className="personal-text-button" disabled={value.actions.length >= 20} onClick={() => edit({ actions: [...value.actions, { id: 'manual-' + crypto.randomUUID(), selected: true, title: '', kind: 'manual', evidence: [], deadline_id: null }] })}>添加行动</button></div>{!value.actions.length ? <p className="personal-meta">暂无需要执行的行动，作为通知保存。</p> : null}
            {value.actions.map((action, index) => <div key={action.id} className="personal-review-child"><KeepAction action={action} onChange={selected => edit({ actions: value.actions.map((item, i) => i === index ? { ...item, selected } : item) })} /><Field name={index === 0 ? 'action' : `action-${index}`} label={value.actions.length === 1 ? '我要做什么' : `行动 ${index + 1}`} error={index === 0 ? draft.errors.action : undefined}><input value={action.title} onChange={event => edit({ actions: value.actions.map((item, i) => i === index ? { ...item, title: event.target.value } : item) })} /></Field><ActionDeadlineField action={action} deadlines={value.deadlines} name={`action-date-${index}`} onChange={deadline_id => edit({ actions: value.actions.map((item, i) => i === index ? { ...item, deadline_id } : item) })} /></div>)}
            {value.actions.length ? <Field name="participation" label="行动安排"><select value={value.participation} onChange={event => edit({ participation: event.target.value })}><option value="later">稍后决定，先保存行动方案</option><option value="yes">我要执行，创建待办任务</option><option value="no">不参加 / 不执行</option></select></Field> : null}
          </div>
          <details className="desk-disclosure"><summary>更多</summary><Field name="summary" label="一句话摘要" error={draft.errors.summary}><textarea className="resize-none" rows={3} style={{ resize: 'none' }} value={value.summary} onChange={event => edit({ summary: event.target.value })} /></Field>{!(confirming && value.participation === 'yes') ? <Field name="relevance" label="是否与我相关" error={draft.errors.relevance}><select value={value.relevance} onChange={event => edit({ relevance: event.target.value })}><option value="unclear">尚未确定</option><option value="yes">与我相关</option><option value="no">与我无关</option></select></Field> : null}<p className="personal-meta">{value.uncertainties.join('；') || '没有额外核对记录。'}</p></details>
          {confirming && value.participation === 'yes' ? <Field name="relevance" label="相关性确认" error={draft.errors.relevance}><select value={value.relevance} onChange={event => edit({ relevance: event.target.value })}><option value="unclear">请确认这项行动是否面向我</option><option value="yes">已核对，与我相关</option><option value="no">与我无关</option></select></Field> : null}
          {warnings.length ? <div className="desk-review-note"><p>请核对：{[...new Set(value.uncertainties)].join('；') || '来源或日期存在不确定项，请对照原文。'}</p><label className="personal-checkbox-label"><input name="warnings" type="checkbox" checked={draft.values.acknowledged_warnings} onChange={event => { draft.field('acknowledged_warnings', event.target.checked); setDirty(true); }} />我已核对原文与不确定项</label>{draft.errors.warnings ? <p className="personal-field-error" role="alert">{draft.errors.warnings}</p> : null}</div> : null}
          {draft.errors.evidence ? <p role="alert" className="personal-field-error">{draft.errors.evidence}</p> : null}
        </fieldset>{confirming ? <p role="status" className="personal-meta">{confirmationSummary(value)}</p> : null}{source}<footer className="desk-sheet-footer"><button type="button" className="button button-quiet" disabled={busy} onClick={close}>取消</button><SubmitButton busy={busy}>{conversion ? '保存为事项' : confirming ? '确认并保存事项' : '保存修改'}</SubmitButton></footer>
        {confirming ? <p className="personal-meta desk-confirm-help">{conversion ? '保存后进入所有事项，并保留微信来源。不会发送 Bark 通知。' : '确认表示你已核对保留的日期。只有这条候选会进入正式事项。'}</p> : null}
      </form>}
  </DeskSheet>{discard ? <ConfirmDialog title="放弃未保存的修改？" message="已保存的原文和草稿会保留。" confirmLabel="放弃修改" onCancel={() => setDiscard(false)} onConfirm={onClose} /> : null}</>;
}

export default function CandidateSheet({ candidate, mode, onClose, onSaved, onCreated, onBusyChange }) {
  const resource = usePersonalResource(mode === 'convert' ? `/wechat/fyi/${encodeURIComponent(candidate.id)}/conversion` : `/raw/${encodeURIComponent(candidate.snapshot_id)}`);
  if (!resource.data) return <DeskSheet title="读取通知" onClose={onClose}><ReadStatus resource={resource} label="通知" />{resource.loading ? <LoaderCircle className="spin" size={20} /> : null}</DeskSheet>;
  return <CandidateForm bundle={resource.data} candidate={candidate} initialMode={mode} onClose={onClose} onSaved={onSaved} onCreated={onCreated} onBusyChange={onBusyChange} />;
}
