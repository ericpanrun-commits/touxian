import { useEffect, useRef, useState } from 'react';
import { Field, SubmitButton } from './Forms';
import { personalRequest } from './api';
import usePersonalResource from './usePersonalResource';
import { preventImeSubmit, useDraft } from './formHelpers';

export default function CourseField({ value, onChange, suggestion, current, disabled = false, name = 'course_id', onBusyChange = () => {}, onDraftChange = () => {} }) {
  const courses = usePersonalResource('/courses?limit=100');
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState('');
  const [created, setCreated] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef(false);
  const controller = useRef(null);
  const input = useRef(null);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => { if (adding) input.current?.focus(); }, [adding]);
  const options = [...new Map([...(courses.data?.items || []), current, suggestion, created].filter(Boolean).map(course => [course.id, course])).values()];
  const create = async () => {
    if (pending.current || disabled) return;
    const name = newName.trim();
    if (!name || Array.from(name).length > 120) { setError('课程名称请填写 1–120 字。'); input.current?.focus(); return; }
    controller.current = new AbortController();
    pending.current = true; setSaving(true); onBusyChange(true); setError('');
    try {
      const saved = await personalRequest('/courses', { method: 'POST', body: { name }, signal: controller.current.signal });
      if (controller.current.signal.aborted) return;
      setCreated(saved); onChange(saved.id); setAdding(false); setNewName(''); courses.retry();
    } catch (failure) { if (!controller.current.signal.aborted) setError(failure.message); }
    finally { if (!controller.current.signal.aborted) { pending.current = false; setSaving(false); onBusyChange(false); } }
  };
  return <div className="personal-course-field">
    <Field label="课程（可选）" name={name} help="不关联课程也可以保存事项和创建任务。">
      <select value={adding ? '__new' : value || ''} disabled={disabled || saving} onChange={event => {
        onDraftChange();
        if (event.target.value === '__new') setAdding(true);
        else { setAdding(false); onChange(event.target.value || null); }
      }}>
        <option value="">不关联课程</option>
        {options.map(course => <option key={course.id} value={course.id}>{course.name}</option>)}
        <option value="__new">添加其他课程…</option>
      </select>
    </Field>
    {suggestion && value !== suggestion.id && !adding ? <p className="personal-meta">建议课程：{suggestion.name} · 待你确认 <button type="button" className="personal-text-button" disabled={disabled || saving} onClick={() => onChange(suggestion.id)}>采用建议</button></p> : null}
    {courses.loading ? <p className="personal-meta" role="status">正在读取课程，可先保留为空。</p> : null}
    {courses.error ? <p className="personal-field-error" role="alert">课程列表未能读取，当前选择已保留。<button type="button" className="personal-text-button" disabled={disabled} onClick={courses.retry}>重新读取课程</button></p> : null}
    {adding ? <div className="personal-review-child">
      <Field label="新课程名称" name={name + '.new'} error={error}><input ref={input} value={newName} maxLength={120} disabled={disabled || saving} onChange={event => { setNewName(event.target.value); setError(''); onDraftChange(); }} onKeyDown={event => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); event.stopPropagation(); create(); } }} /></Field>
      <button type="button" className="button button-quiet" disabled={disabled || saving} aria-busy={saving} onClick={create}>{saving ? '正在添加…' : '添加并选择'}</button>
      <button type="button" className="personal-text-button" disabled={disabled || saving} onClick={() => setAdding(false)}>取消添加</button>
    </div> : null}
  </div>;
}

export function CourseEditor({ event, busy, onDirtyChange, onSave, onCancel }) {
  const draft = useDraft({ course_id: event.course_id || null });
  const [creating, setCreating] = useState(false);
  const locked = busy || creating;
  return <form ref={draft.formRef} className="personal-form" noValidate onKeyDown={preventImeSubmit} onSubmit={e => { e.preventDefault(); if (!locked) onSave(draft.values.course_id); }}>
    <fieldset disabled={locked}>
      <CourseField value={draft.values.course_id} current={event.course} onBusyChange={setCreating} onDraftChange={() => onDirtyChange(true)} onChange={id => { draft.field('course_id', id); onDirtyChange(true); }} />
    </fieldset>
    <div className="personal-form-actions"><SubmitButton busy={locked}>保存课程关联</SubmitButton><button type="button" className="button button-quiet" disabled={locked} onClick={onCancel}>取消修改</button></div>
  </form>;
}

