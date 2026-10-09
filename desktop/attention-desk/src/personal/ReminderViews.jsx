import { useEffect, useRef, useState } from 'react';
import { personalRequest } from './api';
import { Field, SubmitButton } from './Forms';
import { preventImeSubmit, useDraft } from './formHelpers';
import { ReadStatus } from './PlanningViews';
import { displayInstant, REMINDER_STATUS } from './planningHelpers';
import usePersonalResource from './usePersonalResource';
import { ConfirmDialog } from './Evidence';

function SettingsForm({ settings, busy, onSave, onDirtyChange }) {
  const draft = useDraft({ enabled: settings.enabled, date_offsets: settings.date_offsets, minute_offsets: settings.minute_offsets,
    daily_time: settings.daily_time, privacy: settings.privacy, grace_hours: String(settings.grace_hours) });
  const { values, errors, field } = draft;
  const toggle = (name, offset, checked) => field(name, checked ? [...values[name], offset].sort((a, b) => b - a) : values[name].filter(value => value !== offset));
  return <form ref={draft.formRef} className="personal-form" noValidate onKeyDown={preventImeSubmit} onChange={() => onDirtyChange(true)} onSubmit={event => {
    event.preventDefault(); if (busy) return;
    const next = {};
    if (!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(values.daily_time)) next.daily_time = '请填写 HH:mm，例如 09:00。';
    if (!/^\d{1,2}$/.test(values.grace_hours) || Number(values.grace_hours) > 24) next.grace_hours = '补发窗口请填写 0—24 小时。';
    if (draft.validate(next)) onSave({ ...values, grace_hours: Number(values.grace_hours), expected_version: settings.version });
  }}><fieldset disabled={busy}>
    <label className="personal-checkbox-label"><input type="checkbox" checked={values.enabled} onChange={event => field('enabled', event.target.checked)} />开启事项提醒</label>
    <p className="personal-meta">只提醒已经确认且日期明确的时间节点。关闭会取消未来提醒，已触发记录保留。</p>
    <div className="personal-form-grid"><fieldset className="planning-policy-group"><legend>日期型 · 提醒哪几天</legend>{[[3, '提前 3 天'], [1, '提前 1 天'], [0, '当天']].map(([offset, label]) => <label key={offset} className="personal-checkbox-label"><input type="checkbox" checked={values.date_offsets.includes(offset)} onChange={event => toggle('date_offsets', offset, event.target.checked)} />{label}</label>)}</fieldset>
      <fieldset className="planning-policy-group"><legend>精确时间 · 提前多久</legend>{[[24, '提前 24 小时'], [2, '提前 2 小时']].map(([offset, label]) => <label key={offset} className="personal-checkbox-label"><input type="checkbox" checked={values.minute_offsets.includes(offset)} onChange={event => toggle('minute_offsets', offset, event.target.checked)} />{label}</label>)}</fieldset></div>
    <div className="personal-form-grid"><Field name="daily_time" label="日期型提醒时刻" help="属于提醒时刻，不会为原日期补上具体时间。" error={errors.daily_time}><input value={values.daily_time} onChange={event => field('daily_time', event.target.value)} placeholder="HH:mm" /></Field>
      <Field name="grace_hours" label="睡眠 / 重启后补发窗口（小时）" help="默认 6 小时；更早的提醒不会集中补弹。0 表示不补发。" error={errors.grace_hours}><input inputMode="numeric" value={values.grace_hours} onChange={event => field('grace_hours', event.target.value)} /></Field></div>
    <Field name="privacy" label="系统通知内容" help="通知可能显示在锁屏。默认只显示事项标题和时间节点。"><select value={values.privacy} onChange={event => field('privacy', event.target.value)}><option value="title">仅标题 + Deadline</option><option value="full">完整 · 含少量未完成任务</option><option value="hidden">隐藏敏感内容</option></select></Field>
  </fieldset><div className="personal-form-actions"><SubmitButton busy={busy}>保存提醒设置</SubmitButton></div></form>;
}

