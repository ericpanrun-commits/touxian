import { useEffect, useId, useRef, useState } from 'react';
import { FileText, X } from 'lucide-react';
import { FileList, SourceBlockPicker, SourceImage, sourceLabel } from './DocumentSources';

export function EvidenceLinks({ evidence, fieldPath, onView }) {
  const matches = evidence.filter((value) => value.field_path === fieldPath);
  return matches.length ? <div className="personal-evidence-links">{matches.map((value, index) =>
    <button type="button" className="personal-text-button" key={value.id} onClick={() => onView(value)} aria-controls="personal-original-notice">
      <FileText size={14} />查看依据{value.page ? ` · 第 ${value.page} 页` : value.section ? ` · ${value.section}` : matches.length > 1 ? ` ${index + 1}` : ''}
    </button>)}</div> : <span className="personal-meta">未绑定原文依据</span>;
}

export function OriginalNotice({ snapshot, selectedEvidence, onClear, onParse, busy }) {
  const region = useRef(null);
  const [viewBlock, setViewBlock] = useState(snapshot.blocks?.[0]?.block_id || '');
  useEffect(() => {
    if (selectedEvidence) {
      region.current?.scrollIntoView({ block: 'nearest', behavior: 'instant' });
      region.current?.focus({ preventScroll: true });
      const mark = region.current?.querySelector('mark');
      const source = region.current?.querySelector('pre');
      if (mark && source) source.scrollTop += mark.getBoundingClientRect().top - source.getBoundingClientRect().top - 24;
    }
  }, [selectedEvidence?.id, selectedEvidence?.quote, selectedEvidence?.start_offset, selectedEvidence?.end_offset, selectedEvidence?.block_id]);
  const block = snapshot.blocks?.find(b => b.block_id === (selectedEvidence?.block_id || viewBlock));
  const words = selectedEvidence && block?.words?.filter(w => w.start < selectedEvidence.end_offset && w.end > selectedEvidence.start_offset).map(w => w.box);
  const quoteBox = words?.length ? { ...words[0], x: Math.min(...words.map(w => w.x)), y: Math.min(...words.map(w => w.y)), width: Math.max(...words.map(w => w.x + w.width)) - Math.min(...words.map(w => w.x)), height: Math.max(...words.map(w => w.y + w.height)) - Math.min(...words.map(w => w.y)) } : null;
  const original = block?.text ?? snapshot.original_text;
  const text = Array.from(original);
  const web = snapshot.web;
  return <section id="personal-original-notice" ref={region} tabIndex={-1} className="personal-section personal-original" aria-label="原始通知与引用依据">
    <div className="personal-section-heading"><h2>原始通知</h2>{selectedEvidence ? <button type="button" className="personal-text-button" onClick={onClear}><X size={14} />清除高亮</button> : null}</div>
    <p className="personal-meta">原文已保存，不可修改。{web ? ` 网站发布时间：${web.published_at || '未提供'}；原文第 ${web.version} 版。发布时间不等于截止日期。` : snapshot.source_time ? ` 原消息时间：${snapshot.source_time.replace('T', ' ')}（北京时间）` : ' 原消息时间未提供。'}</p>
    {web ? <p className="personal-meta"><a className="personal-text-button" href={web.canonical_url} target="_blank" rel="noopener noreferrer">打开官网原页</a> · 抓取于 {new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(web.fetched_at))}（北京时间）</p> : null}
    <FileList snapshot={snapshot} busy={busy} onParse={onParse} />
    {snapshot.attachment_jobs?.[0]?.error?.length ? <div role="alert" className="personal-analysis-error"><strong>上次附件处理未完整完成</strong><ul>{snapshot.attachment_jobs[0].error.map((error, index) => <li key={index}>{error.message}</li>)}</ul><p className="personal-meta">已保存的原文件和已有引用仍保留。</p></div> : null}
    {!snapshot.blocks?.length && web?.images.length ? <p className="personal-meta">正文含 {web.images.length} 张图片，图片内容未识别，请解析原图或打开官网核对。</p> : null}
    {!snapshot.blocks?.length && web?.body_status === 'document_only' ? <p className="personal-meta">该页正文位于嵌入文件中，请下载并解析附件后核对。</p> : null}
    <SourceBlockPicker blocks={snapshot.blocks} value={selectedEvidence?.block_id || viewBlock} onChange={id => { onClear(); setViewBlock(id); }} />
    {block ? <p className="personal-source-badge">{sourceLabel(block)}</p> : null}
    {block?.attachment_id && (block.page || block.source_type === 'image_ocr') ? <SourceImage source={{ ...block, bounding_box: selectedEvidence?.bounding_box || quoteBox || block.bounding_box }} /> : null}
    {selectedEvidence ? <blockquote className="personal-quote" aria-label="当前依据引用">{selectedEvidence.quote}</blockquote> : null}
    <pre className="personal-original-text">{selectedEvidence ? <>
      {text.slice(0, selectedEvidence.start_offset).join('')}<mark>{text.slice(selectedEvidence.start_offset, selectedEvidence.end_offset).join('')}</mark>{text.slice(selectedEvidence.end_offset).join('')}
    </> : original}</pre>
  </section>;
}

export function ConfirmDialog({ title, message, confirmLabel, onConfirm, onCancel }) {
  const dialog = useRef(null);
  const id = useId();
  useEffect(() => {
    const opener = document.activeElement;
    dialog.current.showModal();
    dialog.current.querySelector('button')?.focus();
    return () => {
      dialog.current?.close();
      if (opener?.isConnected) opener.focus();
    };
  }, []);
  return <dialog ref={dialog} className="personal-confirm-dialog" aria-labelledby={`${id}-title`} aria-describedby={`${id}-message`} onCancel={(event) => { event.preventDefault(); onCancel(); }}>
    <h2 id={`${id}-title`}>{title}</h2><p id={`${id}-message`}>{message}</p>
    <div className="personal-form-actions"><button type="button" className="button button-quiet" onClick={onCancel}>继续当前操作</button><button type="button" className="button button-primary" onClick={onConfirm}>{confirmLabel}</button></div>
  </dialog>;
}
