import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Check, LoaderCircle, RefreshCw, RotateCcw } from 'lucide-react';
import usePersonalResource from './usePersonalResource';
import { personalRequest } from './api';
import CandidateSheet from './CandidateSheet';
import WeChatKnowledge from './WeChatKnowledge';
import { displayWechatTime } from './wechatHelpers';

import WeChatCollectorStatus from './WeChatCollectorStatus';

import './wechat.css';

const PAGE_SIZE = 20;

export default function WeChatInbox({ onOpen, onSettings, onCreated, onAll, preview = false, onBusyChange = () => {} }) {
  const params = new URLSearchParams(window.location.search);
  const [filter, setFilter] = useState(!preview && params.get('wechat_status') === 'ignored' ? 'ignored' : 'needs_review');
  const [page, setPage] = useState(preview ? 0 : Math.max(0, Number(params.get('wechat_page')) || 0));
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [undo, setUndo] = useState(null);
  const [sheet, setSheet] = useState(null);
  const pending = useRef(false);
  const status = usePersonalResource('/wechat/status', { refreshInterval: 15000 });
  const pageSize = preview ? 3 : PAGE_SIZE;
  const Heading = preview ? 'h2' : 'h1';
  const candidates = usePersonalResource(`/wechat/candidates?status=${filter}&limit=${pageSize}&offset=${page * pageSize}`, { refreshInterval: 15000, paused: Boolean(busy || sheet) });
  const data = candidates.data;
  const refresh = () => { candidates.retry(); status.retry(); };
  useEffect(() => {
    if (data && page && data.total <= page * pageSize) {
      const lastPage = Math.max(0, Math.ceil(data.total / pageSize) - 1);
      setPage(lastPage);
      const url = new URL(window.location.href);
      if (lastPage) url.searchParams.set('wechat_page', lastPage); else url.searchParams.delete('wechat_page');
      window.history.replaceState({}, '', url);
    }
  }, [data?.total, page]);
  const changeFilter = value => {
    setFilter(value); setPage(0); setError('');
    const url = new URL(window.location.href);
    if (value === 'ignored') url.searchParams.set('wechat_status', value); else url.searchParams.delete('wechat_status');
    url.searchParams.delete('wechat_page'); window.history.replaceState({}, '', url);
  };
  const changePage = value => {
    setPage(value);
    const url = new URL(window.location.href);
    if (value) url.searchParams.set('wechat_page', value); else url.searchParams.delete('wechat_page');
    window.history.replaceState({}, '', url);
  };
  const mutate = async (item, operation) => {
    if (pending.current) return;
    pending.current = true; setBusy(item.id); onBusyChange(true); setError('');
    try { await operation(); }
    catch (failure) { setError(failure.message); }
    finally { pending.current = false; setBusy(null); onBusyChange(false); }
  };
  const ignore = (item, ignored = true) => mutate(item, async () => {
    await personalRequest(`/wechat/candidates/${encodeURIComponent(item.id)}/ignore`, { method: 'POST', body: { ignored, ...(ignored ? { expected_version: item.review_version } : {}) } });
    setNotice(ignored ? `已忽略「${item.analysis.title}」，原文仍保留。` : `已恢复「${item.analysis.title}」到待复核。`);
    setUndo(ignored ? item : null); refresh();
  });
  return <section className="personal-section wechat-inbox" aria-label="微信待确认">
    <div className="personal-section-heading"><div><Heading>{preview ? "微信待确认" : "待确认"}</Heading>{!preview ? <p className="personal-meta">微信里的重要信息，确认后成为你的事项。</p> : null}</div><button type="button" className="personal-text-button" disabled={Boolean(busy)} onClick={onSettings}>采集设置<ArrowRight size={14} /></button></div>
    {!preview ? <details className="desk-disclosure desk-collector-disclosure"><summary>采集状态与来源</summary><WeChatCollectorStatus resource={status} /></details> : null}
    {preview && data?.total ? <button type="button" className="personal-text-button wechat-view-all" onClick={onAll}>查看全部 {data.total} 条待确认通知<ArrowRight size={14} /></button> : null}
    {!preview ? <div className="wechat-inbox-toolbar"><div className="wechat-filter" role="group" aria-label="微信候选状态">
      <button type="button" aria-pressed={filter === 'needs_review'} className={`button ${filter === 'needs_review' ? 'button-primary' : 'button-quiet'}`} disabled={Boolean(busy)} onClick={() => changeFilter('needs_review')}>待确认{filter === 'needs_review' && data ? ` · ${data.total}` : ''}</button>
      <button type="button" aria-pressed={filter === 'ignored'} className={`button ${filter === 'ignored' ? 'button-primary' : 'button-quiet'}`} disabled={Boolean(busy)} onClick={() => changeFilter('ignored')}>已忽略</button>
    </div><button type="button" className="personal-text-button" disabled={Boolean(busy)} onClick={refresh}><RefreshCw size={14} />刷新候选</button></div> : null}
    <div className="wechat-feedback" role="status" aria-live="polite">{notice}{undo ? <button type="button" className="personal-text-button" disabled={Boolean(busy)} onClick={() => ignore(undo, false)}>撤销忽略</button> : null}</div>
    {error || candidates.error ? <div role="alert" className="wechat-inline-error"><p>{error || candidates.error.message}</p><button type="button" className="personal-text-button" onClick={refresh}>重新读取候选</button></div> : null}
    {candidates.loading && !data ? <div className="wechat-inbox-loading" role="status"><LoaderCircle size={18} className="spin" />正在读取已保存的通知…</div> : null}
    {data?.items.length ? <ul className="wechat-candidate-list">{data.items.map(item => {
      const value = item.draft_preview ? { ...item.analysis, ...item.draft_preview } : item.analysis;
      const deadline = item.draft_preview ? value.deadlines?.find(date => date.selected)?.value : value.deadline?.value;
      const unanalysed = value.analysis_origin === 'local_pending';
      const failed = ['error', 'outcome_unknown', 'manual_review'].includes(item.analysis_state?.status);
      const priority = { P0: '紧急', P1: '重要', P2: '一般', P3: '参考' }[value.priority];
      return <li key={item.id}><article className="wechat-candidate" aria-label={`微信候选：${value.title}`}>
        <div className="wechat-candidate-heading"><button type="button" className="desk-candidate-open" onClick={() => setSheet({ item, mode: 'detail' })}><h3>{value.title}</h3></button><span className={`wechat-priority ${value.priority || ''}`}>{unanalysed ? failed ? '待整理' : '整理中' : priority || '待确认'}</span></div>
        <p className="wechat-candidate-summary">{value.summary || '原文已保存，等待整理。'}</p>
        <p className="wechat-candidate-source">{item.chat_name}{item.messages?.[0]?.source_time ? ` · ${displayWechatTime(item.messages[0].source_time)}` : ''}{item.draft_preview ? ' · 已编辑' : ''}</p>
        {item.attachment_count ? <p className="personal-meta desk-attachment-label">包含 {item.attachment_count} 份附件</p> : null}
        {deadline ? <p className="desk-deadline-label">截止 {String(deadline).replace('T', ' ')}</p> : null}
        <div className="wechat-candidate-footer"><span className="personal-meta">{filter === 'ignored' ? '已忽略' : unanalysed ? failed ? '可手动编辑并确认' : '原文已保存' : '待确认'}</span><div className="wechat-candidate-actions">
          {filter === 'ignored' ? <button type="button" className="button button-quiet" disabled={Boolean(busy)} onClick={() => ignore(item, false)}><RotateCcw size={14} />恢复待确认</button> : <>
            <button type="button" className="button button-quiet desk-ignore" disabled={Boolean(busy)} onClick={() => ignore(item)}>忽略</button>
            <button type="button" className="button button-quiet" disabled={Boolean(busy)} onClick={() => setSheet({ item, mode: 'edit' })}>编辑</button>
            <button type="button" className="button button-primary" disabled={Boolean(busy)} onClick={() => setSheet({ item, mode: 'confirm' })}>{busy === item.id ? <LoaderCircle size={15} className="spin" /> : <Check size={15} />}确认</button>
          </>}
        </div></div>
      </article></li>;
    })}</ul> : data ? <div className="wechat-empty"><Check size={24} /><h3>{filter === 'ignored' ? '没有已忽略的通知' : '暂时没有需要确认的事项'}</h3><p>{filter === 'ignored' ? '忽略的通知会保留在这里，随时可以恢复。' : '明确需要行动或影响安排的通知，会出现在这里。'}</p></div> : null}
    {!preview && data && data.total > PAGE_SIZE ? <nav className="wechat-pagination" aria-label="微信候选分页"><span>{page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, data.total)} / {data.total} 条</span><div><button type="button" className="button button-quiet" disabled={page === 0 || Boolean(busy)} onClick={() => changePage(page - 1)}>上一页</button><button type="button" className="button button-quiet" disabled={(page + 1) * PAGE_SIZE >= data.total || Boolean(busy)} onClick={() => changePage(page + 1)}>下一页</button></div></nav> : null}
    {sheet ? <CandidateSheet candidate={sheet.item} mode={sheet.mode} onClose={() => setSheet(null)} onBusyChange={onBusyChange} onSaved={() => { setSheet(null); setNotice('修改已保存，仍在待确认中。'); refresh(); }} onCreated={events => { setSheet(null); setNotice('已确认，已进入正式事项。'); refresh(); onCreated?.(events); }} /> : null}
    {!preview && filter==='needs_review' ? <WeChatKnowledge holding /> : null}
  </section>;
}
