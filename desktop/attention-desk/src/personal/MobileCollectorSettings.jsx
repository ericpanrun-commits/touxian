import { useEffect, useRef, useState } from 'react';
import { RefreshCw, Smartphone, Square } from 'lucide-react';
import { personalRequest } from './api';
import { Field, SubmitButton } from './Forms';
import { ConfirmDialog } from './Evidence';
import { preventImeSubmit, useDraft } from './formHelpers';
import { expiryLabel, qrImagePath, remainingSeconds } from './collectorPairing';

export default function MobileCollectorSettings({ onDirtyChange, onBusyChange, onBack }) {
  const draft = useDraft({ interface_id: '', port: '8473' });
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [mutationError, setMutationError] = useState('');
  const [dialog, setDialog] = useState(null);
  const [reload, setReload] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [now, setNow] = useState(() => performance.now());
  const [qrFailed, setQrFailed] = useState(null);
  const receivedAt = useRef(performance.now());
  const pending = useRef(false);
  const initialized = useRef(false);
  const mutationVersion = useRef(0);
  const heading = useRef(null);
  const applyStatus = next => { receivedAt.current = performance.now(); setNow(receivedAt.current); setStatus(next); };
  useEffect(() => {
    const timer = window.setInterval(() => setNow(performance.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let timer;
    const read = async () => {
      const version = mutationVersion.current;
      try {
        const next = await personalRequest('/collector', { signal: controller.signal });
        if (controller.signal.aborted) return;
        if (pending.current || version !== mutationVersion.current) { timer = window.setTimeout(read, 5000); return; }
        applyStatus(next); setError(''); setLoaded(true);
        if (!initialized.current) {
          initialized.current = true;
          draft.setValues({ interface_id: next.preferred_interface_id || (next.interfaces.length === 1 ? next.interfaces[0].id : ''), port: String(next.port) });
          heading.current?.focus();
        }
      } catch (failure) { if (!controller.signal.aborted) { setError(failure.message); setLoaded(true); } }
      if (!controller.signal.aborted) timer = window.setTimeout(read, 5000);
    };
    read();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [reload]);
  const mutate = async path => {
    if (pending.current) return;
    pending.current = true; mutationVersion.current += 1; setBusy(true); onBusyChange(true); setError(''); setMutationError('');
    try {
      const next = await personalRequest('/collector/' + path, { method: 'POST', ...(path === 'enable' ? { body: { interface_id: draft.values.interface_id || null, port: Number(draft.values.port) } } : {}) });
      applyStatus(next); onDirtyChange(false);
    } catch (failure) { setMutationError(failure.message); }
    finally { pending.current = false; setBusy(false); onBusyChange(false); }
  };
  const submit = event => {
    event.preventDefault();
    const errors = {};
    if (!/^\d+$/.test(draft.values.port) || Number(draft.values.port) < 1024 || Number(draft.values.port) > 65535) errors.port = '请填写 1024—65535 的端口。';
    if ((status?.interfaces.length || 0) > 1 && !draft.values.interface_id) errors.interface_id = '请选择手机与电脑共同连接的物理网络。';
    if (draft.validate(errors)) mutate('enable');
  };
  const selected = status?.interfaces.find(item => item.id === draft.values.interface_id);
  const remaining = remainingSeconds(status, receivedAt.current, now);
  const imagePath = qrImagePath(status);
  const showQr = remaining > 0 && imagePath && qrFailed !== imagePath && !error;
  return <section className="personal-section collector-settings">
    <div className="personal-section-heading"><h1 ref={heading} tabIndex={-1}>手机收集</h1><button type="button" className="button button-quiet" disabled={busy} onClick={onBack}>返回我的事项</button></div>
    <p className="personal-meta">电脑和 iPhone 连接同一 Wi-Fi 后启用。接收的通知会进入现有待确认列表，核对后才创建事项。</p>
    {!loaded ? <p role="status">正在检测可用物理网络…</p> : null}
    {error || mutationError ? <div className="personal-analysis-error" role="alert"><p>{mutationError || error}</p><button type="button" className="button button-quiet" disabled={busy} onClick={() => { setMutationError(''); setReload(value => value + 1); }}><RefreshCw size={16} />重新检测</button></div> : null}
    {status ? <div className="collector-layout"><div>
      <p className="collector-status" role="status"><Smartphone size={18} /><strong>{status.enabled ? '正在接收手机通知' : '手机收集已关闭'}</strong></p>
      <p className="personal-meta">{status.message}</p>
      <form ref={draft.formRef} className="personal-form" noValidate onSubmit={submit} onKeyDown={preventImeSubmit} onChange={() => onDirtyChange(true)}>
        <fieldset disabled={busy || status.enabled}>
          <Field name="interface_id" label="共同连接的网络" error={draft.errors.interface_id} help="启用时重新检测 LAN 地址；只保存接口选择，不保存 IP。"><select value={draft.values.interface_id} onChange={event => draft.field('interface_id', event.target.value)}><option value="">{status.interfaces.length ? '选择物理网络' : '未找到可用物理网络'}</option>{status.interfaces.map(item => <option key={item.id} value={item.id}>{item.name} · {item.address} · {item.profile}</option>)}</select></Field>
          <Field name="port" label="收集端口" error={draft.errors.port}><input inputMode="numeric" value={draft.values.port} onChange={event => draft.field('port', event.target.value)} /></Field>
        </fieldset>
        {!status.enabled ? <><p className="personal-meta">{selected?.profile === 'Public' ? '当前网络为 Public。请在可信的共同 Wi-Fi 环境下手动检查 Private network，再启用。' : selected?.profile !== 'Private' ? '仅允许在已确认的 Private network 上启用。' : '已检测到 Private network。启用后若手机无法访问，请手动检查该网络的入站权限。'}</p><div className="personal-form-actions"><SubmitButton busy={busy}>启用手机收集</SubmitButton><button type="button" className="button button-quiet" disabled={busy} onClick={() => { initialized.current = false; setReload(value => value + 1); }}><RefreshCw size={16} />重新检测</button></div></> : <div className="personal-form-actions"><button type="button" className="button button-quiet" disabled={busy} onClick={() => mutate('stop')}><Square size={16} />停止手机收集</button></div>}
      </form>
      <div className="collector-network-note"><h2>连接检查</h2><p className="personal-meta">网络信息来自 Windows 当前物理网卡。</p><p className="personal-meta">Private 防火墙：{status.firewall?.private_enabled === false ? '关闭' : status.firewall?.private_enabled === true ? '已启用' : '尚未确认'}；当前端口入站允许规则：{status.firewall?.matching_private_allow ? '检测到' : '未确认'}。这里只读取状态，不会修改网络类型或防火墙。</p><p className="personal-meta">若需手动放行，请仅在可信的 Private network 中允许此 Python 程序或所选 TCP 端口，并限定本地子网。Public network 不应开放此入口。网络地址或类型变化时，入口自动停止；重新启用会检测新地址。准备扫码时再生成二维码。</p></div>
    </div><div className="collector-pairing">
      <h2>{status.enabled ? '用 iPhone 相机扫码' : '连接同一 Wi-Fi 后启用'}</h2>
      <div className="collector-qr">{showQr ? <img key={imagePath} width="264" height="264" src={imagePath} onError={() => setQrFailed(imagePath)} alt="手机收集配对二维码；请仅向自己的手机展示" /> : <p>{status.enabled ? (qrFailed === imagePath && imagePath ? '二维码加载失败，请生成新二维码。' : '准备扫码时点击「生成新二维码」。') : '启用后可生成配对二维码'}</p>}</div>
      {status.enabled ? <><p className="personal-meta">LAN：{status.address} · 端口：{status.port}</p><p className="personal-meta">已配对 {status.paired_devices} 台 · {remaining > 0 ? '二维码五分钟内有效；手机确认后才使用一次' : '当前没有可用配对二维码'}</p>{showQr ? <div className="personal-meta collector-qr-time"><p>二维码生成时间：<time dateTime={status.pairing.created_at}>{new Date(status.pairing.created_at).toLocaleTimeString('zh-CN', { hour12: false })}</time></p><p>有效期剩余：<span>{expiryLabel(remaining)}</span></p></div> : null}{!status.diagnostics_ok ? <p role="alert" className="personal-analysis-error">配对诊断暂未保存，请检查本地数据目录的写入权限。</p> : null}<p className="personal-meta">已批准的手机会话最长七天。重新生成二维码保留已配对设备；停止收集、电脑重启或网络变化时需重新配对。</p><div className="personal-form-actions"><button type="button" className="button button-quiet" disabled={busy} onClick={() => remaining <= 0 ? mutate('regenerate') : setDialog({ title: '生成新配对二维码？', message: '旧二维码将失效，已配对的手机继续有效。电脑已保存的通知及正在处理的内容仍保留。', confirmLabel: '重新生成', action: () => mutate('regenerate') })}><RefreshCw size={16} />生成新二维码</button><button type="button" className="button button-quiet" disabled={busy || !status.paired_devices} onClick={() => setDialog({ title: '撤销已配对设备？', message: '所有已配对手机和未使用二维码立即失效。需要使用时再生成二维码。再次使用需重新扫码并确认配对。电脑已保存的内容和正在处理的任务仍保留。', confirmLabel: '撤销已配对设备', action: () => mutate('revoke-devices') })}>撤销已配对设备</button></div></> : null}
      <p className="personal-meta">配对后，手机只能提交内容并查看本次接收状态。手机不提供事项修改、删除、提取设置或原文件下载。</p>
    </div></div> : null}
    {dialog ? <ConfirmDialog {...dialog} onCancel={() => setDialog(null)} onConfirm={() => { const action = dialog.action; setDialog(null); action(); }} /> : null}
  </section>;
}
