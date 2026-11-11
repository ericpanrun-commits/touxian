import { useEffect, useRef, useState } from 'react';
import { FileInput, Plus } from 'lucide-react';
import DeskSheet from './DeskSheet';
import { Field, SubmitButton } from './Forms';
import { ConfirmDialog, OriginalNotice } from './Evidence';
import { SourceImage } from './DocumentSources';
import CourseField from './CourseField';
import usePersonalResource from './usePersonalResource';
import { personalRequest, personalFileUrl } from './api';
import { preventImeSubmit, useDraft } from './formHelpers';

const TYPES = { text: '文字', image: '图片', pdf: 'PDF', file: '文件', url: '链接' };
const STATES = { received: '已接收', extracting: '本地解析中', classifying: '整理中', analyzing: '分析中', needs_review: '待核对', completed: '已完成', failed: '处理失败', recovery_needed: '需恢复', needs_manual_content: '待补充正文' };
const NOTE_STATES = { needs_context: '待确认信息', paused: '已暂停', failed: '处理失败', completed: '分析已完成', needs_review: '待核对', processing: '分析中' };
const stateLabel = item => (item.note_processing?.mode === 'note-course-tags-v1' ? ({completed:'已分类',needs_context:'待确认',processing:'分类中'})[item.note_processing.status] : null) || NOTE_STATES[item.note_processing?.status] || STATES[item.processing_status] || item.processing_status;
const pdfAttachment = item => item.input_kind === 'pdf' ? item.attachments?.find(a=>a.extension==='pdf'||a.mime_type==='application/pdf') : null;
const date = value => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
const readUrl = () => {
  const q = new URLSearchParams(location.search), p = Number(q.get('inbox_page'));
  return { status: ['all', 'processing', 'needs_review', 'completed', 'archived'].includes(q.get('inbox_status')) ? q.get('inbox_status') : 'all',
    page: Number.isSafeInteger(p) && p >= 0 ? Math.min(p, 1000000) : 0, scope: q.get('inbox_scope') === 'all' ? 'all' : 'submitted', id: q.get('inbox_item') || null };
};

function ReadError({ resource }) { return resource.error ? <div className="personal-field-error" role="alert">{resource.error.message} <button type="button" className="personal-text-button" onClick={resource.retry}>重新读取</button></div> : null; }

