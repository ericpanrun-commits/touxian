import { useEffect, useRef, useState } from 'react';
import { Globe2, LoaderCircle, RefreshCw } from 'lucide-react';
import './personal.css';
import '../official.css';

const labels = { idle: '尚未检查', not_checked: '尚未检查', checking: '检查中', ok: '正常', partial: '部分原文未取得', error: '失败', unreachable: '暂时不可达', unconfigured: '未配置' };
const formatter = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' });
const timestamp = value => value && Number.isFinite(Date.parse(value)) ? formatter.format(new Date(value)) : '尚无记录';

async function request(path, signal, body) {
  const response = await fetch('/api/official/' + path, { signal, ...(body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '来源请求失败，请重新读取状态。');
  return value;
}

export default function OfficialSources({ onOpenReviews }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [readError, setReadError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const lifecycle = useRef({ alive: false, generation: 0, mutation: false, controller: null });
  const heading = useRef(null);
  useEffect(() => { document.title = '官网来源 · Attention Desk'; heading.current?.focus(); }, []);
  useEffect(() => {
    const state = lifecycle.current;
    state.alive = true;
    let timer;
    let cancelled = false;
    const read = async () => {
      if (cancelled) return;
      if (state.mutation) { timer = window.setTimeout(read, 1500); return; }
      const generation = ++state.generation;
      const controller = new AbortController();
      state.controller = controller;
      const timeout = window.setTimeout(() => controller.abort(), 15000);
      let scanning = false;
      try {
        const next = await request('overview', controller.signal);
        if (!cancelled && state.alive && generation === state.generation) { setData(next); setReadError(''); scanning = next.scan.running; }
      } catch (value) {
        if (!cancelled && state.alive && generation === state.generation) setReadError(value.name === 'AbortError' ? '读取来源超时，请重试。' : '无法读取来源状态，请检查连接后重试。');
      } finally {
        window.clearTimeout(timeout);
        if (!cancelled && state.alive) timer = window.setTimeout(read, scanning ? 1500 : 30000);
      }
    };
    read();
    return () => { cancelled = true; state.alive = false; ++state.generation; state.controller?.abort(); window.clearTimeout(timer); };
  }, [retry]);
  const mutate = async (path, body) => {
    const state = lifecycle.current;
    if (state.mutation) return;
    state.mutation = true;
    ++state.generation;
    state.controller?.abort();
    const controller = new AbortController();
    state.controller = controller;
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    setBusy(true); setError(''); setNotice('');
    try {
      const next = await request(path, controller.signal, body);
      if (state.alive) { setData(next); setNotice(path === 'scan' ? '检查已开始。原文保存后进入待核对列表，可以离开此页面。' : '来源设置已保存。'); }
    } catch (value) {
      if (state.alive) setError(value.name === 'AbortError' ? '无法确认请求是否完成，请重新读取来源状态。' : value.message || '来源请求失败，请重试。');
    } finally {
      window.clearTimeout(timeout); state.mutation = false;
      if (state.alive) { setBusy(false); setRetry(value => value + 1); }
    }
  };
  const scanning = data?.scan.running;
  const configured = data?.configuration.status === 'configured';
  return <div className="personal-workspace official-source-management">
    <header className="topbar personal-topbar"><div className="group-context"><div className="context-icon"><Globe2 size={17} /></div><div><span className="context-label">公开官网采集</span><strong>{data?.school || '公开官网'}</strong></div></div><button type="button" className="button button-quiet" onClick={() => onOpenReviews()}>打开统一工作台</button></header>
    <div className="personal-content">
      <section className="personal-section">
        <h1 ref={heading} tabIndex={-1}>官网来源</h1>
        <p className="personal-intro">定期保存通知原文。进入工作台后，可按需使用 DeepSeek 分析、修改预览并确认事项。</p>
        <div className="personal-form-actions"><button type="button" className="button button-primary" disabled={busy || scanning || !configured || !data?.summary.enabled_count} aria-busy={Boolean(scanning)} onClick={() => mutate('scan', {})}>{scanning ? <LoaderCircle size={16} className="spin" /> : <RefreshCw size={16} />}<span>{scanning ? `检查中 ${data.scan.done}/${data.scan.total}` : '检查已启用来源'}</span></button><button type="button" className="button button-quiet" onClick={() => onOpenReviews()}>查看待核对原文{data ? `（${data.summary.pending_count}）` : ''}</button></div>
        <p className="personal-meta" aria-live="polite">{scanning ? `正在检查：${data.scan.current || '准备中'}。` : data ? `已启用 ${data.summary.enabled_count}/${data.summary.source_count} 个来源。每个来源最多检查最近 20 条；默认每 12 小时检查一次。` : '正在读取来源…'}</p>
      </section>
      <div className="personal-feedback" role="status">{notice}</div>
      {error || readError ? <div className="personal-section" role="alert"><p className="personal-field-error">{error || readError}</p><button type="button" className="button button-quiet" disabled={busy} onClick={() => { setError(''); setRetry(value => value + 1); }}>重新读取来源状态</button></div> : null}
      {data?.configuration.error ? <section className="personal-section" role="alert"><h2>来源未配置</h2><p>{data.configuration.error}</p></section> : null}
      {data?.sources.map(source => <section key={source.id} className="personal-section" aria-labelledby={`source-${source.id}`}>
        <div className="personal-section-heading"><div><h2 id={`source-${source.id}`}>{source.name}</h2><p className="personal-meta">{source.department} · {source.category}</p></div><span className={`personal-status ${source.status === 'ok' ? 'done' : 'needs_review'}`}>{labels[source.status] || '待检查'}</span></div>
        <a className="personal-text-button official-source-url" href={source.list_url} target="_blank" rel="noopener noreferrer">{source.list_url}</a>
        <dl className="official-source-facts"><div><dt>最后检查</dt><dd>{timestamp(source.last_checked_at)}</dd></div><div><dt>最后成功</dt><dd>{timestamp(source.last_success_at)}</dd></div><div><dt>本次发现</dt><dd>{source.last_discovered_count} 条新通知 · {source.last_changed_count} 条更新</dd></div><div><dt>已保存</dt><dd>{source.raw_count} 条 · {source.pending_count} 份原文待核对</dd></div></dl>
        {source.error ? <p className="personal-field-error">{source.error}{source.fetch_pending_count ? ` ${source.fetch_pending_count} 条通知已保存地址，正文尚未取得。` : ''} 下次自动检查：{timestamp(source.next_check_at)}。</p> : null}
        <div className="personal-form-actions"><label className="personal-checkbox-label"><input type="checkbox" checked={source.enabled} disabled={busy || scanning} onChange={event => mutate('source', { source_id: source.id, enabled: event.target.checked })} />启用{source.name}</label><button type="button" className="button button-quiet" disabled={busy || scanning || !source.enabled} onClick={() => mutate('scan', { source_ids: [source.id] })}>检查此来源</button><button type="button" className="button button-quiet" onClick={() => onOpenReviews(source.id)}>核对此来源原文</button></div>
      </section>)}
    </div>
  </div>;
}
