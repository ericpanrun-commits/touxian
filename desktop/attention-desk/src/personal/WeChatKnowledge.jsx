import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, FileImage, Info, LoaderCircle, Pin } from 'lucide-react';
import DeskSheet from './DeskSheet';
import CandidateSheet from './CandidateSheet';
import WeChatEvidence from './WeChatEvidence';
import usePersonalResource from './usePersonalResource';
import { personalRequest } from './api';
import { displayWechatTime } from './wechatHelpers';

const noop = () => {};

function InformationRows({ items, media = false, onSelect }) {
  return <ul className="desk-knowledge-rows">{items.map(item => <li key={item.id}><button type="button" aria-label={`查看信息：${item.analysis.title}`} onClick={() => onSelect(item)}><span className="desk-row-icon">{media ? <FileImage size={18} /> : <Info size={18} />}</span><span>
    <strong>{media ? `${item.attachment_count} 份未解析附件` : item.analysis.title}</strong><small>{item.chat_name} · {displayWechatTime(item.source_at || item.messages?.[0]?.source_time)}{item.pinned_until && new Date(item.pinned_until) > new Date() ? <span className="desk-fyi-pin"><Pin size={12} />已固定</span> : null}{item.fyi_state === 'converted' ? ' · 已转为事项' : item.fyi_state === 'ignored' ? ' · 已忽略' : null}</small><p>{media ? item.attention_reason : item.analysis.summary}</p>
  </span><ArrowUpRight size={15} /></button></li>)}</ul>;
}

function InformationSheet({ item, onClose, busy = false, children }) {
  const disclosure = useRef(null);
  return <DeskSheet title={item.attachment_count ? '未解析附件' : item.analysis.await_more_context ? '暂存的信息' : '值得知道'} onClose={onClose} busy={busy}>
    <p className="personal-meta">{item.chat_name}{item.source_at ? ` · ${displayWechatTime(item.source_at)}` : ''}</p><h3 className="desk-detail-title">{item.attachment_count ? `${item.attachment_count} 份附件` : item.analysis.title}</h3>
    <p className="desk-knowledge-summary">{item.analysis.summary}</p><p className="desk-knowledge-explanation">{item.fyi_state ? item.fyi_state === 'converted' ? '已手动保存为事项，微信来源仍保留。' : '参考信息，无需确认，也不会推送到手机。' : item.attention_reason}</p>
    {item.snapshot_id ? <details ref={disclosure} className="desk-disclosure"><summary>查看原始信息与附件</summary><WeChatEvidence candidate={item} onClose={() => { if (disclosure.current) disclosure.current.open = false; }} /></details> : <pre className="personal-original-text">{item.raw_text}</pre>}{children}
  </DeskSheet>;
}

function HoldingInformation() {
  const [selected, setSelected] = useState(null);
  const mediaArea = useRef(null);
  const focused = useRef(false);
  const resource = usePersonalResource('/wechat/briefing?limit=3', { refreshInterval: 15000, paused: Boolean(selected) });
  const data = resource.data;
  useEffect(() => {
    if (!focused.current && data?.media.total && new URLSearchParams(window.location.search).get('wechat_media') === '1' && mediaArea.current) {
      focused.current = true; mediaArea.current.open = true; mediaArea.current.scrollIntoView({ block: 'center' });
      const url = new URL(window.location.href); url.searchParams.delete('wechat_media'); window.history.replaceState({}, '', url);
    }
  }, [data?.media.total]);
  return <><div className="desk-holding-area" aria-label="暂存信息与附件">
    {data?.unresolved.total ? <details><summary>未确认的信息 · {data.unresolved.total}</summary><p className="personal-meta">先保留上下文，收到明确消息后再判断，不需要你逐条确认。</p><InformationRows items={data.unresolved.items} onSelect={setSelected} /></details> : null}
    {data?.media.total ? <details ref={mediaArea}><summary>未解析附件 · {data.media.total}</summary><p className="personal-meta">连续附件按组保留，后台会自动继续处理。解析出有效内容后再判断是否值得关注。</p><InformationRows items={data.media.items} media onSelect={setSelected} /></details> : null}
  </div>{resource.error ? <p role="alert" className="personal-field-error">暂存信息暂时无法读取。<button type="button" className="personal-text-button" onClick={resource.retry}>重新读取</button></p> : null}
    {selected ? <InformationSheet item={selected} onClose={() => setSelected(null)} /> : null}</>;
}