export default function EricInbox({ onNavigate, onBusyChange, onDirtyChange, initialAdd = false, cloud = false, onAdd }) {
  const [view, setView] = useState(readUrl);
  const [adding, setAdding] = useState(Boolean(initialAdd));
  const [notice, setNotice] = useState('');
  const list = usePersonalResource(`/inbox?status=${view.status}&scope=${view.scope}&offset=${view.page * 20}&limit=20`, { refreshInterval: 10000, paused: adding || Boolean(view.id) });
  const change = next => {
    const value = { ...view, ...next };
    const u = new URL(location.href);
    for (const [key, data] of Object.entries({ inbox_status: value.status, inbox_page: value.page, inbox_scope: value.scope, inbox_item: value.id })) {
      if (data === null || data === '' || data === 0) u.searchParams.delete(key); else u.searchParams.set(key, String(data));
    }
    history.pushState({}, '', u); setView(value);
  };
  const openNote = async item => {
    const attachment=pdfAttachment(item);
    if(!attachment){change({id:item.id});return;}
    // Open synchronously from the click; resolve only this file's existing private URL.
    const tab=window.open('about:blank','_blank');
    if(!tab){setNotice('浏览器阻止了打开原件，请允许新窗口，或从分类与详情打开。');return;}
    tab.opener=null;tab.document.title='正在打开原始 PDF';tab.document.body.textContent='正在打开原始 PDF…';
    try {const url=await personalFileUrl(`/api/personal/attachments/${attachment.id}/original?inline=true`);tab.location.replace(url);}
    catch {tab.close();setNotice('原始 PDF 暂时没有打开，请稍后重试；已保存的笔记仍在。');}
  };
  useEffect(() => { const back = () => setView(readUrl()); window.addEventListener('popstate', back); return () => window.removeEventListener('popstate', back); }, []);
  useEffect(() => { if (list.data && view.page && view.page * 20 >= list.data.total) change({ page: Math.floor(Math.max(0, list.data.total - 1) / 20) }); }, [list.data?.total, view.page]);
  return <section className="personal-section eric-inbox">
    <div className="personal-section-heading"><div>{cloud?null:<p className="personal-meta">统一输入 · 原件先保存</p>}{cloud?<h2>已接收的内容</h2>:<h1 tabIndex={-1}>Eric Inbox</h1>}</div><button type="button" className="button button-primary" onClick={() => onAdd?onAdd():setAdding(true)}><Plus size={16} />添加输入</button></div>
    <p><button type="button" className="personal-text-button" onClick={() => onNavigate('learning')}>Learning · 看看接下来学什么</button></p>
    <p className="personal-meta">文字、截图、PDF、文件和链接都从这里收好。自动采集的微信与官网记录可切换到“全部来源”查看。</p>
    <div className="eric-inbox-filters"><div className="planning-views" role="group" aria-label="Inbox 状态">{[['all', '全部'], ['processing', '处理中'], ['needs_review', '待核对'], ['completed', '已完成'], ...(cloud?[['archived','已归档']]:[])].map(([id, label]) => <button type="button" key={id} className={`button ${view.status === id ? 'button-primary' : 'button-quiet'}`} aria-pressed={view.status === id} onClick={() => change({ status: id, page: 0 })}>{label}</button>)}</div>
      <Field label="来源范围" name="inbox_scope"><select value={view.scope} onChange={e => change({ scope: e.target.value, page: 0 })}><option value="submitted">主动提交</option><option value="all">全部来源</option></select></Field></div>
    <p className="eric-inbox-feedback" role="status" aria-live="polite">{notice}</p>
    <ReadError resource={list} />
    {list.loading ? <p className="personal-meta" role="status">正在读取已保存的输入…</p> : null}
    {list.data ? <><p className="personal-meta">共 {list.data.total} 条{list.data.total ? ` · 当前 ${view.page * 20 + 1}–${Math.min((view.page + 1) * 20, list.data.total)}` : ''}</p>
      {!list.data.items.length ? <div className="personal-empty"><FileInput size={25} /><h2>{view.status === 'all' ? '先把东西交给 Inbox' : '这个状态下暂无输入'}</h2><p>添加一段文字，或上传手边的材料。保存后就能在这里找到。</p></div> : <ul className="personal-event-list eric-inbox-list">{list.data.items.map(item => <li key={item.id}><button type="button" className="personal-event-row" onClick={() => void openNote(item)}>
        <span className="eric-inbox-row-content"><small>{TYPES[item.input_kind]} · {item.source} · {date(item.created_at)}</small><strong>{item.title}</strong><span className="eric-inbox-preview">{item.preview}</span><small>{item.course?.name || '未关联课程'}{item.project_ref ? ` · ${item.project_ref}` : ''}{item.proposal_count ? ` · ${item.proposal_count} 个行动提案` : ''}{item.needs_manual ? ' · 需要核对' : ''}</small>{item.classification?.note_classification?.tags?.length ? <small>{item.classification.note_classification.tags.join(' · ')}</small> : null}</span><span className={`personal-status ${item.needs_manual ? 'needs_review' : item.processing_status === 'completed' ? 'done' : ''}`}>{stateLabel(item)}</span>
      </button>{pdfAttachment(item)?<button type="button" className="personal-text-button" onClick={()=>change({id:item.id})}>分类与详情</button>:null}</li>)}</ul>}
      <div className="personal-form-actions"><button type="button" className="button button-quiet" disabled={!view.page || list.loading} onClick={() => change({ page: view.page - 1 })}>上一页</button><button type="button" className="button button-quiet" disabled={(view.page + 1) * 20 >= list.data.total || list.loading} onClick={() => change({ page: view.page + 1 })}>下一页</button></div>
    </> : null}
    {adding ? <InboxInput onBusyChange={onBusyChange} onDirtyChange={onDirtyChange} onClose={() => { setAdding(false); onDirtyChange(false); }} onSaved={receipt => { setAdding(false); onDirtyChange(false); setNotice(receipt.duplicate ? '这份内容已接收，已找到同一份原件。' : '已接收，原件已保存到电脑。'); list.retry(); change({ id: receipt.submission_id }); }} /> : null}
    {view.id ? <InboxDetail cloud={cloud} onLearning={captureId=>onNavigate('learning',captureId)} onSchool={rawId => onNavigate('school', null, { rawItemId: rawId })} id={view.id} onBusyChange={onBusyChange} onDirtyChange={onDirtyChange} onClose={() => { change({ id: null }); list.retry(); }} onReview={sid => { onDirtyChange(false); onNavigate('review', sid); }} /> : null}
  </section>;
}