export function ReminderSettings({ onDirtyChange, onBusyChange }) {
  const resource = usePersonalResource('/reminders/settings');
  const [saved, setSaved] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [reloadDialog, setReloadDialog] = useState(false);
  const [revision, setRevision] = useState(0);
  const dirty = useRef(false);
  const pending = useRef(false);
  const settings = saved || resource.data;
  const reportDirty = value => { dirty.current = value; onDirtyChange(value); };
  const run = async (action) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); onBusyChange(true); setError(''); setNotice('');
    try { await action(); } catch (failure) { setError(failure.message); }
    finally { pending.current = false; setBusy(false); onBusyChange(false); }
  };
  return <section className="personal-section personal-collection"><h1 tabIndex={-1}>提醒与日历设置</h1><p className="personal-intro">后端运行时提醒才有效。关闭网页可以保留后端；完全退出工作台后不保证系统通知。</p>
    <ReadStatus resource={resource} label="提醒设置" /><div className="personal-feedback" role="status">{notice}</div>{error ? <div className="personal-field-error" role="alert"><p>{error} 当前输入保留。</p><button type="button" className="button button-quiet" disabled={busy} onClick={() => setReloadDialog(true)}>重新读取已保存设置</button></div> : null}
    {settings ? <><p className="personal-meta">当前系统时区：{resource.data?.system_timezone}。日期未指定时区时按系统时区解释；已有显式时区会保留。</p>
      <SettingsForm key={`${settings.version}-${revision}`} settings={settings} busy={busy} onDirtyChange={reportDirty} onSave={payload => run(async () => { const value = await personalRequest('/reminders/settings', { method: 'PUT', body: payload }); setSaved(value); reportDirty(false); setNotice('提醒设置已保存。未来提醒已重新计算。'); })} />
      <div className="planning-test-notification"><h2>Windows 通知</h2><p className="personal-meta">发送一条“Attention Desk / 测试提醒”，不创建事项。若系统阻止横幅，请检查 Windows 通知设置；事项提醒仍保留在提醒中心。</p><button type="button" className="button button-quiet" disabled={busy} aria-busy={busy} onClick={() => run(async () => { const result = await personalRequest('/reminders/test', { method: 'POST', body: {} }); setNotice(result.message); })}>发送测试提醒</button></div>
      <div className="planning-startup"><label className="personal-checkbox-label"><input type="checkbox" disabled checked={false} readOnly aria-describedby="planning-startup-note" />随 Windows 启动 Attention Desk</label><p id="planning-startup-note" className="personal-meta">{resource.data?.startup_note}</p></div>
    </> : null}
    {reloadDialog ? <ConfirmDialog title="重新读取提醒设置？" message={dirty.current ? '当前未保存的输入将被替换。已保存的设置、事项和提醒记录保留。' : '将读取后端最新保存的提醒设置。'} confirmLabel="读取已保存设置" onCancel={() => setReloadDialog(false)} onConfirm={() => { setReloadDialog(false); run(async () => { const value = await personalRequest('/reminders/settings'); setSaved(value); setRevision(current => current + 1); reportDirty(false); setNotice('已读取最新保存的提醒设置。'); }); }} /> : null}
  </section>;
}

