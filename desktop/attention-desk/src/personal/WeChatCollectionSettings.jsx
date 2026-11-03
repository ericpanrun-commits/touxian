import { useEffect, useRef, useState } from 'react';
import { LoaderCircle, Pause, Play, Plus, RefreshCw, Search, Trash2, X } from 'lucide-react';
import { personalRequest } from './api';
import { Field, SubmitButton } from './Forms';
import { ConfirmDialog } from './Evidence';
import DeskSheet from './DeskSheet';
import { preventImeSubmit } from './formHelpers';
import usePersonalResource from './usePersonalResource';
import { displayWechatTime, WECHAT_STATUS } from './wechatHelpers';
import WeChatCollectorStatus from './WeChatCollectorStatus';
import './wechat.css';

const PRIORITIES = { high: '高', medium: '中', low: '低' };
const GROUP_STATUS = { not_scanned: '等待首次扫描', changed: '已读取新消息', no_change: '没有新消息', partial: '等待继续补扫', error: '读取需要重试', interrupted: '本次扫描已中断' };
const cursorText = cursor => {
  if (!cursor) return '尚未建立';
  try { const values = Object.values(JSON.parse(cursor.shard_watermarks_json || '{}')); return values.length ? values.join(' / ') : cursor.last_local_id ?? '已记录'; }
  catch { return cursor.last_local_id ?? '已记录'; }
};