function InboxInput({ onSaved, onClose, onBusyChange, onDirtyChange }) {
  const draft = useDraft({ kind: 'text', original_text: '', title_hint: '' });
  const [files, setFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [dialog, setDialog] = useState(null);
  const pending = useRef(false), request = useRef(null), controller = useRef(null);
  const dirty = Boolean(files.length || draft.values.original_text || draft.values.title_hint);
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => controller.current?.abort(), []);
  const close = () => dirty ? setDialog({ title: '放弃尚未发送的输入？', message: '这些内容尚未确认保存到电脑。', confirmLabel: '放弃输入', action: onClose }) : onClose();
  const send = async large => {
    if (pending.current) return;
    const values = draft.values;
    const invalid = {};
    if (values.kind !== 'file' && !values.original_text.trim()) invalid.original_text = '请填写内容。';
    if (values.original_text.length > 100000) invalid.original_text = '内容最多 100,000 字。';
    if (values.kind === 'url') { try { const u = new URL(values.original_text.trim()); if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password || values.original_text.length > 4096) throw new Error(); } catch { invalid.original_text = '请输入不含账号密码的完整 HTTP / HTTPS 链接。'; } }
    if (values.kind === 'file' && (!files.length || files.length > 10 || files.some(f => f.size > 50000000) || files.reduce((n, f) => n + f.size, 0) > 59000000)) invalid.files = '请选择 1–10 个文件，单个不超过 50 MB，合计不超过 59 MB。';
    if (!draft.validate(invalid)) return;
    if (!large && files.some(f => f.size > 15000000)) { setDialog({ title: '保存较大的文件？', message: '文件超过 15 MB，上传和本地解析可能需要较长时间。原件先保存。', confirmLabel: '保存文件', action: () => send(true) }); return; }
    pending.current = true; setBusy(true); onBusyChange(true); setError('');
    controller.current = new AbortController();
    const signature = JSON.stringify({ values, files: files.map(f => [f.name, f.size, f.lastModified]) });
    if (request.current?.signature !== signature) request.current = { signature, id: crypto.randomUUID() };
    let path, body;
    if (values.kind === 'file') {
      path = '/collect-files?inbox=1'; body = new FormData(); files.forEach(f => body.append('files', f)); body.set('original_text', values.original_text); body.set('title_hint', values.title_hint); body.set('confirm_large', String(Boolean(large)));
    } else { path = `/raw?inbox_kind=${values.kind}`; body = { original_text: values.kind === 'url' ? values.original_text.trim() : values.original_text, title_hint: values.title_hint }; }
    try { const receipt = await personalRequest(path, { method: 'POST', body, headers: { 'Idempotency-Key': request.current.id }, signal: controller.current.signal }); onSaved(receipt); }
    catch (failure) { if (!controller.current.signal.aborted) setError(failure.message); }
    finally { pending.current = false; setBusy(false); onBusyChange(false); }
  };
  return <DeskSheet title="添加到 Eric Inbox" onClose={close} busy={busy}>
    <form className="personal-form" ref={draft.formRef} noValidate onKeyDown={preventImeSubmit} onSubmit={e => { e.preventDefault(); send(false); }}>
      <fieldset disabled={busy}><Field label="输入类型" name="kind"><select value={draft.values.kind} onChange={e => draft.field('kind', e.target.value)}><option value="text">文字</option><option value="url">链接</option><option value="file">图片 / PDF / 其他文件</option></select></Field>
        <Field label="标题（可选）" name="title_hint"><input maxLength={200} value={draft.values.title_hint} onChange={e => draft.field('title_hint', e.target.value)} /></Field>
        {draft.values.kind === 'file' ? <Field label="选择文件" name="files" error={draft.errors.files} help="不支持解析的格式也会保存原件。"><input type="file" multiple onChange={e => setFiles(Array.from(e.target.files || []))} /></Field> : null}
        <Field label={draft.values.kind === 'url' ? '网页链接' : draft.values.kind === 'file' ? '附加原文（可选）' : '原始文字'} name="original_text" error={draft.errors.original_text}>{draft.values.kind === 'url' ? <input type="url" inputMode="url" value={draft.values.original_text} onChange={e => draft.field('original_text', e.target.value)} /> : <textarea rows={9} className="resize-none" style={{ resize: 'none' }} value={draft.values.original_text} onChange={e => draft.field('original_text', e.target.value)} />}</Field>
      </fieldset><p className="personal-meta">先保存原件，再本地解析和给出建议。需要云端分析时，由你在提取预览中发起。</p>
      {error ? <p className="personal-field-error" role="alert">{error}</p> : null}
      <div className="personal-form-actions"><SubmitButton busy={busy}>保存到 Inbox</SubmitButton><button type="button" className="button button-quiet" disabled={busy} onClick={close}>取消</button></div>
    </form>{dialog ? <ConfirmDialog {...dialog} onCancel={() => setDialog(null)} onConfirm={() => { const action = dialog.action; setDialog(null); action(); }} /> : null}
  </DeskSheet>;
}