function ReminderRow({ item, busy, onAction, onOpen }) {
  const [snooze, setSnooze] = useState('10m');
  const active = !['cancelled', 'dismissed', 'dispatching'].includes(item.status);
  return <li className="planning-reminder"><div className="personal-section-heading"><h2><button type="button" className="planning-event-link" onClick={() => onOpen(item.event_id)}>{item.event_title}</button></h2><span className={`personal-status ${item.status === 'failed' ? 'planning-overdue' : ''}`}>{REMINDER_STATUS[item.status]}</span></div>
    <p>{item.deadline_label} · {item.deadline_value?.replace('T', ' ')}{item.overdue ? ` · ${item.relative_label}` : ''}</p><p className="personal-meta">{item.status === 'snoozed' ? '稍后提醒：' : '计划提醒：'}{displayInstant(item.snoozed_until || item.scheduled_at)} · 还有 {item.remaining_task_count} 项任务未完成</p>
    {item.fired_at ? <p className="personal-meta">系统曾接受通知：{displayInstant(item.fired_at)}</p> : null}
    {item.status === 'failed' ? <p role="status" className="personal-field-error">系统通知未送达（第 {item.attempts}/3 次）。{item.next_attempt_at ? `将于 ${displayInstant(item.next_attempt_at)} 有限重试。` : '已停止自动重试，可查看事项或选择稍后提醒。'}{item.result === 'delivery_unknown' ? '上次退出时无法确认是否送达，为避免重复没有自动重发。' : ''}</p> : null}
    <div className="planning-reminder-actions"><button type="button" className="personal-text-button" onClick={() => onOpen(item.event_id)}>查看事项</button>{active ? <><Field name={`snooze_${item.id}`} label="稍后提醒"><select value={snooze} disabled={busy} onChange={event => setSnooze(event.target.value)}><option value="10m">10 分钟后</option><option value="1h">1 小时后</option><option value="tomorrow">明天 · 默认提醒时刻</option></select></Field><button type="button" className="button button-quiet" disabled={busy} onClick={() => onAction(item, snooze)}>稍后提醒</button><button type="button" className="personal-text-button" disabled={busy} onClick={() => onAction(item, 'dismiss')}>关闭本次提醒</button></> : null}</div>
  </li>;
}

export function ReminderCenter({ onOpen, onBusyChange }) {
  const [page, setPage] = useState(0);
  const resource = usePersonalResource(`/reminders?limit=100&offset=${page * 100}`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const pending = useRef(false);
  const onAction = async (item, action) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); onBusyChange(true); setError(''); setNotice('');
    try { await personalRequest(`/reminders/${item.id}/action`, { method: 'POST', body: { action, expected_generation: item.generation } }); setNotice(action === 'dismiss' ? '本次提醒已关闭。事项和任务状态保留。' : '已保存稍后提醒时间。'); resource.retry(); }
    catch (failure) { setError(failure.message); }
    finally { pending.current = false; setBusy(false); onBusyChange(false); }
  };
  return <section className="personal-section"><div className="personal-section-heading"><div><h1 tabIndex={-1}>提醒中心</h1><p className="personal-meta">这里只管理提醒。关闭提醒不会完成事项或任务。</p></div><button type="button" className="personal-text-button" disabled={busy || resource.loading} onClick={resource.retry}>刷新</button></div>
    <ReadStatus resource={resource} label="提醒" /><div className="personal-feedback" role="status">{notice}</div>{error ? <p role="alert" className="personal-field-error">{error}</p> : null}
    {resource.data?.items.length ? <ul className="personal-event-list">{resource.data.items.map(item => <ReminderRow key={item.id} item={item} busy={busy || resource.loading} onAction={onAction} onOpen={onOpen} />)}</ul> : resource.data ? <div className="personal-empty"><h2>暂无提醒记录</h2><p>确认日期后，会按默认策略安排提醒。</p></div> : null}
    {resource.data && (page > 0 || (page + 1) * 100 < resource.data.total) ? <div className="personal-form-actions"><button type="button" className="button button-quiet" disabled={busy || resource.loading || page === 0} onClick={() => setPage(value => value - 1)}>上一页</button><span className="personal-meta">第 {page + 1} 页 · 共 {resource.data.total} 条</span><button type="button" className="button button-quiet" disabled={busy || resource.loading || (page + 1) * 100 >= resource.data.total} onClick={() => setPage(value => value + 1)}>下一页</button></div> : null}
  </section>;
}
