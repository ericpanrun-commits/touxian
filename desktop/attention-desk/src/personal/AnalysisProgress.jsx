import { useEffect, useRef, useState } from 'react';
import { personalRequest } from './api';
import { analysisProgress } from './analysisState';

export function AnalysisProgress({ extraction, busy, onCancel }) {
  const p = analysisProgress(extraction);
  if (!p) return null;
  const active = ['running', 'waiting'].includes(extraction.status);
  return <div className="personal-section" aria-label="文档分析进度">
    <p role="status" aria-live="polite">{p.label} · {p.completed} / {p.total} 个区域{p.cached ? ` · ${p.cached} 个复用已保存结果` : ''}</p>
    {active && p.total ? <progress aria-label="已处理区域" max={p.total} value={p.completed} /> : null}
    {p.incomplete ? <p className="personal-meta">{p.incomplete} 个区域未完整分析，结果可能不完整。可重试、分析尚未覆盖区域，或核对后继续人工确认。</p> : null}
    {p.state === 'cancelled' ? <p className="personal-meta">分析已取消；原文和已成功区域仍保留。</p> : null}
    {active ? <button type="button" className="button button-quiet" disabled={busy} onClick={onCancel}>取消分析并保留结果</button> : null}
  </div>;
}

export function AnalysisHistory({ extraction, snapshot, busy, onView }) {
  const [limit, setLimit] = useState(20);
  const [record, setRecord] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const request = useRef(null);
  useEffect(() => () => request.current?.abort(), []);
  const read = async row => {
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setLoading(true); setError('');
    try {
      const value = await personalRequest(`/raw/${snapshot.id}/analysis-chunks/${row.id}`, { signal: controller.signal });
      if (!controller.signal.aborted) setRecord(value);
    } catch (value) { if (!controller.signal.aborted) setError(value.message); }
    finally { if (!controller.signal.aborted) setLoading(false); }
  };
  const labels = { validated: '已校验', failed: '失败', truncated: '截断', split: '已缩小范围继续分析', cancelled: '已取消', running: '分析中' };
  const rows = extraction.chunks || [];
  return <details className="personal-section personal-history"><summary>区域分析记录（{rows.length}）</summary>
    <p className="personal-meta">成功结果保存在本机。重试使用相同原文，已成功区域无需再次付费；改变模型或提取设置可能使缓存失效。</p>
    <ul>{rows.slice(0, limit).map(row => {
      const source = snapshot.blocks?.find(b => b.block_id === row.sources?.[0]?.block_id);
      return <li key={row.id}><p>{source?.filename || '正文'}{source?.page ? ` · 第 ${source.page} 页` : source?.section ? ` · ${source.section}` : ''} · {labels[row.status] || row.status}{row.reused_from_key ? ' · 复用缓存' : ''}</p>
        <p className="personal-meta">尝试 {row.attempt} · {row.chunk_id} · {row.configuration.model} · 输出上限 {row.configuration.max_output_tokens}{row.retry_reason !== 'initial' ? ` · ${row.retry_reason}` : ''}</p>
        {row.error ? <p className="personal-meta">{row.error.message}</p> : null}
        {row.status === 'validated' ? <button type="button" className="personal-text-button" disabled={busy || loading} onClick={() => read(row)}>查看区域候选与依据</button> : null}
      </li>;
    })}</ul>
    {rows.length > limit ? <button type="button" className="button button-quiet" onClick={() => setLimit(current => current + 20)}>加载更多区域记录</button> : null}
    {loading ? <p role="status">读取区域结果…</p> : null}{error ? <p role="alert">{error}</p> : null}
    {record?.parsed_output ? <section aria-label="区域候选"><h2>区域候选</h2>
      {!record.parsed_output.events.length ? <p className="personal-meta">该区域未提取出需要处理的事项，原文仍保留。</p> : null}
      {record.parsed_output.events.map(event => <div key={event.id}><h3>{event.title}</h3><p>{event.summary}</p><p className="personal-meta">{event.deadlines.length} 个日期候选 · {event.actions.length} 个行动候选</p>
        <ul>{event.deadlines.map(date => <li key={date.id}>{date.role}：{date.original_text} · {date.normalized_date || '待确认年份'} · {date.relation}</li>)}</ul>
        <ul>{event.actions.map(action => <li key={action.id}>{action.title}</li>)}</ul>
      </div>)}
      <ul>{record.parsed_output.evidence.map(quote => <li key={quote.id}><button type="button" className="personal-text-button" onClick={() => onView({ ...quote, raw_snapshot_id: snapshot.id, start_offset: quote.start, end_offset: quote.end })}>核对原文：{quote.quote}</button></li>)}</ul>
    </section> : null}
  </details>;
}
