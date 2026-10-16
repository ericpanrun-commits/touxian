import { useRef, useState } from 'react';
import { LoaderCircle, Send } from 'lucide-react';
import { personalRequest } from './api';
import { Field, SubmitButton } from './Forms';
import { preventImeSubmit, useDraft } from './formHelpers';
import { ConfirmDialog } from './Evidence';
import { ReadStatus } from './PlanningViews';
import { officialTime } from './officialHelpers';
import usePersonalResource from './usePersonalResource';
import { BARK_STATUS, DELIVERY_STATUS, barkReason, validQuietTime } from './barkHelpers';
import WorkerStatus from './WorkerStatus';

const RULES = [['critical', '紧急通知'], ['high_relevant', '重要且高相关'], ['vehicle_direct', '车辆工程直接相关'],
  ['college_grade', '学院要求 2026 级执行'], ['academic_core', '核心教务事项']];

function BarkForm({ settings, busy, onSave, onDirtyChange }) {
  const draft = useDraft({ enabled: settings.preference_enabled, ...settings.policy, minimum_relevance: String(settings.policy.minimum_relevance) });
  const { values, field, errors } = draft;
  return <form className="personal-form" ref={draft.formRef} noValidate onKeyDown={preventImeSubmit} onChange={() => onDirtyChange(true)} onSubmit={event => {
    event.preventDefault(); if (busy) return;
    const next = {};
    if (!/^\d{1,3}$/.test(values.minimum_relevance) || Number(values.minimum_relevance) > 100) next.minimum_relevance = '请填写 0—100 的相关度。';
    for (const name of ['quiet_start', 'quiet_end']) if (!validQuietTime(values[name])) next[name] = '请填写 HH:mm，例如 23:30。';
    if (draft.validate(next)) { const { enabled, ...policy } = values; onSave({ enabled, policy: { ...policy, minimum_relevance: Number(policy.minimum_relevance) }, expected_version: settings.version }); }
  }}><fieldset disabled={busy}>
    <label className="personal-checkbox-label"><input type="checkbox" checked={values.enabled} onChange={event => field('enabled', event.target.checked)} />启用 Bark 自动推送</label>
    <p className="personal-meta">需后端已配置并允许 Bark。首次启用跳过全部已有信息，只判断随后新发现的通知。</p>
    <fieldset className="planning-policy-group"><legend>哪些新消息提醒我</legend>{RULES.map(([name, label]) => <label key={name} className="personal-checkbox-label"><input type="checkbox" checked={values[name]} onChange={event => field(name, event.target.checked)} />{label}</label>)}<p className="personal-meta">普通校园新闻、宣传报道、已结束通知和历史信息不弹窗。</p></fieldset>
    <div className="personal-form-grid"><Field name="minimum_relevance" label="最低相关度（0—100）" error={errors.minimum_relevance}><input inputMode="numeric" value={values.minimum_relevance} onChange={event => field('minimum_relevance', event.target.value)} /></Field>
      <Field name="minimum_importance" label="常规推送最低重要程度" help="紧急、专业直推与核心教务开关独立生效。"><select value={values.minimum_importance} onChange={event => field('minimum_importance', event.target.value)}><option value="normal">普通</option><option value="high">高</option><option value="critical">紧急</option></select></Field></div>
    <label className="personal-checkbox-label"><input type="checkbox" checked={values.quiet_enabled} onChange={event => field('quiet_enabled', event.target.checked)} />开启静默时间</label>
    <div className="personal-form-grid"><Field name="quiet_start" label="静默开始" error={errors.quiet_start}><input value={values.quiet_start} onChange={event => field('quiet_start', event.target.value)} placeholder="23:30" /></Field><Field name="quiet_end" label="静默结束" error={errors.quiet_end}><input value={values.quiet_end} onChange={event => field('quiet_end', event.target.value)} placeholder="07:30" /></Field></div>
    <p className="personal-meta">时区：{values.timezone}。静默期间普通通知延迟到结束；紧急通知立即发送。</p>
  </fieldset><div className="personal-form-actions"><SubmitButton busy={busy}>保存手机通知设置</SubmitButton></div></form>;
}

function NotificationHistory({ resource, page, setPage, busy }) {
  const data = resource.data;
  return <section className="personal-section"><div className="personal-section-heading"><h2>最近手机通知</h2><button type="button" className="personal-text-button" disabled={busy || resource.loading} onClick={resource.retry}>刷新通知记录</button></div>
    <ReadStatus resource={resource} label="通知记录" />
    {data?.items.length ? <ul className="personal-event-list">{data.items.map(item => <li className="planning-reminder" key={item.id}><div className="personal-section-heading"><strong>{item.title}</strong><span className="personal-status">{DELIVERY_STATUS[item.status]}</span></div><p className="personal-meta">Bark · {officialTime(item.sent_at || item.failed_at || item.created_at)}{item.attempt_count ? ` · 尝试 ${item.attempt_count}/3 次` : ''}</p>{item.scheduled_at ? <p className="personal-meta">计划发送：{officialTime(item.scheduled_at)}</p> : null}{item.last_error || item.suppressed_reason ? <p className="personal-meta">{barkReason(item.last_error || item.suppressed_reason)}</p> : null}</li>)}</ul> : data ? <p className="personal-empty">暂无通知记录。测试通知独立发送，不占用正式通知去重记录。</p> : null}
    {data ? <div className="personal-form-actions"><button type="button" className="button button-quiet" disabled={busy || resource.loading || page === 0} onClick={() => setPage(value => value - 1)}>上一页</button><span className="personal-meta">第 {page + 1} 页 · 共 {data.total} 条</span><button type="button" className="button button-quiet" disabled={busy || resource.loading || (page + 1) * 20 >= data.total} onClick={() => setPage(value => value + 1)}>下一页</button></div> : null}
  </section>;
}

