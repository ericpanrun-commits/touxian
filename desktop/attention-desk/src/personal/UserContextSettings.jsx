import { useRef, useState } from 'react';
import { Field, SubmitButton } from './Forms';
import { personalRequest } from './api';
import usePersonalResource from './usePersonalResource';
import { preventImeSubmit, useDraft } from './formHelpers';
import { splitContextList } from './wechatHelpers';

function ContextForm({ context, onDirtyChange, onBusyChange }) {
  const draft = useDraft({ ...context, courses: context.courses.join('、'), interests: context.interests.join('、'), projects: context.projects.join('、'), wechat_self_aliases: context.wechat_self_aliases.join('、'), entry_year: String(context.entry_year || '') });
  const [roles, setRoles] = useState(Object.entries(context.role_assertions).map(([name, assertion]) => ({ name, value: assertion.confirmed ? assertion.value : 'unknown' })));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const pending = useRef(false);
  const version = useRef(context.profile_version);
  const changeRoles = next => { setRoles(next); onDirtyChange(true); };
  const submit = async event => {
    event.preventDefault(); if (pending.current) return;
    const errors = {};
    if (roles.some(role => !role.name.trim()) || new Set(roles.map(role => role.name.trim())).size !== roles.length) errors.roles = '角色名称请填写完整，且不能重复。';
    if (draft.values.entry_year && (!/^\d{4}$/.test(draft.values.entry_year) || Number(draft.values.entry_year) < 1900 || Number(draft.values.entry_year) > 2200)) errors.entry_year = '请填写四位入学年份，也可以留空。';
    if (!draft.validate(errors)) return;
    pending.current = true; setBusy(true); onBusyChange(true); setError(''); setMessage('');
    try {
      const payload = { ...draft.values, profile_version: version.current, entry_year: draft.values.entry_year ? Number(draft.values.entry_year) : null,
        role_assertions: Object.fromEntries(roles.map(role => [role.name.trim(), { value: role.value, confirmed: role.value !== 'unknown' }])),
        confirmed_fields: ['school', 'college', 'major', 'entry_year', 'class_name'] };
      for (const key of ['courses', 'interests', 'projects', 'wechat_self_aliases']) payload[key] = splitContextList(draft.values[key]);
      const saved = await personalRequest('/wechat/user-context', { method: 'PUT', body: payload });
      version.current = saved.profile_version; onDirtyChange(false); setMessage('画像已保存。历史消息不会自动重新付费分析。');
    } catch (failure) { setError(failure.message); }
    finally { pending.current = false; setBusy(false); onBusyChange(false); }
  };
  return <form className="personal-form" noValidate ref={draft.formRef} onKeyDown={preventImeSubmit} onChange={() => onDirtyChange(true)} onSubmit={submit}>
    <p className="personal-meta">学校与专业的初始值来自现有配置，请核对后保存。未知角色不会被当作“不具有该角色”。</p>
    <fieldset disabled={busy}><div className="personal-form-grid">{[['school', '学校'], ['college', '学院'], ['major', '专业'], ['entry_year', '入学年份'], ['class_name', '班级']].map(([key, label]) => <Field key={key} name={key} label={label} error={draft.errors[key]}><input maxLength={key === 'entry_year' ? 4 : 80} value={draft.values[key]} onChange={event => draft.field(key, event.target.value)} /></Field>)}</div>
      <h3>角色确认</h3><p className="personal-meta">只有你明确确认的“是 / 否”参与排除判断。</p>
      {roles.map((role, index) => <div className="wechat-role-row" key={index}><Field name={`role_${index}`} label="角色名称"><input value={role.name} maxLength={80} onChange={event => changeRoles(roles.map((item, i) => i === index ? { ...item, name: event.target.value } : item))} /></Field><Field name={`role_value_${index}`} label="是否具有这个角色"><select value={role.value} onChange={event => changeRoles(roles.map((item, i) => i === index ? { ...item, value: event.target.value } : item))}><option value="unknown">尚未确认</option><option value="true">已确认：是</option><option value="false">已确认：否</option></select></Field><button type="button" className="personal-text-button" onClick={() => changeRoles(roles.filter((_, i) => i !== index))}>移除此行</button></div>)}
      {draft.errors.roles ? <p role="alert" className="personal-field-error">{draft.errors.roles}</p> : null}
      <button type="button" className="button button-quiet" disabled={roles.length >= 50} onClick={() => changeRoles([...roles, { name: '', value: 'unknown' }])}>添加角色</button>
      <div className="personal-form-grid">{[['courses', '当前课程'], ['interests', '兴趣'], ['projects', '项目'], ['wechat_self_aliases', '本人微信显示名 / 群昵称']].map(([key, label]) => <Field key={key} name={key} label={label} help="多项用顿号分隔，不确定可以留空。"><input value={draft.values[key]} onChange={event => draft.field(key, event.target.value)} /></Field>)}</div>
      <Field name="notes" label="补充说明"><textarea className="resize-none" rows={3} maxLength={1000} style={{ resize: 'none' }} value={draft.values.notes} onChange={event => draft.field('notes', event.target.value)} /></Field>
    </fieldset><div className="personal-feedback" role="status">{message}</div>{error ? <p role="alert" className="personal-field-error">{error}</p> : null}<SubmitButton busy={busy}>保存用户画像</SubmitButton>
  </form>;
}

export default function UserContextSettings(props) {
  const resource = usePersonalResource('/wechat/user-context');
  return <section className="personal-section"><h1>用户画像</h1>{resource.error ? <div role="alert"><p className="personal-field-error">{resource.error.message}</p><button type="button" className="personal-text-button" onClick={resource.retry}>重新读取画像</button></div> : null}{!resource.data && resource.loading ? <p role="status">正在读取画像…</p> : null}{resource.data ? <ContextForm context={resource.data} {...props} /> : null}</section>;
}