function InboxDetail({ onSchool, onLearning, id, onClose, onReview, onBusyChange, onDirtyChange,cloud=false }) {
  const resource = usePersonalResource(`/inbox/${encodeURIComponent(id)}`, { refreshInterval: 3000 });
  const [linking, setLinking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [dialog, setDialog] = useState(null);
  const pending = useRef(false);
  const d = resource.data;
  const close = () => linking ? setDialog({ title: '放弃未保存的关联？', message: '已保存的原件会保留。', confirmLabel: '放弃修改', action: () => { onDirtyChange(false); onClose(); } }) : onClose();
  const mutate = async (path, body) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); onBusyChange(true); setError('');
    try { const saved=await personalRequest(path, { method: path.endsWith('/links') ? 'PUT' : 'POST', body }); setLinking(false); onDirtyChange(false); setNotice(saved?.queued?'修改已保存，会自动同步。':path.endsWith('/links') ? '关联已保存。' : '已开始继续处理。'); resource.retry(); }
    catch (failure) { setError(failure.message); }
    finally { pending.current = false; setBusy(false); onBusyChange(false); }
  };
  return <DeskSheet title={d?.title || '输入详情'} onClose={close} busy={busy} wide>
    <ReadError resource={resource} />{!d ? <p role="status">正在读取原件…</p> : <>
      <p className="personal-meta">{TYPES[d.input_kind]} · {d.source} · {date(d.created_at)}（北京时间）</p><p role="status"><strong>{stateLabel(d)}</strong> · 原件已保存</p>
      {cloud&&d.learning_capture&&d.learning_capture.status!=='completed'?<p><button type="button" className="button button-quiet" disabled={busy} onClick={()=>onLearning(d.learning_capture.id)}>继续整理课堂笔记</button></p>:null}
      {cloud?<p><button type="button" className="personal-text-button" disabled={busy} onClick={()=>mutate(`/inbox/${id}/archive`,{expected_revision:d.archive?._cloud?.revision||0,archived:!d.archive?.archived})}>{d.archive?.archived?'放回收件箱':'移到已归档'}</button><span className="personal-meta"> · 原件、课程与正式记录始终保留</span></p>:null}
      {d.extraction_quality?.files.length ? <div className="eric-inbox-quality" aria-label="保存与提取质量"><p>原件：已保存，可查看与下载。</p>{d.extraction_quality.files.map(f => <div key={f.attachment_id}><p>{f.filename}{f.pdf_structure !== 'not_applicable' ? ` · PDF 结构：${f.pdf_structure === 'readable' ? `可读取 · ${f.page_count} 页` : '尚未确认可读取'}` : ''}</p><p>内容提取：{f.content_quality === 'needs_review' ? '需要人工检查' : '已提取文字'}{f.ocr_quality ? ` · OCR ${f.ocr_quality === 'low' ? '质量偏低' : '尚未核对，无可靠置信度'}` : ''}</p></div>)}{d.extraction_quality.reasons.map((reason, i) => <p className="personal-field-error" key={i}>{reason}</p>)}<p className="personal-meta">文字提取完成不代表已经理解或核对笔记。可在提取预览中主动重新分析。</p></div> : null}
      {d.error && d.error.code !== 'extraction_warning' ? <div className="personal-analysis-error" role="alert"><p>{d.error.message}</p>{d.error.retryable && d.managed && d.processing_stage !== 'analysis' ? <button type="button" className="button button-quiet" disabled={busy} onClick={() => mutate(`/inbox/${id}/retry`, { expected_version: d.version })}>重试本地处理</button> : null}</div> : null}
      <p className="eric-inbox-feedback" role="status">{notice}</p>{error ? <p className="personal-field-error" role="alert">{error}</p> : null}
      <section className="eric-inbox-detail-section" id="inbox-original"><h3>原始材料 · Original</h3><OriginalNotice snapshot={d.original} onClear={() => {}} busy={busy} />{d.input_kind === 'image' && d.original.attachments?.[0] ? <SourceImage source={{ attachment_id: d.original.attachments[0].id }} /> : null}{d.input_kind === 'pdf' && d.original.attachments?.[0] ? <details className="desk-disclosure"><summary>预览 PDF 第 1 页</summary><SourceImage source={{ attachment_id: d.original.attachments[0].id, document_id:d.school_documents?.[0]?.id, page: 1 }} /></details> : null}</section>
      <section className="eric-inbox-detail-section"><h3>提取内容 · Extracted</h3>{d.review.snapshot.id !== d.original.id || d.input_kind === 'text' ? <pre className="personal-original-text">{d.review.snapshot.original_text}</pre> : <p className="personal-meta">{d.input_kind === 'url' ? '网页正文尚未获取。' : '尚无可用解析文字，原件已保留。'}</p>}{d.review.snapshot.web ? <p className="personal-meta">网页快照获取于 {date(d.review.snapshot.web.fetched_at)} · {d.review.snapshot.web.title}</p> : null}{d.attachments.map(a => <p className="personal-meta" key={a.id}>{a.filename} · {a.mime_type || '未知 MIME'} · .{a.extension || '无扩展名'} · {a.parse_status === 'unsupported' ? '暂不支持自动解析' : a.parse_status === 'parsed' ? '已解析' : a.parse_error || '等待解析'}</p>)}</section>
      <section className="eric-inbox-detail-section"><h3>关联 · Linked</h3>{d.input_kind === 'pdf' && d.course?.id === 'course-math-ii' ? <p><button type="button" className="button button-quiet" disabled={busy || linking} onClick={() => onSchool(id)}>创建 / 关联课堂记录</button></p> : null}{linking ? <InboxLinks key={id} detail={d} busy={busy} onSave={values => mutate(`/inbox/${id}/links`, { expected_version: d.version, ...values })} onCancel={() => { setLinking(false); onDirtyChange(false); }} /> : <><p>课程：{d.course?.name || '未关联'} · 项目：{d.project_ref || '未关联'}</p>{Object.values(d.classification?.possible_course || d.review.course_suggestions || {}).map(c => <p className="personal-meta" key={c.id}>建议课程：{c.name} · 尚未确认</p>)}<button type="button" className="personal-text-button" disabled={busy} onClick={() => { setLinking(true); onDirtyChange(true); }}>修改课程 / 项目关联</button></>}</section>
      <section className="eric-inbox-detail-section"><h3>{d.classification?.note_mode ? '笔记分类' : '分析 · Analysis'}</h3>{d.classification?.note_mode ? null : d.classification ? <><p>领域建议：{({ school: '学校', ielts: '雅思', engineering: '工程', travel: '旅行', general: '通用' })[d.classification.domain]} · 内容建议：{({ note: '笔记', assignment: '作业', reference: '参考资料', exercise: '习题', notice: '通知', document: '文档' })[d.classification.kind]}</p><p className="personal-meta">本地规则建议，{d.classification.possible_action ? '可能包含待办行动，请核对。' : '没有识别出明确行动。'}本地分类未调用云端 AI。</p></> : <p className="personal-meta">尚无 Inbox 分类。历史来源沿用已有分析。</p>}
        {d.note_processing ? <div role="status"><p>{d.note_processing.message}</p>{d.note_processing.summary ? <p>{d.note_processing.summary}</p> : null}{d.classification?.note_classification ? <><p>知识点：{d.classification.note_classification.knowledge_point || '待确认'}</p><p>标签：{d.classification.note_classification.tags?.join(' · ') || '待确认'}</p></> : null}</div> : null}
        {d.review.extraction ? <p>已有 {d.review.extraction.provider} 分析 · {d.review.extraction.model} · {d.review.extraction.status === 'failed' ? '未完成，可在提取预览中核对与重试' : '结果待你核对'}</p> : null}
        {d.classification?.note_mode ? <details className="desk-disclosure"><summary>分类用量</summary><p className="personal-meta">{d.classification.note_classification?.model || '暂无已完成的分类调用'}{d.classification.note_classification?.usage ? ` · ${JSON.stringify(d.classification.note_classification.usage)}` : ''}{d.classification.note_classification?.cost ? ` · 估算费用 ${d.classification.note_classification.cost.estimated_cost == null ? '未知' : `¥${d.classification.note_classification.cost.estimated_cost}`}` : ''}</p></details> : <details className="desk-disclosure"><summary>处理依据与用量</summary><p className="personal-meta">{d.classification?.processing_version || '原有来源处理链'} · {d.classification?.model || d.review.extraction?.model || (d.note_processing?.status === 'completed' ? '已有笔记分析（复用已保存结果）' : '尚无模型分析')} · {d.classification?.prompt_version || d.review.extraction?.prompt_version || '无'}</p>{d.classification?.evidence?.map((e, i) => <blockquote key={i}>{e.quote}</blockquote>)}<p className="personal-meta">本地分类：0 tokens · 0 张云端图片 · ¥0</p>{d.review.extraction ? <p className="personal-meta">云端用量：{JSON.stringify(d.review.extraction.token_usage || {})}{d.review.extraction.cost ? ` · 费用${d.review.extraction.cost.estimated_cost === null ? '未知' : `保守估算 ¥${d.review.extraction.cost.estimated_cost}`} · ${d.review.extraction.cost.basis}` : ' · 历史分析未估算费用'}</p> : null}</details>}
      </section>
      <section className="eric-inbox-detail-section"><h3>行动提案 · Actions</h3>{d.proposal_count ? <ul>{d.review.extraction.parsed_output.events.map(e => <li key={e.id}>{e.title}</li>)}</ul> : <p className="personal-meta">尚无行动提案。需要提取待办时，可进入已有提取预览；明确发起后才会调用配置的 AI。</p>}<p className="personal-meta">正式事项和任务仍由你在批量确认中决定。拒绝保留原件。</p><button type="button" className="button button-quiet" disabled={busy || d.review_available === false} onClick={() => onReview(d.snapshot_id)}>{d.review_status === 'confirmed' ? '查看已确认结果' : d.proposal_count ? '核对 / 确认 / 拒绝提案' : '打开提取预览'}</button></section>
      <section className="eric-inbox-detail-section"><h3>依据 · Evidence</h3><a className="personal-text-button" href="#inbox-original">回到原始材料</a><p className="personal-meta">共 {d.receipts.length || 1} 次接收记录，指向同一份输入。分析和原文分别保留。</p></section>
    </>}{dialog ? <ConfirmDialog {...dialog} onCancel={() => setDialog(null)} onConfirm={() => { const action = dialog.action; setDialog(null); action(); }} /> : null}
  </DeskSheet>;
}

function InboxLinks({ detail, busy, onSave, onCancel }) {
  const draft = useDraft({ course_id: detail.course?.id || null, project_ref: detail.project_ref || '' });
  const [adding, setAdding] = useState(false);
  return <form className="personal-form" ref={draft.formRef} noValidate onKeyDown={preventImeSubmit} onSubmit={e => { e.preventDefault(); if (!busy && !adding) onSave(draft.values); }}><CourseField value={draft.values.course_id} current={detail.course} suggestion={Object.values(detail.classification?.possible_course || detail.review.course_suggestions || {})[0]} disabled={busy} onBusyChange={setAdding} onChange={id => draft.field('course_id', id)} /><Field label="项目标记（可选）" name="project_ref" help="仅作轻量归档标记；暂不建立项目管理系统。"><input maxLength={120} disabled={busy} value={draft.values.project_ref} onChange={e => draft.field('project_ref', e.target.value)} /></Field><div className="personal-form-actions"><SubmitButton busy={busy || adding}>保存关联</SubmitButton><button type="button" className="button button-quiet" disabled={busy || adding} onClick={onCancel}>取消修改</button></div></form>;
}
