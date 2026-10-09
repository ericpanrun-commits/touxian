import { useEffect, useRef, useState } from 'react';
import { Check, FileText, Image, Link, Smartphone, Upload } from 'lucide-react';
import { Field, SubmitButton } from '../personal/Forms';
import { ConfirmDialog } from '../personal/Evidence';
import { preventImeSubmit, useDraft, validTime } from '../personal/formHelpers';
import { collectorRequest } from './transport';
import { collectorHome, verifyCollectorOrigin } from './pairing';

const MODES = [{ id: 'text', label: '文字', icon: FileText }, { id: 'url', label: '链接', icon: Link }, { id: 'image', label: '截图', icon: Image }, { id: 'file', label: '文件', icon: Upload }];
const TERMINAL = new Set(['needs_review', 'failed']);
const EMPTY = { text: '', url: '', source_name: '', source_time: '' };
// Keep the invitation URL for Code Scanner -> Safari until explicit approval.
// No local/sessionStorage: only the server's HttpOnly cookie persists a device.
const entryToken = new URLSearchParams(window.location.search).get('token');

export default function CollectorApp() {
  const draft = useDraft(EMPTY);
  const { values, errors, field } = draft;
  const [mode, setMode] = useState('text');
  const [files, setFiles] = useState([]);
  const [session, setSession] = useState(null);
  const [connecting, setConnecting] = useState(true);
  const [pairingReady, setPairingReady] = useState(false);
  const [pairingBusy, setPairingBusy] = useState(false);
  const [cookieUnavailable, setCookieUnavailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState(null);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [pollVersion, setPollVersion] = useState(0);
  const submission = useRef(null);
  const pending = useRef(false);
  const request = useRef(null);
  const token = useRef(entryToken);
  const heading = useRef(null);
  const picker = useRef(null);
  const sourceDetails = useRef(null);
  const fileMode = mode === 'file' || mode === 'image';
  const dirty = !receipt?.saved && (files.length > 0 || Object.values(values).some(value => value.trim()));

  const acceptSession = result => {
    token.current = null;
    window.history.replaceState({}, '', collectorHome);
    setPairingReady(false); setSession(result);
    setCookieUnavailable(false);
    heading.current?.focus();
  };
  const connect = async () => {
    if (pending.current) return;
    pending.current = true; setConnecting(true); setError('');
    request.current = new AbortController();
    try {
      const config = await collectorRequest('/collector-config', { signal: request.current.signal });
      verifyCollectorOrigin(config, window.location.origin);
      try {
        acceptSession(await collectorRequest('/api/session', { signal: request.current.signal }));
      } catch (failure) {
        if (failure.code !== 'not_paired' || !token.current) throw failure;
        await collectorRequest('/api/pair/validate', { body: { token: token.current }, signal: request.current.signal });
        setPairingReady(true);
      }
    } catch (failure) { if (failure.code !== 'aborted') setError(failure.message); }
    finally { pending.current = false; setConnecting(false); }
  };
  const confirmPairing = async event => {
    event.preventDefault();
    if (pending.current || !pairingReady || !token.current) return;
    pending.current = true; setPairingBusy(true); setError('');
    request.current = new AbortController();
    try {
      await collectorRequest('/api/pair/confirm', { body: { token: token.current }, signal: request.current.signal });
      // A successful confirmation only proves approval on Windows. Verify the
      // browser actually returns its HttpOnly cookie before showing intake.
      token.current = null;
      window.history.replaceState({}, '', collectorHome);
      setPairingReady(false);
      try {
        acceptSession(await collectorRequest('/api/session', { signal: request.current.signal }));
      } catch (failure) {
        if (failure.code === 'not_paired') {
          setCookieUnavailable(true);
          setError('电脑已批准配对，但当前浏览器没有返回配对 Cookie。请在普通 Safari 中使用；若已阻止所有 Cookie，请检查 Safari 设置后在电脑生成新二维码。');
        } else throw failure;
      }
    } catch (failure) {
      // A lost response may already have set our cookie. Recover only our own
      // approved session; never retry consumption automatically or transfer it.
      if (failure.code === 'pairing_invalid') {
        try { acceptSession(await collectorRequest('/api/session', { signal: request.current.signal })); }
        catch { setPairingReady(false); setError(failure.message); }
      } else if (failure.code !== 'aborted') setError(failure.message);
    } finally { pending.current = false; setPairingBusy(false); }
  };
  useEffect(() => { connect(); return () => request.current?.abort(); }, []);
  useEffect(() => {
    const guard = event => { if (dirty || busy) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [dirty, busy]);
  useEffect(() => {
    if (!receipt || TERMINAL.has(receipt.state)) return undefined;
    const controller = new AbortController();
    let timer;
    const poll = async () => {
      try {
        const result = await collectorRequest(`/api/submissions/${receipt.id}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setReceipt(result); setError('');
        if (!TERMINAL.has(result.state)) timer = window.setTimeout(poll, 2000);
      } catch (failure) { if (!controller.signal.aborted) setError(failure.message); }
    };
    timer = window.setTimeout(poll, 1000);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [receipt?.id, pollVersion]);

  const validate = () => {
    const next = {};
    if (mode === 'text' && !values.text.trim()) next.text = '请粘贴需要收集的通知。';
    if (values.text.length > 100000) next.text = '正文最多 100,000 字，请分开提交。';
    if (mode === 'url') {
      try { const url = new URL(values.url.trim()); if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || values.url.length > 4096) throw new Error(); }
      catch { next.url = '请输入完整的公开 http:// 或 https:// 链接。'; }
    }
    if (fileMode && !files.length) next.files = '请先选择截图或文件。';
    else if (fileMode && (files.length > 10 || files.reduce((size, file) => size + file.size, 0) > 59_000_000)) next.files = '每次最多 10 个文件，合计不超过 59 MB。';
    else if (fileMode && files.some(file => file.size > 50_000_000)) next.files = '单个文件最多 50 MB，请拆分。';
    else if (fileMode && files.some(file => !/\.(png|jpe?g|pdf|docx|txt)$/i.test(file.name))) next.files = '支持 PNG/JPG/JPEG、PDF/DOCX/TXT。HEIC 请转为 JPEG，或上传截图。';
    if (values.source_name.length > 200) next.source_name = '来源名称最多 200 字。';
    if (values.source_time.trim() && !validTime(values.source_time, values.source_time.trim().length === 10 ? 'date' : 'minute')) next.source_time = '请填写 YYYY-MM-DD 或 YYYY-MM-DD HH:mm，也可以留空。';
    if ((next.source_name || next.source_time) && sourceDetails.current) sourceDetails.current.open = true;
    return draft.validate(next);
  };
  const send = async (again = false, confirmLarge = false) => {
    if (pending.current || !session || !validate()) return;
    pending.current = true; setBusy(true); setError(''); setProgress(0);
    request.current = new AbortController();
    const payload = { source_name: values.source_name, source_time: values.source_time.trim() || null, again,
      ...(mode === 'url' ? { url: values.url.trim() } : { text: values.text }), ...(fileMode ? { confirm_large: confirmLarge } : {}) };
    const signature = JSON.stringify({ mode, payload, files: files.map(file => [file.name, file.size, file.lastModified]) });
    try {
      if (submission.current?.signature !== signature || again) {
        const fresh = await collectorRequest('/api/session', { signal: request.current.signal });
        setSession(fresh); submission.current = { signature, id: fresh.submission_id };
      }
      payload.submission_id = submission.current.id;
      let body = payload;
      if (fileMode) {
        body = new FormData();
        Object.entries(payload).forEach(([name, value]) => body.append(name, value ?? ''));
        files.forEach(file => body.append('files', file));
      }
      const result = await collectorRequest(fileMode ? '/api/file' : `/api/${mode}`, { body, csrf: session.csrf, signal: request.current.signal, onProgress: setProgress });
      setReceipt(result); setProgress(null); setPollVersion(version => version + 1);
      heading.current?.focus();
    } catch (failure) {
      if (failure.code !== 'aborted') setError(failure.message);
      if (failure.code === 'not_paired') setSession(null);
    } finally { pending.current = false; setBusy(false); setProgress(null); }
  };
  const submit = event => {
    event.preventDefault();
    if (pending.current || !validate()) return;
    if (fileMode && files.some(file => file.size > 15_000_000)) setDialog({ title: '上传较大的文件？', message: '文件超过 15 MB，上传和本地解析可能需要较长时间。请保持页面打开。', confirmLabel: '确认上传', action: () => send(false, true) });
    else send();
  };
  const reset = (nextMode = mode) => {
    draft.setValues(EMPTY); draft.validate({}); setFiles([]); if (picker.current) picker.current.value = '';
    submission.current = null; setReceipt(null); setError(''); setMode(nextMode); heading.current?.focus();
  };
  const changeMode = next => {
    if (busy || next === mode) return;
    if (dirty) setDialog({ title: '放弃当前输入？', message: '切换类型后将清空当前尚未发送的输入。', confirmLabel: '放弃输入并切换', action: () => reset(next) });
    else reset(next);
  };
  return <main className="mobile-collector">
    <header className="mobile-heading"><span className="context-label"><Smartphone size={16} /> Attention Desk</span><h1 ref={heading} tabIndex={-1}>手机收集</h1><p>把通知送到电脑，在工作台核对并确认。</p></header>
    {connecting ? <section className="personal-section" role="status">正在连接电脑…</section> : !session ? <section className="personal-section mobile-pairing"><h2>{pairingReady ? '在 Safari 中完成配对' : '先与电脑配对'}</h2>
      {pairingReady ? <><p className="mobile-pairing-guidance"><strong>建议在 Safari 中打开后完成配对。</strong></p><p>请使用普通标签页。无痕浏览的配对不会转入普通 Safari，不适合作为日常收集入口。</p><p>如果通过 iPhone「扫码器」打开，请先在菜单中选择「在 Safari 中打开」，再点击下方按钮。扫码器与 Safari 的配对各自独立。</p><p>打开和刷新此页不会用掉二维码。二维码生成后五分钟内有效，点击「完成配对」后才会使用一次。</p><p className="personal-meta">配对后可在七天内刷新、关闭并重新打开 Safari 使用；电脑需保持手机收集开启，网络地址不变。</p><form noValidate onSubmit={confirmPairing}><SubmitButton busy={pairingBusy}>完成配对</SubmitButton></form></> : <><p>在电脑打开「手机收集」，启用后用相机扫描二维码，在普通 Safari 标签页完成配对。电脑与手机需连接同一 Wi-Fi。</p><p>若已在扫码器或无痕浏览中完成配对，请在电脑重新生成二维码，然后在普通 Safari 中打开并完成配对。</p></>}
      {error ? <p className="personal-field-error" role="alert">{error}</p> : null}{!pairingReady ? <button className="button button-primary" type="button" onClick={connect}>重新连接</button> : null}
      {cookieUnavailable ? <p>无痕浏览和扫码器的会话不会自动转入普通 Safari。请勿把新二维码作为日常入口；配对成功后可收藏当前收集页。</p> : null}
    </section> : <>
      <nav className="mobile-modes" aria-label="选择收集类型">{MODES.map(item => <button key={item.id} type="button" aria-pressed={mode === item.id} disabled={busy} onClick={() => changeMode(item.id)}><item.icon size={18} />{item.label}</button>)}</nav>
      <section className="personal-section">
        <form ref={draft.formRef} className="personal-form" noValidate onSubmit={submit} onKeyDown={preventImeSubmit}>
          <fieldset disabled={busy || receipt?.saved}>
            {mode === 'text' ? <Field name="text" label="通知原文" error={errors.text} help="保留原文和换行，电脑会先保存再分析。"><textarea rows={9} className="resize-none" style={{ resize: 'none' }} value={values.text} onChange={event => field('text', event.target.value)} placeholder="在这里粘贴一整条通知" /></Field> : null}
            {mode === 'url' ? <Field name="url" label="公开网页链接" error={errors.url} help="无法读取或需要登录时，仍保存链接；可改发截图或粘贴正文。"><input type="url" inputMode="url" autoCapitalize="none" autoCorrect="off" value={values.url} onChange={event => field('url', event.target.value)} placeholder="https://…" /></Field> : null}
            {fileMode ? <><Field name="files" label={mode === 'image' ? '选择截图或图片' : '选择文件'} error={errors.files} help="PNG/JPG/JPEG、PDF/DOCX/TXT；每次最多 10 个，单个最多 50 MB。"><input ref={picker} type="file" multiple accept={mode === 'image' ? 'image/png,image/jpeg' : '.png,.jpg,.jpeg,.pdf,.docx,.txt'} onChange={event => { setFiles(Array.from(event.target.files || [])); draft.clearError('files'); }} /></Field>{files.length ? <ul className="mobile-file-list">{files.map((file, index) => <li key={`${file.name}-${index}`}><span>{file.name}</span><small>{(file.size / 1000000).toFixed(2)} MB</small></li>)}</ul> : null}<Field name="text" label="补充正文（可选）" error={errors.text}><textarea rows={3} className="resize-none" style={{ resize: 'none' }} value={values.text} onChange={event => field('text', event.target.value)} /></Field></> : null}
            <details ref={sourceDetails} className="mobile-source-details"><summary>来源信息（可选）</summary><Field name="source_name" label="来源名称" error={errors.source_name}><input value={values.source_name} onChange={event => field('source_name', event.target.value)} placeholder="例如：班级群" /></Field><Field name="source_time" label="原消息时间" error={errors.source_time} help="北京时间；不清楚时留空。"><input value={values.source_time} onChange={event => field('source_time', event.target.value)} placeholder="YYYY-MM-DD HH:mm" /></Field></details>
          </fieldset>
          {!receipt?.saved ? <div className="personal-form-actions"><SubmitButton busy={busy}>发送到电脑</SubmitButton></div> : null}
        </form>
        {busy ? <div className="mobile-progress" role="status"><p>{progress === 100 ? '上传完成，等待电脑确认接收…' : `正在上传${progress === null ? '…' : ` · ${progress}%`}`}</p><progress max="100" value={progress ?? undefined} aria-label="上传进度" /></div> : null}
        {error ? <div className="mobile-feedback" role="alert"><p className="personal-field-error">{error}</p>{receipt ? <button className="button button-quiet" type="button" onClick={() => setPollVersion(version => version + 1)}>重新读取接收状态</button> : null}</div> : null}
        {receipt ? <div className="mobile-feedback" role="status" aria-live="polite"><h2>{receipt.state === 'failed' ? (receipt.saved ? '已保存，但分析未完成' : '尚未保存') : receipt.state === 'needs_review' ? '已发送到工作台' : receipt.saved ? '电脑已收到' : '电脑正在接收'}</h2><p>{receipt.phase}</p>{receipt.warning ? <p className="personal-field-error">{receipt.warning}</p> : null}{receipt.duplicate ? <p>这份内容已收到，无需重复发送。</p> : null}{receipt.state === 'needs_review' ? <p><Check size={16} />请在电脑工作台核对并确认。</p> : null}{TERMINAL.has(receipt.state) ? <div className="personal-form-actions"><button className="button button-primary" type="button" onClick={() => reset()}>收集下一条</button><button className="button button-quiet" type="button" onClick={() => setDialog({ title: '作为新通知再提交一次？', message: '这会在电脑上保存一份新的原始记录，并再次尝试分析。', confirmLabel: '再次提交', action: () => send(true, true) })}>仍然再次提交</button></div> : null}</div> : null}
      </section>
      <p className="mobile-footnote">文件保留在电脑，本地解析后的文字按电脑中的提取设置分析。手机页面仅用于收集。可在 Safari 分享菜单中选择「添加到主屏幕」。</p>
    </>}
    {dialog ? <ConfirmDialog {...dialog} onCancel={() => setDialog(null)} onConfirm={() => { const action = dialog.action; setDialog(null); action(); }} /> : null}
  </main>;
}
