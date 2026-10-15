import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, Bookmark, ExternalLink, Globe2, LoaderCircle, RefreshCw, Settings2 } from 'lucide-react';
import { personalRequest } from './api';
import usePersonalResource from './usePersonalResource';
import useOfficialSync from './useOfficialSync';
import OfficialSources, { SyncFeedback } from './OfficialSources';
import { ReadStatus } from './PlanningViews';
import { IMPORTANCE, OFFICIAL_VIEWS, officialLink, officialRoute, officialTime } from './officialHelpers';
import { DELIVERY_STATUS, barkReason } from './barkHelpers';
import './personal.css';
import '../official.css';

function ItemMeta({ item }) {
  return <div className="official-item-meta"><span>{item.published_at || '发布日期未提供'}</span><span>{item.category}</span><span className={`personal-status official-importance-${item.importance}`}>{IMPORTANCE[item.importance]}</span><span>相关度 {item.relevance}%</span><span>{item.is_read ? '已读' : '未读'}</span></div>;
}

function OfficialItemDetail({ id, onBack, onReview }) {
  const resource = usePersonalResource(`/official/items/${id}`);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(null);
  const heading = useRef(null); const state = useRef({ alive: false, pending: false, controller: null });
  const item = resource.data;
  useEffect(() => { const current = state.current; current.alive = true; return () => { current.alive = false; current.controller?.abort(); }; }, []);
  useEffect(() => { document.title = `${item?.title || '官网信息详情'} · Attention Desk`; if (item) heading.current?.focus(); }, [item?.title]);
  const update = async body => {
    const current = state.current; if (current.pending) return;
    current.pending = true; current.controller = new AbortController(); setBusy(true); setError(null);
    try { await personalRequest(`/official/items/${id}`, { method: 'PATCH', body, signal: current.controller.signal }); if (current.alive) resource.retry(); }
    catch (value) { if (current.alive) setError(value); }
    finally { current.pending = false; if (current.alive) setBusy(false); }
  };
  return <section className="personal-section official-reading"><button type="button" className="personal-text-button" onClick={onBack}><ArrowLeft size={15} />返回信息列表</button><ReadStatus resource={resource} label="官网信息" />
    {error ? <p className="personal-field-error" role="alert">{error.message}</p> : null}
    {item ? <><h1 ref={heading} tabIndex={-1}>{item.title}</h1><p className="personal-meta">{item.sources.map(source => source.name).join(' · ')}</p><ItemMeta item={item} />
      <p className="personal-meta">相关依据：{item.reasons.join('、')} · 采集于 {officialTime(item.fetched_at)} · 原文版本 {item.version}</p>
      {item.bark_notification ? <p className="personal-meta">手机通知：{DELIVERY_STATUS[item.bark_notification.status]}{item.bark_notification.sent_at ? ` · ${officialTime(item.bark_notification.sent_at)}` : ''}{item.bark_notification.scheduled_at ? ` · 计划 ${officialTime(item.bark_notification.scheduled_at)}` : ''}{item.bark_notification.last_error || item.bark_notification.suppressed_reason ? ` · ${barkReason(item.bark_notification.last_error || item.bark_notification.suppressed_reason)}` : ''}</p> : null}
      <div className="personal-form-actions"><a className="button button-quiet" href={officialLink(item.url)} target="_blank" rel="noopener noreferrer"><ExternalLink size={16} />打开官网原文</a><button type="button" className="button button-quiet" disabled={busy || resource.loading} aria-pressed={item.is_saved} onClick={() => update({ is_saved: !item.is_saved })}><Bookmark size={16} />{item.is_saved ? '取消收藏' : '收藏'}</button><button type="button" className="button button-quiet" disabled={busy || resource.loading} onClick={() => update({ is_read: !item.is_read })}>{item.is_read ? '标为未读' : '标为已读'}</button><button type="button" className="button button-quiet" onClick={() => onReview(item.raw_snapshot_id)}>核对事项 / 创建提醒</button></div>
      {item.time_evidence.length ? <aside className="official-time-hints"><strong>原文中的时间线索 · 待核对</strong>{item.time_evidence.map(hint => <p key={hint.field}>{({ deadline_at: '截止', event_start_at: '开始', event_end_at: '结束' })[hint.field]}：{officialTime(hint.value)}<br /><span className="personal-meta">{hint.quote}</span></p>)}<p className="personal-meta">日期经你确认后才会进入 Reminder 和 Calendar。</p></aside> : null}
      <div className="official-article-text">{item.content}</div>
      {item.attachments.length ? <section className="official-attachments"><h2>附件（{item.attachments.length}）</h2><ul>{item.attachments.map(attachment => <li key={attachment.url}><a className="personal-text-button" href={officialLink(attachment.url)} target="_blank" rel="noopener noreferrer">{attachment.filename}</a><span className="personal-meta"> · {attachment.extension?.toUpperCase() || '文件'} · 未下载</span></li>)}</ul></section> : null}
      {item.images.length ? <details className="official-run-details"><summary>原文图片（{item.images.length}）</summary><ul>{item.images.map((image, index) => <li key={`${image.url}-${index}`}><a className="personal-text-button" href={officialLink(image.url)} target="_blank" rel="noopener noreferrer">{image.alt || `查看原文图片 ${index + 1}`}</a></li>)}</ul></details> : null}
      {item.references.length > 1 ? <details className="official-run-details"><summary>其他官方引用链接</summary><ul>{item.references.map(ref => <li key={ref.url}><a className="personal-text-button official-source-url" href={officialLink(ref.url)} target="_blank" rel="noopener noreferrer">{ref.url}</a></li>)}</ul></details> : null}
    </> : null}
  </section>;
}

