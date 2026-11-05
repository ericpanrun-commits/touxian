import { useRef, useState } from 'react';
import { Bell, Eye, EyeOff, Send } from 'lucide-react';
import { Field, SubmitButton } from './Forms';
import { preventImeSubmit, useDraft } from './formHelpers';
import { personalRequest } from './api';
import usePersonalResource from './usePersonalResource';
import { ReadStatus } from './PlanningViews';

function ConnectionForm({ settings, busy, onSave, onDirtyChange }) {
  const draft = useDraft({ enabled: settings.enabled, server: settings.server, device_key: '' });
  const [visible, setVisible] = useState(false);
  return <form ref={draft.formRef} className="personal-form" noValidate onKeyDown={preventImeSubmit} onChange={() => onDirtyChange(true)} onSubmit={event => {
    event.preventDefault(); if (busy) return;
    const errors = {};
    try { const url = new URL(draft.values.server); if (url.protocol !== 'https:' || url.pathname !== '/' || url.search || url.hash || url.username || url.password) errors.server = '请填写 HTTPS 服务根地址，密钥单独填写。'; } catch { errors.server = '请填写有效的 HTTPS 地址。'; }
    if (draft.values.device_key && !/^[A-Za-z0-9_-]{1,256}$/.test(draft.values.device_key)) errors.device_key = '请复制 Bark 的单独 Device Key。';
    if (draft.validate(errors)) onSave({ ...draft.values, expected_version: settings.version });
  }}><fieldset disabled={busy}>
    <label className="desk-switch-row"><span><strong>重要微信通知</strong><small>发现后推送到 iPhone，仍由你确认事项。</small></span><input type="checkbox" role="switch" aria-label="开启 Bark 微信通知" checked={draft.values.enabled} onChange={event => draft.field('enabled', event.target.checked)} /></label>
    <Field name="server" label="Bark Server" error={draft.errors.server}><input autoComplete="off" spellCheck={false} value={draft.values.server} placeholder="https://api.day.app" onChange={event => draft.field('server', event.target.value)} /></Field>
    <div className="desk-secret"><Field name="device_key" label="Device Key" error={draft.errors.device_key} help={settings.device_key_configured ? '密钥已配置。留空继续使用；填写新密钥可替换。' : '从 iPhone 的 Bark 应用复制 Device Key。'}><input autoComplete="new-password" type={visible ? 'text' : 'password'} value={draft.values.device_key} onChange={event => draft.field('device_key', event.target.value)} placeholder={settings.device_key_configured ? '已安全保存' : '输入 Device Key'} /></Field><button type="button" className="desk-icon-button" aria-label={visible ? '隐藏 Device Key' : '显示 Device Key'} aria-pressed={visible} onClick={() => setVisible(value => !value)}>{visible ? <EyeOff size={17} /> : <Eye size={17} />}</button></div>
  </fieldset><div className="personal-form-actions"><SubmitButton busy={busy}>保存通知设置</SubmitButton></div></form>;
}

export default function WeChatBarkSettings({ onDirtyChange, onBusyChange }) {
  const resource = usePersonalResource('/notifications/wechat-bark/settings');
  const [settings, setSettings] = useState(null);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const pending = useRef(false);
  const current = settings || resource.data;
  const run = async action => {
    if (pending.current) return;
    pending.current = true; setBusy(true); onBusyChange(true); setMessage(''); setError('');
    try { await action(); } catch (failure) { setError(failure.message); }
    finally { pending.current = false; setBusy(false); onBusyChange(false); }
  };
  return <section className="personal-section desk-bark-settings"><div className="personal-section-heading"><div><h1 tabIndex={-1}>手机通知</h1><p className="personal-meta">把重要的事，送到你手边。</p></div><Bell size={24} strokeWidth={1.5} /></div><ReadStatus resource={resource} label="手机通知设置" />
    {current ? <><p className="desk-connection-status"><span className={current.device_key_configured ? 'desk-status-dot' : 'desk-status-dot is-off'} />{current.device_key_configured ? `密钥已配置 · ${current.key_source === 'windows' ? 'Windows 加密保护' : '后端环境密钥'}` : '尚未连接 Bark'}</p>
      <ConnectionForm key={revision} settings={current} busy={busy} onDirtyChange={onDirtyChange} onSave={payload => run(async () => { setSettings(await personalRequest('/notifications/wechat-bark/settings', { method: 'PUT', body: payload })); setRevision(value => value + 1); onDirtyChange(false); setMessage('手机通知设置已保存。'); })} />
      <div className="desk-test-notification"><div><strong>试试手机能否收到</strong><p className="personal-meta">自动推送关闭时，也可以单独测试连接。</p></div><button type="button" className="button button-quiet" disabled={busy || !current.device_key_configured} aria-busy={busy} onClick={() => run(async () => { const result = await personalRequest('/notifications/wechat-bark/test', { method: 'POST', body: {} }); if (result.accepted) setMessage(result.message); else setError(result.message); })}><Send size={15} />发送测试通知</button></div>
      <div className="personal-feedback" role="status" aria-live="polite">{message}</div>{error ? <p role="alert" className="personal-field-error">{error}</p> : null}
      <details className="desk-disclosure"><summary>哪些通知会推送</summary><ul className="desk-policy-list"><li>普通通知与资料只保存在 Attention Desk。</li><li>明确与你相关、需要执行的任务或截止事项会推送。</li><li>紧急、当天截止和临时变动使用时效性通知。</li><li>同一候选成功后只推送一次；网络故障最多尝试三次。</li><li>首次启用跳过已有通知；00:00–06:00 暂停自动推送。</li></ul><p className="personal-meta">推送只提醒发现了候选。打开 Attention Desk 确认后，才会进入正式事项。设备密钥不会返回页面，也不会进入普通日志。</p></details>
    </> : null}</section>;
}