export default function BarkSettings({ onDirtyChange, onBusyChange }) {
  const resource = usePersonalResource('/notifications/bark/settings');
  const [page, setPage] = useState(0);
  const history = usePersonalResource(`/notifications/bark/history?limit=20&offset=${page * 20}`);
  const [saved, setSaved] = useState(null); const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const [reloadDialog, setReloadDialog] = useState(false);
  const pending = useRef(false); const dirty = useRef(false);
  const settings = saved || resource.data;
  const reportDirty = value => { dirty.current = value; onDirtyChange(value); };
  const run = async action => {
    if (pending.current) return;
    pending.current = true; setBusy(true); onBusyChange(true); setError(''); setNotice('');
    try { await action(); } catch (failure) { setError(failure.message); }
    finally { pending.current = false; setBusy(false); onBusyChange(false); }
  };
  const reload = () => run(async () => { setSaved(await personalRequest('/notifications/bark/settings')); setRevision(value => value + 1); reportDirty(false); setNotice('已读取最新手机通知设置。'); });
  return <><section className="personal-section personal-collection"><div className="personal-section-heading"><h1 tabIndex={-1}>Bark 手机通知</h1><button type="button" className="personal-text-button" disabled={busy || resource.loading} onClick={() => dirty.current ? setReloadDialog(true) : reload()}>重新读取设置</button></div>
    <p className="personal-intro">重要且与你有关的学校新通知，自动送达 iPhone。点击打开官方原文。</p>
    <ReadStatus resource={resource} label="手机通知设置" /><div className="personal-feedback" role="status">{notice}</div>{error ? <p className="personal-field-error" role="alert">{error} 请保留当前输入，检查后重试。</p> : null}
    {settings ? <><p role="status"><strong>{BARK_STATUS[settings.status] || '状态待确认'}</strong></p><p className="personal-meta">Server：{settings.server} · Device Key：{settings.device_key_configured ? '已配置（仅后端保存）' : '未配置'}</p>
      {settings.runtime && !settings.runtime.automatic_bark ? <p className="personal-meta">自动推送当前受运行模式保护而关闭。连接测试仍可独立使用；正式自动推送仅由已验证的云端生产后台执行。</p> : null}
      {!settings.configured ? <p className="personal-meta">在后端 server/.env 填写 BARK_DEVICE_KEY，重启后端后重新读取设置。无需修改代码。</p> : null}
      {!settings.environment_enabled ? <p className="personal-meta">后端尚未启用 Bark。需在后端明确设置 BARK_ENABLED=true，之后才能发送连接测试。</p> : null}
      {settings.configuration_error ? <p role="alert" className="personal-field-error">后端 Bark 配置无效，请核对环境变量。</p> : null}
      <p className="personal-meta">最近成功：{officialTime(settings.last_success_at)}<br />最近失败：{officialTime(settings.last_failure_at)}{settings.last_error ? ` · ${barkReason(settings.last_error)}` : ''}<br />历史保护启用：{officialTime(settings.activation_at)}</p>
      <div className="personal-form-actions"><button type="button" className="button button-quiet" disabled={busy || !settings.configured || !settings.enabled} aria-busy={busy} onClick={() => run(async () => {
        const result = await personalRequest('/notifications/bark/test', { method: 'POST', body: {} });
        if (result.accepted) setNotice(result.message); else setError(result.message);
        const value = await personalRequest('/notifications/bark/settings'); setSaved(value); history.retry();
      })}>{busy ? <LoaderCircle size={16} className="spin" /> : <Send size={16} />}发送测试通知</button></div>
      <p className="personal-meta">测试明确标记 test，每分钟最多一次。连接状态来自最近发送结果，后台不会定时向手机发测试通知。</p>
      <BarkForm key={revision} settings={settings} busy={busy} onDirtyChange={reportDirty} onSave={payload => run(async () => {
        setSaved(await personalRequest('/notifications/bark/settings', { method: 'PUT', body: payload })); setRevision(value => value + 1); reportDirty(false); setNotice('手机通知设置已保存。'); history.retry();
      })} /><p className="personal-meta">{settings.runtime?.automatic_bark ? '自动抓取和推送由云端后台执行，无需保持前端或本地电脑开启。' : '正式自动推送尚未启用，请查看下方后台运行状态。'}</p>
    </> : null}</section><WorkerStatus /><NotificationHistory resource={history} page={page} setPage={setPage} busy={busy} />
    {reloadDialog ? <ConfirmDialog title="读取已保存的手机通知设置？" description="当前未保存的输入将被替换，通知历史保留。" confirmLabel="读取已保存设置" onCancel={() => setReloadDialog(false)} onConfirm={() => { setReloadDialog(false); reload(); }} /> : null}
  </>;
}