export default function OfficialFeed({ onOpenReviews, onReview }) {
  const [route, setRoute] = useState(() => officialRoute(window.location.search));
  const resource = usePersonalResource(`/official/feed?view=${route.view}&offset=${route.offset}&limit=30`);
  const sync = useOfficialSync(); const heading = useRef(null);
  const navigate = patch => {
    const next = { ...route, ...patch }; const url = new URL(window.location.href);
    url.searchParams.set('official_feed', next.view);
    if (next.sources) url.searchParams.set('official_sources', '1'); else url.searchParams.delete('official_sources');
    if (next.item) url.searchParams.set('official_item', next.item); else url.searchParams.delete('official_item');
    if (next.offset) url.searchParams.set('official_offset', String(next.offset)); else url.searchParams.delete('official_offset');
    window.history.pushState({}, '', url); setRoute(next);
  };
  useEffect(() => { const handler = () => setRoute(officialRoute(window.location.search)); window.addEventListener('popstate', handler); return () => window.removeEventListener('popstate', handler); }, []);
  useEffect(() => { if (!route.item && !route.sources) { document.title = '官网信息 · Attention Desk'; heading.current?.focus(); } }, [route.view, route.item, route.sources]);
  useEffect(() => { if (sync.completed) resource.retry(); }, [sync.completed]);
  if (route.sources) return <OfficialSources sync={sync} onBack={() => navigate({ sources: false })} onOpenReviews={onOpenReviews} />;
  const data = resource.data;
  return <div className="personal-workspace official-information">
    <header className="topbar personal-topbar"><div className="group-context"><Globe2 size={18} /><div><span className="context-label">肇庆学院 · 机械与汽车工程学院</span><strong>官网信息</strong></div></div><div className="topbar-actions"><button type="button" className="button button-quiet" onClick={() => navigate({ sources: true })}><Settings2 size={16} />信息源</button><button type="button" className="button button-primary official-sync-button" disabled={sync.busy || !sync.data?.summary.enabled_count} aria-busy={sync.running} onClick={() => sync.mutate('scan', {})}>{sync.running ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}同步官网信息</button></div></header>
    <div className="personal-content"><SyncFeedback sync={sync} />
      {route.item ? <OfficialItemDetail key={route.item} id={route.item} onBack={() => { navigate({ item: '' }); resource.retry(); }} onReview={onReview} /> : <section className="personal-section"><div className="personal-section-heading"><div><h1 ref={heading} tabIndex={-1}>学校的新消息</h1><p className="personal-intro">官方通知自动汇入。先看与你有关的事，再决定收藏或创建提醒。</p></div><button type="button" className="personal-text-button" disabled={resource.loading} onClick={resource.retry}><RefreshCw size={15} />重新读取</button></div>
        <div className="official-feed-filters" role="group" aria-label="官网信息筛选">{OFFICIAL_VIEWS.map(([view, label]) => <button key={view} type="button" className={`button ${route.view === view ? 'button-primary' : 'button-quiet'}`} aria-pressed={route.view === view} onClick={() => navigate({ view, offset: 0 })}>{label}</button>)}</div>
        <ReadStatus resource={resource} label="官网信息" />
        {data ? <p className="personal-meta" role="status">共 {data.total} 条{data.total ? ` · 当前 ${data.offset + 1}–${data.offset + data.items.length}` : ''} · 内容相同的官方转载合并显示</p> : null}
        {data?.items.length ? <ul className="official-information-list">{data.items.map(item => <li key={item.id}><article className={`official-information-card ${item.is_read ? 'is-read' : ''}`}><h2><button type="button" className="official-title-link" onClick={() => navigate({ item: item.id })}>{item.title}</button></h2><p className="personal-meta">{item.sources.map(source => source.name).join(' · ')}{item.is_saved ? ' · 已收藏' : ''}</p><ItemMeta item={item} /><p className="official-item-summary">{item.summary}</p><button type="button" className="personal-text-button" onClick={() => navigate({ item: item.id })}>阅读详情</button></article></li>)}</ul> : data && !resource.loading ? <div className="personal-empty"><h2>这里暂时没有信息</h2><p>可以切换筛选，或同步官网信息。首次只补全最近 90 天的有限页面，较早的通知不会全部导入。</p></div> : null}
        {data?.total > 30 || route.offset ? <div className="personal-form-actions"><button type="button" className="button button-quiet" disabled={resource.loading || !route.offset} onClick={() => navigate({ offset: Math.max(0, route.offset - 30) })}>上一页</button><button type="button" className="button button-quiet" disabled={resource.loading || !data?.has_more} onClick={() => navigate({ offset: route.offset + 30 })}>下一页</button></div> : null}
      </section>}
    </div>
  </div>;
}
