import { useRef, useState } from 'react';
import { Field, SubmitButton } from './Forms';
import { preventImeSubmit, useDraft, validTime } from './formHelpers';
import { ConfirmDialog } from './Evidence';

export default function RawCollectionForm({ busy, onSave, onCancel, onDirtyChange }) {
  const draft = useDraft({ original_text: '', title_hint: '', source_time: '' });
  const submission = useRef(null);
  const [files, setFiles] = useState([]);
  const [confirmation, setConfirmation] = useState(null);
  const addFiles = values => { const added = Array.from(values); setFiles(current => [...current, ...added].slice(0, 10)); onDirtyChange(true); };
  const submit = (analyze, confirmed = false) => {
    const next = {};
    if ((!files.length && !draft.values.original_text.trim()) || Array.from(draft.values.original_text).length > 100000) next.original_text = '请粘贴原文或添加文件；文字最多 100,000 字。';
    if (files.some(file => file.size > 50000000) || files.reduce((sum, file) => sum + file.size, 0) > 59000000) next.files = '单文件最多 50 MB；每次文件合计最多 59 MB。请拆分后收集。';
    if (draft.values.title_hint.length > 200) next.title_hint = '标题提示最多 200 字。';
    const time = draft.values.source_time.trim();
    if (time && !validTime(time, time.length === 10 ? 'date' : 'minute')) next.source_time = '请填写 YYYY-MM-DD 或 YYYY-MM-DD HH:mm，也可以留空。';
    if (!busy && draft.validate(next)) {
      if (!confirmed && files.some(file => file.size > 15000000)) { setConfirmation(analyze); return; }
      const payload = { ...draft.values, source_time: time || null };
      const signature = JSON.stringify({ ...payload, files: files.map(file => [file.name, file.size, file.lastModified]) });
      if (submission.current?.signature !== signature) submission.current = { signature, id: crypto.randomUUID() };
      onSave(payload, submission.current.id, analyze, files, confirmed);
    }
  };
  return <form ref={draft.formRef} className="personal-form personal-collection-form" noValidate onKeyDown={preventImeSubmit} onChange={() => onDirtyChange(true)} onSubmit={event => { event.preventDefault(); submit(true); }}>
    <fieldset disabled={busy}>
      <div className="personal-file-drop" onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); if (busy) return; if (event.dataTransfer.files.length) addFiles(event.dataTransfer.files); else { const text = event.dataTransfer.getData('text/plain'); if (text) draft.field('original_text', text); onDirtyChange(true); } }}>
        <Field label="文件或截图（可选）" name="files" help="拖入文件、截图或文字。支持 PDF、DOCX、TXT、PNG、JPG / JPEG；最多 10 个。" error={draft.errors.files}><input type="file" accept=".pdf,.docx,.txt,.png,.jpg,.jpeg" multiple onChange={event => { addFiles(event.target.files); event.target.value = ''; }} /></Field>
        {files.length ? <ul>{files.map((file, index) => <li key={`${file.name}-${index}`}>{file.name} · {(file.size / 1000000).toFixed(2)} MB <button type="button" className="personal-text-button" onClick={() => { setFiles(current => current.filter((_, i) => i !== index)); onDirtyChange(true); }}>移除 {file.name}</button></li>)}</ul> : <p className="personal-meta">也可以把文件拖到这里。</p>}
      </div>
      <Field label="原始通知" name="original_text" help="先保存不可变原文，确认前不会创建正式事项。" error={draft.errors.original_text}><textarea className="resize-none" rows={12} style={{ resize: 'none' }} value={draft.values.original_text} onChange={event => draft.field('original_text', event.target.value)} placeholder="粘贴一整段通知" /></Field>
      <div className="personal-form-grid">
        <Field label="标题提示（可选）" name="title_hint" error={draft.errors.title_hint}><input value={draft.values.title_hint} onChange={event => draft.field('title_hint', event.target.value)} /></Field>
        <Field label="原消息时间（可选）" name="source_time" help="北京时间；不知道时留空，年份或相对日期会提示你确认。" error={draft.errors.source_time}><input placeholder="YYYY-MM-DD HH:mm" value={draft.values.source_time} onChange={event => draft.field('source_time', event.target.value)} /></Field>
      </div>
    </fieldset>
    <p className="personal-meta">文件先保存在本机并解析，截图使用本地 OCR。分析会把原文和提取文字发送到你配置的 DeepSeek 服务；原图不会上传。确认前只生成候选。</p>
    <div className="personal-form-actions"><SubmitButton busy={busy}>{files.length ? '保存文件并解析、分析' : '保存原文并分析'}</SubmitButton><button type="button" className="button button-quiet" disabled={busy} onClick={() => submit(false)}>{files.length ? '保存并本地解析，手动整理' : '保存原文，手动整理'}</button><button type="button" className="button button-quiet" disabled={busy} onClick={onCancel}>返回事项</button></div>
    {confirmation !== null ? <ConfirmDialog title="解析较大的文件？" message="文件超过 15 MB，解析可能需要较长时间。原文件保留在本机；单文件最多 50 MB。" confirmLabel="保存并继续解析" onCancel={() => setConfirmation(null)} onConfirm={() => { const analyze = confirmation; setConfirmation(null); submit(analyze, true); }} /> : null}
  </form>;
}