function KnowledgeFeed({ onCreated, onOpenEvent, onBusyChange }) {
  const [view, setView] = useState('recent');
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState(null);
  const [converting, setConverting] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [undo, setUndo] = useState(null);
  const pending = useRef(false);
  const limit = expanded ? 20 : 3;
  const resource = usePersonalResource(`/wechat/fyi?limit=${limit}&offset=${page * limit}&view=${view}`, { refreshInterval: 15000, paused: Boolean(selected || converting || busy) });
  const data = resource.data;
  useEffect(() => { onBusyChange(Boolean(busy || converting)); return () => onBusyChange(false); }, [busy, converting, onBusyChange]);
  useEffect(() => { if (data && page && page * limit >= data.total) setPage(Math.max(0, Math.ceil(data.total / limit) - 1)); }, [data?.total, page, limit]);
  const changeView = value => { setView(value); setPage(0); setError(''); };
  const select = item => { setSelected(item); setError(''); };
  const mutate = async (item, action) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError('');
    try {
      const result = await personalRequest(`/wechat/fyi/${encodeURIComponent(item.id)}/state`, { method: 'POST', body: { action, expected_version: item.fyi_version } });
      const labels = { archive: '已归档，原文仍保留。', ignore: '已忽略，原文仍保留。', restore: '已恢复到最近信息。', pin: '已固定 30 天。', unpin: '已取消固定。' };
      setNotice(labels[action]); setUndo(['archive', 'ignore'].includes(action) ? result.item : null);
      if (selected?.id === item.id) setSelected(['archive', 'ignore', 'restore'].includes(action) ? null : result.item);
      resource.retry();
    } catch (failure) { setError(failure.message); }
    finally { pending.current = false; setBusy(false); }
  };
  const reread = async () => {
    if (pending.current || !selected) return;
    pending.current = true; setBusy(true);
    try { setSelected(await personalRequest(`/wechat/fyi/${encodeURIComponent(selected.id)}`)); setError(''); resource.retry(); }
    catch (failure) { setError(failure.message); }
    finally { pending.current = false; setBusy(false); }
  };
  const feedback = error ? <p className="personal-field-error" role="alert">{error}{selected ? <button type="button" className="personal-text-button" disabled={busy} onClick={reread}>重新读取此条</button> : null}</p> : null;
  return <><section className="personal-section desk-knowledge" aria-label="值得知道">
    <div className="personal-section-heading"><div><h2>值得知道</h2><p className="personal-meta">{view === 'recent' ? '最近 7 天，留待需要时查阅。' : '已归档的信息，原文仍保留。'}</p></div><div className="wechat-filter" role="group" aria-label="参考信息范围"><button type="button" className={`button ${view === 'recent' ? 'button-primary' : 'button-quiet'}`} aria-pressed={view === 'recent'} onClick={() => changeView('recent')}>最近</button><button type="button" className={`button ${view === 'archived' ? 'button-primary' : 'button-quiet'}`} aria-pressed={view === 'archived'} onClick={() => changeView('archived')}>归档{data?.archived_total ? ` · ${data.archived_total}` : ''}</button></div></div>
    <div role="status" aria-live="polite" className="desk-fyi-feedback">{notice}{undo ? <button type="button" disabled={busy} className="personal-text-button" onClick={() => mutate(undo, 'restore')}>撤销</button> : null}</div>
    {!selected ? feedback : null}{resource.error ? <p role="alert" className="personal-field-error">参考信息暂时无法读取。<button type="button" className="personal-text-button" onClick={resource.retry}>重新读取</button></p> : null}
    {resource.loading && !data ? <p role="status" className="personal-meta"><LoaderCircle size={16} className="spin" />正在读取信息…</p> : null}
    {data?.items.length ? <InformationRows items={data.items} onSelect={select} /> : data ? <p className="desk-inline-empty">{view === 'recent' ? '最近没有新的参考信息。更早的内容保留在归档中。' : '还没有归档信息。'}</p> : null}
    {data && data.total > limit ? !expanded ? <button type="button" className="personal-text-button" onClick={() => { setExpanded(true); setPage(0); }}>查看更多信息<ArrowUpRight size={14} /></button> : <nav className="wechat-pagination" aria-label="参考信息分页"><span>{page * limit + 1}–{Math.min((page + 1) * limit, data.total)} / {data.total}</span><div><button type="button" className="button button-quiet" disabled={!page} onClick={() => setPage(page - 1)}>上一页</button><button type="button" className="button button-quiet" disabled={(page + 1) * limit >= data.total} onClick={() => setPage(page + 1)}>下一页</button></div></nav> : null}
    <p className="personal-meta desk-fyi-retention">{view === 'recent' ? '7 天后自动归档；重要信息可固定 30 天。' : '归档不会删除来源，可以恢复到最近或手动转为事项。'}</p>
  </section>
    {selected ? <InformationSheet item={selected} busy={busy} onClose={() => setSelected(null)}>{feedback}{selected.pinned_until ? <p className="personal-meta">固定至 {displayWechatTime(selected.pinned_until)}</p> : null}
      {selected.fyi_state === 'converted' ? <footer className="desk-sheet-footer"><button type="button" className="button button-primary" onClick={() => onOpenEvent(selected.converted_event_id)}>查看事项</button></footer> : <footer className="desk-sheet-footer desk-knowledge-actions"><div>
        <button type="button" className="button button-quiet" disabled={busy} onClick={() => mutate(selected, selected.pinned_until ? 'unpin' : 'pin')}>{selected.pinned_until ? '取消固定' : '固定 30 天'}</button><button type="button" className="button button-quiet" disabled={busy} onClick={() => mutate(selected, selected.fyi_state === 'active' ? 'archive' : 'restore')}>{selected.fyi_state === 'active' ? '归档' : '恢复到最近'}</button><button type="button" className="personal-text-button" disabled={busy} onClick={() => mutate(selected, 'ignore')}>忽略</button>
      </div><button type="button" className="button button-primary" disabled={busy} onClick={() => { setConverting(selected); setSelected(null); }}>转为事项<ArrowUpRight size={15} /></button></footer>}
    </InformationSheet> : null}
    {converting ? <CandidateSheet candidate={converting} mode="convert" onClose={() => setConverting(null)} onBusyChange={noop} onSaved={noop} onCreated={events => { setConverting(null); resource.retry(); onCreated(events); }} /> : null}
  </>;
}

export default function WeChatKnowledge({ holding = false, onCreated = noop, onOpenEvent = noop, onBusyChange = noop }) {
  return holding ? <HoldingInformation /> : <KnowledgeFeed onCreated={onCreated} onOpenEvent={onOpenEvent} onBusyChange={onBusyChange} />;
}