function GroupSearch({ groups, revision, onSaved, onClose, onBusyChange, onRefresh }) {
  const [query, setQuery] = useState('');
  const [composing, setComposing] = useState(false);
  const [offset, setOffset] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState(null);
  const [priority, setPriority] = useState('medium');
  const pending = useRef(false);
  const input = useRef(null);
  useEffect(() => { input.current?.focus(); }, []);
  useEffect(() => {
    const controller = new AbortController();
    setResult(null); setError('');
    if (!query.trim() || composing) { setLoading(false); return () => controller.abort(); }
    setLoading(true);
    const timer = window.setTimeout(() => {
      personalRequest(`/wechat/chats/search?q=${encodeURIComponent(query.trim())}&offset=${offset}`, { signal: controller.signal })
        .then(data => { if (!controller.signal.aborted) setResult(data); })
        .catch(failure => { if (!controller.signal.aborted) setError(failure.message); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 350);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [query, composing, offset, attempt]);
  const grant = async () => {
    if (pending.current) return;
    pending.current = true; setBusy(true); onBusyChange(true); setError('');
    try {
      await personalRequest('/wechat/chats', { method: 'POST', body: { chatroom_id: selected.chatroom_id, display_name: selected.display_name, priority, expected_revision: revision } });
      onSaved(`已授权「${selected.display_name}」，首次只读取最近 24 小时。`); onClose();
    } catch (failure) { setError(failure.message); setSelected(null); onRefresh(); }
    finally { pending.current = false; setBusy(false); onBusyChange(false); }
  };
  return <>
    <DeskSheet title="添加微信群" onClose={onClose} busy={busy}>
      <p className="personal-meta">先搜索群名，再选择授权。搜索只查看群名和最近活跃时间；加入后才读取正文。</p>
      <div className="wechat-group-search">
        <Field name="group_search" label="搜索微信群"><input ref={input} type="search" onKeyDown={event => { if (event.key === 'Escape' && !composing && !event.nativeEvent.isComposing) { event.preventDefault(); onClose(); } }} maxLength={120} autoComplete="off" value={query} placeholder="输入群名关键词" onCompositionStart={() => setComposing(true)} onCompositionEnd={() => setComposing(false)} onChange={event => { setQuery(event.target.value); setOffset(0); }} /></Field>
        {query ? <button type="button" className="desk-icon-button" aria-label="清空搜索" onClick={() => { setQuery(''); setOffset(0); input.current?.focus(); }}><X size={17} /></button> : null}
      </div>
      <Field name="group_priority" label="新群优先级" help="高：尽量保留通知；中：平衡通知与闲聊；低：减少短闲聊和普通手机推送。"><select value={priority} onChange={event => setPriority(event.target.value)}>{Object.entries(PRIORITIES).map(([value,label]) => <option key={value} value={value}>{label}优先级</option>)}</select></Field>
      {loading ? <p className="wechat-search-message" role="status"><LoaderCircle size={16} className="spin" />正在查找群名…</p> : !query.trim() ? <p className="wechat-search-message"><Search size={18} />输入群名，不会扫描所有群消息。</p> : null}
      {error ? <div role="alert"><p className="personal-field-error">{error}</p><button type="button" className="personal-text-button" disabled={busy} onClick={() => setAttempt(value => value + 1)}>重新搜索</button></div> : null}
      {result && !loading ? <>
        <p className="personal-meta" role="status">{result.items.length ? `找到 ${result.items.length} 个群${result.has_more ? '，还有更多结果' : ''}` : '没有匹配的群，请换一个关键词。'}</p>
        <ul className="wechat-group-results">{result.items.map(item => {
          const granted = groups.some(group => group.chatroom_id === item.chatroom_id);
          return <li key={item.chatroom_id}><div><strong>{item.display_name}</strong><small>{item.chatroom_id}</small>{item.last_activity_at ? <small>最近活跃 · {displayWechatTime(new Date(item.last_activity_at * 1000).toISOString())}</small> : null}</div><button type="button" className={`button ${granted ? 'button-quiet' : 'button-primary'}`} disabled={busy || granted || groups.length >= 30} aria-label={`${granted ? '已授权' : '加入授权'}：${item.display_name}`} onClick={() => { setSelected(item); setError(''); }}>{granted ? '已授权' : '加入'}</button></li>;
        })}</ul>
        {offset > 0 || result.has_more ? <div className="wechat-search-pagination"><button type="button" className="personal-text-button" disabled={!offset || busy} onClick={() => setOffset(Math.max(0, offset - 30))}>上一页</button><span className="personal-meta">第 {Math.floor(offset / 30) + 1} 页</span><button type="button" className="personal-text-button" disabled={!result.has_more || busy} onClick={() => setOffset(offset + 30)}>下一页</button></div> : null}
      </> : null}
      <p className="personal-meta wechat-search-footnote">最多授权 30 个群。原文按群保存，候选事项仍需你确认。</p>
    </DeskSheet>
    {selected ? <ConfirmDialog title={`授权读取「${selected.display_name}」？`} message={`首次只读最近 24 小时，之后增量读取；有关通知会交给 DeepSeek 整理，重要候选可能推送到 iPhone。不会向微信发送消息。\n群 ID：${selected.chatroom_id}`} confirmLabel="加入授权并启用" onConfirm={grant} onCancel={() => setSelected(null)} busy={busy} /> : null}
  </>;
}

function GroupManager({ resource, dirty, onSaved, onBusyChange, locked = false }) {
  const [search, setSearch] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [remove, setRemove] = useState(null);
  const pending = useRef(false);
  const groups = resource.data?.items || [];
  const change = async (group, patch, removing = false) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); onBusyChange(true); setError('');
    try {
      await personalRequest(`/wechat/chats/${group.id}${removing ? '/remove' : ''}`, { method: removing ? 'POST' : 'PATCH', body: { expected_revision: resource.data.revision, ...patch } });
      setRemove(null); onSaved(removing ? `已移除「${group.name}」的读取授权。` : `已更新「${group.name}」的采集设置。`);
    } catch (failure) { setError(failure.message); setRemove(null); resource.retry(); }
    finally { pending.current = false; setBusy(false); onBusyChange(false); }
  };
  return <section className="personal-section wechat-group-manager" aria-label="授权微信群">
    <div className="personal-section-heading"><div><h2>授权微信群</h2><p className="personal-meta">{groups.length} / 30 个 · 各群独立读取与保存进度</p></div><button type="button" className="button button-primary" disabled={busy || locked || dirty || !resource.data || groups.length >= 30} onClick={() => setSearch(true)}><Plus size={16} />添加群</button></div>
    {dirty ? <p className="personal-meta">请先保存下方采集设置，再管理群授权。</p> : null}
    {resource.loading && !resource.data ? <p role="status">正在读取授权群…</p> : null}
    {resource.error ? <div role="alert"><p className="personal-field-error">{resource.error.message}</p><button type="button" className="personal-text-button" onClick={resource.retry}>重新读取授权群</button></div> : null}
    {!groups.length && resource.data ? <div className="wechat-group-empty"><Search size={25} /><h3>从一个群开始</h3><p>搜索并授权后，这里的新通知会进入待确认。</p></div> : null}
    <div className="wechat-group-grid">{groups.map(group => <article key={group.id} className="wechat-group-card" data-group-id={group.id}>
      <header><h3>{group.name}</h3><span className={`wechat-group-badge${group.enabled ? ' is-enabled' : ''}`}>{PRIORITIES[group.priority]}优先级 · {group.enabled ? '已启用' : '已停用'}</span></header>
      <p className="wechat-group-last">上次扫描 · {displayWechatTime(group.last_scan_at)}</p>
      <div className="wechat-group-counts"><span>新增 <strong>{group.messages_new || 0}</strong></span><span>重要事项 <strong>{group.important_new || 0}</strong></span><span>待确认 <strong>{group.pending_review || 0}</strong></span></div>
      <p className={`wechat-group-state${group.last_error || group.analysis?.status === 'error' ? ' is-warning' : ''}`}>{!group.enabled ? '再次启用后继续上次进度' : GROUP_STATUS[group.last_status] || '等待下次扫描'}{group.last_error ? ' · 原文和进度已保留' : group.analysis?.status === 'error' ? ' · AI 整理待重试' : group.analysis?.status === 'budget_deferred' ? ' · 等待 AI 用量恢复' : group.analysis?.status === 'outcome_unknown' ? ' · AI 响应待核对' : ''}</p>
      <footer><button type="button" className="personal-text-button" aria-label={`${group.enabled ? '停用' : '启用'}：${group.name}`} disabled={busy || locked || dirty} onClick={() => change(group, { enabled: !group.enabled })}>{group.enabled ? <Pause size={14} /> : <Play size={14} />}{group.enabled ? '停用' : '启用'}</button><label className="wechat-priority-control"><span className="visually-hidden">{`设置优先级：${group.name}`}</span><select aria-label={`设置优先级：${group.name}`} disabled={busy || locked || dirty} value={group.priority} onChange={event => change(group, { priority: event.target.value })}>{Object.entries(PRIORITIES).map(([value,label]) => <option key={value} value={value}>{label}优先级</option>)}</select></label><button type="button" className="personal-text-button" aria-label={`移除：${group.name}`} disabled={busy || locked || dirty} onClick={() => setRemove(group)}><Trash2 size={14} />移除</button></footer>
      <details className="wechat-group-progress"><summary>查看读取进度</summary><dl><div><dt>群 ID</dt><dd>{group.chatroom_id || '首次扫描后绑定'}</dd></div><div><dt>上次成功</dt><dd>{displayWechatTime(group.last_success_at)}</dd></div><div><dt>分片进度</dt><dd>{cursorText(group.last_cursor)}</dd></div><div><dt>本次候选</dt><dd>{group.candidates_new || 0} 条</dd></div><div><dt>本次 AI 调用</dt><dd>{group.llm_calls || 0} 次</dd></div><div><dt>已过滤闲聊</dt><dd>{group.messages_filtered || 0} 条</dd></div>{group.last_error ? <div><dt>错误</dt><dd>{group.last_error}</dd></div> : null}</dl></details>
    </article>)}</div>
    {error ? <p role="alert" className="personal-field-error">{error}</p> : null}
    {search ? <GroupSearch groups={groups} revision={resource.data.revision} onSaved={onSaved} onClose={() => setSearch(false)} onBusyChange={onBusyChange} onRefresh={resource.retry} /> : null}
    {remove ? <ConfirmDialog title={`移除「${remove.name}」？`} message="将立即停止读取这个群。已保存的原文、候选、正式事项和采集进度会保留，重新授权后可以继续。" confirmLabel="移除授权" busy={busy} onConfirm={() => change(remove, {}, true)} onCancel={() => setRemove(null)} /> : null}
  </section>;
}

function CollectionSettings({ initial, verified, onSaved, onDirtyChange, onBusyChange }) {
  const [document, setDocument] = useState(initial);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [invalid, setInvalid] = useState({});
  const [reloadDialog, setReloadDialog] = useState(false);
  const pending = useRef(false);
  const config = document.wechat;
  const change = patch => { setDocument(current => ({ ...current, wechat: { ...current.wechat, ...patch } })); onDirtyChange(true); };
  const reload = async () => {
    if (pending.current) return;
    pending.current = true; setBusy(true); onBusyChange(true);
    try { const saved = await personalRequest('/wechat/settings'); setDocument(saved); setInvalid({}); setError(''); setReloadDialog(false); onDirtyChange(false); onSaved('已重新读取保存的采集设置。'); }
    catch (failure) { setError(failure.message); setReloadDialog(false); }
    finally { pending.current = false; setBusy(false); onBusyChange(false); }
  };
  const save = async event => {
    event.preventDefault();
    if (pending.current) return;
    const errors = {};
    for (const [key,min,max] of [['http_daily',1,1000],['tokens_daily',4096,10000000]]) if (!Number.isInteger(config.budget[key]) || config.budget[key] < min || config.budget[key] > max) errors[key] = `请填写 ${min}–${max} 之间的整数。`;
    setInvalid(errors);
    if (Object.keys(errors).length) { const details = event.currentTarget.querySelector('details'); if (details) details.open = true; event.currentTarget.elements.namedItem(Object.keys(errors)[0])?.focus(); return; }
    pending.current = true; setBusy(true); onBusyChange(true); setError('');
    try { await personalRequest('/wechat/settings', { method: 'PUT', body: document }); onDirtyChange(false); onSaved('采集设置已保存。'); }
    catch (failure) { setError(failure.message); }
    finally { pending.current = false; setBusy(false); onBusyChange(false); }
  };
  return <><section className="personal-section"><h2>采集计划</h2><form className="personal-form" noValidate onKeyDown={preventImeSubmit} onSubmit={save}>
    <fieldset disabled={busy}><label className="desk-switch-row"><span><strong>自动收集新通知</strong><small>06:00–24:00，每 15 分钟检查；没有新消息不调用 AI。</small></span><input type="checkbox" role="switch" checked={config.enabled} disabled={!verified && !config.enabled} onChange={event => change({ enabled: event.target.checked })} /></label>
      {!verified ? <p className="personal-meta">本机数据库尚未验证，验证后才能开启采集。</p> : null}
      <details className="wechat-advanced"><summary>AI 调用上限</summary><p className="personal-meta">用量上限由所有群共享；达到上限后保留原文，等待后续整理。</p><div className="personal-form-grid">{[['http_daily','每日 AI 请求上限',1,1000],['tokens_daily','每日 token 上限',4096,10000000]].map(([key,label,min,max]) => <Field key={key} name={key} label={label} error={invalid[key]}><input type="number" min={min} max={max} value={config.budget[key]} onChange={event => change({ budget: { ...config.budget, [key]: Number(event.target.value) } })} /></Field>)}</div></details>
    </fieldset>{error ? <div role="alert"><p className="personal-field-error">{error} 当前输入仍保留。</p><button type="button" className="personal-text-button" disabled={busy} onClick={() => setReloadDialog(true)}>重新读取已保存设置</button></div> : null}<SubmitButton busy={busy}>保存采集设置</SubmitButton>
  </form></section>{reloadDialog ? <ConfirmDialog title="放弃当前修改并重新读取？" message="将用已保存的采集设置替换当前输入。" confirmLabel="重新读取已保存设置" onConfirm={reload} onCancel={() => setReloadDialog(false)} busy={busy} /> : null}</>;
}

export default function WeChatCollectionSettings({ onDirtyChange, onBusyChange }) {
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const status = usePersonalResource('/wechat/status', { refreshInterval: 15000 });
  const settings = usePersonalResource('/wechat/settings');
  const groups = usePersonalResource('/wechat/chats', { refreshInterval: 15000, paused: busy });
  const scans = usePersonalResource('/wechat/scans?limit=10', { refreshInterval: 15000 });
  const pending = useRef(false);
  const key = useRef(null);
  const setWorking = value => { setBusy(value); onBusyChange(value); };
  const setChanged = value => { setDirty(value); onDirtyChange(value); };
  const refresh = () => { status.retry(); scans.retry(); groups.retry(); };
  const saved = message => { setNotice(message); refresh(); settings.retry(); };
  const scan = async () => {
    if (pending.current) return;
    pending.current = true; setWorking(true); setError(''); key.current ||= crypto.randomUUID();
    try { await personalRequest('/wechat/scan', { method: 'POST', body: { override_quiet: false }, headers: { 'Idempotency-Key': key.current } }); key.current = null; setNotice('扫描已排队，各群完成后会更新下面的卡片。'); refresh(); }
    catch (failure) { setError(failure.message); }
    finally { pending.current = false; setWorking(false); }
  };
  return <>
    <section className="personal-section wechat-settings-status"><div className="personal-section-heading"><h1>微信采集</h1><div className="personal-form-actions"><button type="button" className="personal-text-button" disabled={busy} onClick={refresh} aria-label="刷新采集状态"><RefreshCw size={15} /></button><button type="button" className="button button-quiet" disabled={busy || !status.data?.runtime_verified || status.data?.quiet || status.data?.status === 'disabled'} onClick={scan}>{busy ? <LoaderCircle size={15} className="spin" /> : <RefreshCw size={15} />}立即扫描</button></div></div><WeChatCollectorStatus resource={status} compact />
      <p className="personal-feedback" role="status">{notice}</p>{error ? <p role="alert" className="personal-field-error">{error}</p> : null}
    </section>
    <GroupManager resource={groups} dirty={dirty} locked={busy} onSaved={saved} onBusyChange={setWorking} />
    {settings.data ? <CollectionSettings key={settings.data.revision} initial={settings.data} verified={Boolean(status.data?.runtime_verified)} onSaved={saved} onDirtyChange={setChanged} onBusyChange={setWorking} /> : settings.loading ? <p role="status">正在读取采集设置…</p> : null}
    {settings.error ? <div role="alert"><p className="personal-field-error">{settings.error.message}</p><button type="button" className="personal-text-button" onClick={settings.retry}>重新读取设置</button></div> : null}
    <section className="personal-section"><details className="wechat-advanced"><summary>最近采集记录</summary>{scans.data?.items.length ? <ul className="personal-event-list">{scans.data.items.map(item => <li className="wechat-scan-row" key={item.id}><strong>{WECHAT_STATUS[item.status] || '扫描未完成'}</strong><span>{displayWechatTime(item.completed_at || item.created_at)}</span><span>新增 {item.stats.messages_new || 0} 条</span>{item.error_code ? <span>已有原文保留，将按计划重试</span> : null}</li>)}</ul> : <p className="personal-meta">尚无采集记录。</p>}</details></section>
  </>;
}
