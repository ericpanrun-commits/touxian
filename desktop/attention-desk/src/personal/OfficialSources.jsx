import { useEffect, useRef } from 'react';
import { ArrowLeft, Globe2, LoaderCircle, RefreshCw } from 'lucide-react';
import { ReadStatus } from './PlanningViews';
import { HEALTH, PRIORITIES, SOURCE_TYPES, officialLink, officialTime } from './officialHelpers';
import './personal.css';
import '../official.css';

export default function OfficialSources({ sync, onBack, onOpenReviews }) {
  const heading = useRef(null);
  useEffect(() => { document.title = '官网来源 · Attention Desk'; heading.current?.focus(); }, []);
  const data = sync.data;
  return <div className="personal-workspace official-source-management">
    <header className="topbar personal-topbar"><div className="group-context"><Globe2 size={18} /><div><span className="context-label">公开官网采集</span><strong>信息源管理</strong></div></div><button type="button" className="button button-quiet" onClick={onBack}><ArrowLeft size={16} />返回官网信息</button></header>
    <div className="personal-content">
      <section className="personal-section"><div className="personal-section-heading"><div><h1 ref={heading} tabIndex={-1}>官网来源</h1><p className="personal-intro">工作台运行时自动检查；失败后会放缓重试。停用保留已收集的信息。</p></div><button type="button" className="button button-primary official-sync-button" disabled={sync.busy || !data?.summary.enabled_count} aria-busy={sync.running} onClick={() => sync.mutate('scan', {})}>{sync.running ? <LoaderCircle size={16} className="spin" /> : <RefreshCw size={16} />}全部同步</button></div>
        <p className="personal-meta">{data ? `已启用 ${data.summary.enabled_count}/${data.summary.source_count} 个来源。高优先级每 30 分钟；普通每小时；校园新闻每 4 小时。` : '正在读取来源…'}</p>
      </section>
      <SyncFeedback sync={sync} />
      {data?.configuration.error ? <section className="personal-section" role="alert">{data.configuration.error}</section> : null}
      {data?.sources.map(source => <section key={source.id} className="personal-section official-source-card" aria-labelledby={`source-${source.id}`}>
        <div className="personal-section-heading"><div><h2 id={`source-${source.id}`}>{source.name}</h2><p className="personal-meta">{SOURCE_TYPES[source.source_type]} · {source.department} · {PRIORITIES[source.priority]} · 每 {source.fetch_interval / 60} 分钟</p></div><span className={`personal-status ${source.health === 'ok' ? 'done' : 'needs_review'}`}>{HEALTH[source.health] || '待检查'}</span></div>
        <a className="personal-text-button official-source-url" href={officialLink(source.list_url)} target="_blank" rel="noopener noreferrer">{source.list_url}</a>
        <dl className="official-source-facts"><div><dt>最近检查</dt><dd>{officialTime(source.last_checked_at)}</dd></div><div><dt>最近成功</dt><dd>{officialTime(source.last_success_at)}</dd></div><div><dt>最近结果</dt><dd>新增 {source.last_new_count} · 更新 {source.last_changed_count} · 跳过 {source.last_unchanged_count}</dd></div><div><dt>下次检查</dt><dd>{source.enabled ? officialTime(source.next_check_at) : '已停用'}</dd></div></dl>
        {source.error ? <p className="personal-field-error">{source.error} 连续失败 {source.failure_count} 次；已有信息保留。</p> : null}
        {source.disabled_reason ? <p className="personal-meta">{source.disabled_reason}</p> : null}
        <div className="personal-form-actions"><label className="personal-checkbox-label"><input type="checkbox" checked={source.enabled} disabled={sync.busy || Boolean(source.disabled_reason)} onChange={event => sync.mutate('source', { source_id: source.id, enabled: event.target.checked })} />启用{source.name}</label><button type="button" className="button button-quiet" disabled={sync.busy || !source.enabled} onClick={() => sync.mutate('scan', { source_ids: [source.id] })}>立即同步</button><button type="button" className="button button-quiet" onClick={() => onOpenReviews(source.id)}>核对此来源原文</button></div>
        <details className="official-run-details"><summary>最近抓取记录与适配器</summary><p className="personal-meta">{source.adapter_type} · {source.parser_version} · 首次补全：{officialTime(source.backfill_completed_at)} · 已保存 {source.raw_count} 条</p>{data.recent_runs.filter(run => run.source_id === source.id).slice(0, 3).map(run => <p key={run.id} className="personal-meta">{officialTime(run.started_at)} → {run.finished_at ? officialTime(run.finished_at) : '进行中'} · 扫描 {run.scanned} · 发现 {run.discovered} · 新增 {run.inserted} · 更新 {run.updated} · 跳过 {run.skipped} · 失败 {run.failed}{run.error ? ` · ${run.error}` : ''}</p>)}</details>
      </section>)}
    </div>
  </div>;
}

export function SyncFeedback({ sync }) {
  return <><ReadStatus resource={sync} label="来源状态" /><div className="personal-feedback" role="status">{sync.running ? `正在同步 ${sync.data?.scan.done || 0}/${sync.data?.scan.total || 0}：${sync.data?.scan.current || '准备中'}` : sync.notice}</div>{sync.mutationError ? <p className="personal-field-error" role="alert">{sync.mutationError.message}</p> : null}</>;
}
