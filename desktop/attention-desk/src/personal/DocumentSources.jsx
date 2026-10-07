import { useEffect, useState } from 'react';
import { Field } from './Forms';

export function sourceLabel(source) {
  return [source.filename || (source.source_type === 'web' ? '网页正文' : '粘贴原文'), source.page ? `第 ${source.page} 页` : source.section, source.source_type === 'image_ocr' ? 'OCR · 请核对' : ''].filter(Boolean).join(' · ');
}

export function FileList({ snapshot, busy, onParse }) {
  const labels = { not_downloaded: '未下载', downloaded: '已保存 · 待解析', parsing: '本地解析中', parsed: '已解析', unsupported: '格式不支持', failed: '解析未完成' };
  return snapshot.attachments?.length ? <div className="personal-document-files"><h3>文件与截图</h3><p className="personal-meta">解析在本机完成，原文件保留。点击分析时，只把提取文字发送到 DeepSeek。</p><ul>{snapshot.attachments.map(a => <li key={a.id}>
    <div><strong>{a.filename}</strong><span className="personal-meta"> · {labels[a.parse_status]}{a.version ? ` · 文件第 ${a.version} 版` : ''}{a.size ? ` · ${(a.size / 1000000).toFixed(2)} MB` : ''}</span></div>
    {a.parse_error ? <p className="personal-field-error">{a.parse_error}</p> : null}
    <div className="personal-evidence-links">{a.downloaded_at ? <a className="personal-text-button" href={`/api/personal/attachments/${a.id}/original`}>下载已保存原文件</a> : a.original_url ? <a className="personal-text-button" href={a.original_url} target="_blank" rel="noopener noreferrer">打开官网附件链接</a> : null}
      {onParse ? <button type="button" className="personal-text-button" disabled={busy || a.parse_status === 'parsing'} onClick={() => onParse(a)}>{a.parse_status === 'parsed' && a.original_url ? '重新下载核对版本' : a.parse_status === 'parsed' ? '重新读取已解析内容' : a.downloaded_at ? '解析已保存文件' : '下载并解析'}</button> : null}</div>
  </li>)}</ul>{snapshot.attachment_jobs?.[0]?.error?.filter(e => e.local_saved && !snapshot.attachments.some(a => a.id === e.attachment_id)).map(e => <p key={e.attachment_id} className="personal-field-error">{e.filename}：{e.message} <a className="personal-text-button" href={`/api/personal/attachments/${e.attachment_id}/original`}>下载解析失败的新版本原文件</a></p>)}</div> : null;
}

export function SourceImage({ source }) {
  const [failed, setFailed] = useState(false);
  const path = `/api/personal/attachments/${source.attachment_id}/image${source.page ? `?page=${source.page}` : ''}`;
  useEffect(() => { setFailed(false); }, [path]);
  const box = source.bounding_box;
  return <div className="personal-source-image-wrap">
    {failed ? <p className="personal-field-error" role="alert">原图暂时无法显示，请下载原文件核对。</p> : <div className="personal-source-image"><img src={path} alt={source.page ? `原文件第 ${source.page} 页` : '已保存的原始截图'} onError={() => setFailed(true)} />
      {box ? <span aria-label="引用对应文字区域" className="personal-source-image-mark" style={{ left: `${box.x / box.image_width * 100}%`, top: `${box.y / box.image_height * 100}%`, width: `${box.width / box.image_width * 100}%`, height: `${box.height / box.image_height * 100}%` }} /> : null}</div>}
  </div>;
}

export function SourceBlockPicker({ blocks, value, onChange, name = 'source_block', label = '查看来源位置' }) {
  return blocks?.length ? <Field name={name} label={label}><select value={value || ''} onChange={event => onChange(event.target.value)}><option value="" disabled>选择正文、文件页或段落</option>{blocks.map(b => <option key={b.block_id} value={b.block_id}>{sourceLabel(b)} · {b.text.slice(0, 30)}</option>)}</select></Field> : null;
}
