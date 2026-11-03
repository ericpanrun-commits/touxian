import { useState } from 'react';
import { LoaderCircle, X } from 'lucide-react';
import usePersonalResource from './usePersonalResource';
import { OriginalNotice } from './Evidence';

export default function WeChatEvidence({ candidate, onClose }) {
  const source = usePersonalResource(`/raw/${encodeURIComponent(candidate.snapshot_id)}`);
  const [selected, setSelected] = useState(null);
  const snapshot = source.data?.snapshot;
  const cited = new Set(candidate.analysis.evidence_message_ids?.length ? candidate.analysis.evidence_message_ids : (candidate.messages || []).map(message => message.id));
  const blockId = snapshot?.wechat?.messages.find(message => cited.has(message.message_id))?.block_id;
  return <div id={`wechat-evidence-${candidate.id}`} className="wechat-evidence" aria-label={`原始信息：${candidate.analysis.title}`}>
    <div className="personal-section-heading"><h3>原始微信信息</h3><button type="button" className="personal-text-button" onClick={onClose}><X size={14} />收起原文</button></div>
    {source.loading && !snapshot ? <p role="status"><LoaderCircle size={16} className="spin" />正在读取已保存的原文…</p> : null}
    {source.error ? <div role="alert"><p className="personal-field-error">{source.error.message}</p><button type="button" className="button button-quiet" onClick={source.retry}>重新读取原文</button></div> : null}
    {snapshot ? <OriginalNotice snapshot={snapshot} initialBlockId={blockId} selectedEvidence={selected} onClear={() => setSelected(null)} /> : null}
    <aside className="wechat-ai-reason"><h4>分类与来源说明</h4>{candidate.attention_reason ? <p>{candidate.attention_reason}</p> : null}<p>{candidate.analysis.reason || '原文已经保存。'}</p><p className="personal-meta">{candidate.analysis.attention_level==='action' ? '这是 AI 的判断，确认前请核对原文。' : '参考信息和暂存原文不需要逐条确认。'}</p></aside>
  </div>;
}
