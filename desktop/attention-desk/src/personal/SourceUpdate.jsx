import { DeadlineForm } from './Forms';

export default function SourceUpdate({ bundle, target, busy, onStart, onCancel, onDirtyChange, onSave }) {
  const comparison = bundle.source_update;
  if (!comparison?.events.length) return null;
  const proposals = comparison.proposals || [];
  const proposal = target && proposals.find(value => value.role === target.deadline.label && value.relation !== 'previous' && value.relation !== 'cancelled');
  const quotes = bundle.extraction?.parsed_output?.evidence || [];
  const quote = proposal && quotes.find(value => proposal.evidence_ids?.includes(value.id));
  const resolved = proposal && !proposal.uncertain && ['date', 'minute'].includes(proposal.precision) && proposal.normalized_date;
  const edited = target && { ...target.deadline, ...(resolved ? { value: proposal.normalized_date, precision: proposal.precision, end_value: null, range_precision: null } : {}),
    original_text: proposal?.original_text || '', confirmed_by_user: false, uncertain: !resolved };
  return <div className="planning-source-update"><h2>来源发生变化 · 核对已有时间节点</h2><p className="personal-meta">新候选不会覆盖已确认的日期。可以更新原有时间节点，保留其身份、提醒历史和原证据。</p>
    {proposals.length ? <ul className="planning-task-summary">{proposals.map((value, index) => <li key={value.id || index}>{value.role}：{value.normalized_date || value.original_text || '待确认'}{value.uncertain ? ' · 日期不确定' : ''}{value.conflicts?.length ? ' · 与已有日期不同，需要确认' : ''}</li>)}</ul> : <p className="personal-meta">尚无新日期候选。可先分析新来源，也可以根据准确引用手动核对。</p>}
    {!target ? comparison.events.map(event => <div key={event.id}><h3>{event.title}</h3>{event.deadlines.map(deadline => <p key={deadline.id} className="personal-meta">{deadline.label} · 已确认：{deadline.value.replace('T', ' ')} <button type="button" className="personal-text-button" disabled={busy || ['done', 'cancelled'].includes(event.status)} onClick={() => onStart({ event, deadline })}>核对并更新这个日期</button>{['done', 'cancelled'].includes(event.status) ? '（请先恢复事项）' : ''}</p>)}</div>) : <>
      <p>正在核对：{target.event.title} / {target.deadline.label}</p><p className="personal-meta">原日期：{target.deadline.value.replace('T', ' ')}。保存前还会展示确认提示。</p>
      <DeadlineForm key={`${target.deadline.id}-${bundle.snapshot.id}`} deadline={edited} evidence={quote ? { quote: quote.quote, block_id: quote.block_id } : null} original={bundle.snapshot.original_text} blocks={bundle.snapshot.blocks} busy={busy} requireEvidence saveLabel="核对并确认来源变更" onDirtyChange={onDirtyChange} onCancel={onCancel} onSave={onSave} />
    </>}
  </div>;
}
