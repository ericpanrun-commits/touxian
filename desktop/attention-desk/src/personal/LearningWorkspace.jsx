import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, BookOpen, Clock3, Download } from 'lucide-react';
import usePersonalResource from './usePersonalResource';
import { personalRequest } from './api';
import DeskSheet from './DeskSheet';
import { ConfirmDialog } from './Evidence';
import { SourceImage } from './DocumentSources';
import { Field, SubmitButton } from './Forms';
import { preventImeSubmit, useDraft, validTime } from './formHelpers';
import { learningBookmarklet, consumeLearningShare } from './learningHelpers';
import { ExerciseScore } from './PreviewEvidence';
import LearningFocus from './LearningFocus';
import './learning.css';

let sharedInput = consumeLearningShare();
const civil = value => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric' }).format(new Date(value));
const topicName = title => title.replace(/^第.{1,4}章\s*第.{1,4}节\s*/, '').replace('（前置参考）','');
const resultLabel = { correct: '正确', incorrect: '错误', unknown: '结果待核对' };
const activityLabel = { preview: '课前预习', classroom: '课堂习题', practice: '学习练习', diagnostic: '历史诊断', retest: '复测', self_test: '自测' };

function ReadState({ resource }) {
  return <>{resource.loading && !resource.data ? <p className="learning-read-state" role="status">正在读取学习记录…</p> : null}{resource.error ? <div className="personal-field-error" role="alert">{resource.error.message} <button type="button" className="personal-text-button" onClick={resource.retry}>重新读取</button></div> : null}</>;
}

function TopicRows({ items, empty, onOpen, progress = false }) {
  return items.length ? <ul className="learning-topic-list">{items.map(t => <li key={t.id}><button type="button" onClick={() => onOpen(t.id)}><span><strong>{topicName(t.title)}</strong><small>{progress ? (t.before_after_progress ? '课前出错 → 课后独立复测正确' : '上次错误 → 间隔后独立复测正确') : t.learning_state.reasons[1] || t.learning_state.reasons[0]}</small></span><span className={`personal-status ${progress ? 'done' : 'needs_review'}`}>{t.learning_state.label}</span></button></li>)}</ul> : <p className="learning-empty">{empty}</p>;
}

export default function LearningWorkspace({ onNavigate, onDirtyChange, onBusyChange,cloud=false,captureId=null }) {
  const [sheet, setSheet] = useState(null), [dirty, setDirty] = useState(false), [dialog, setDialog] = useState(null);
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState(''), [error, setError] = useState('');
  const [share, setShare] = useState(sharedInput);
  const pending = useRef(false), shareStarted = useRef(false);
  const resource = usePersonalResource('/learning', { refreshInterval: 10000, paused: busy });
  const d = resource.data;
  const preview = d?.preview;
  useEffect(()=>{setSheet(current=>captureId?{kind:'capture',id:captureId}:current?.kind==='capture'?null:current);setError('');},[captureId]);
  const otherWeak = d?.weak.filter(t => !preview?.weaknesses.some(w => w.topic_id === t.id)) || [];
  const markDirty = value => { setDirty(value); onDirtyChange(value); };
  const write = async (path, options, message, done) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); onBusyChange(true); setError(''); setNotice('');
    try { const value = await personalRequest(path, options); setNotice(value?.queued?'内容已保存在此设备，会自动发送并接续处理。':message); done?.(value); resource.retry(); return value; }
    catch (failure) { setError(failure.message); return null; }
    finally { pending.current = false; setBusy(false); onBusyChange(false); }
  };
  const saveShare = () => write('/learning/capture', { method: 'POST', body: share }, '已送到 Eric Inbox，正在后台整理。', () => { sharedInput = null; setShare(null); });
  useEffect(() => { if (share && !share.error && !shareStarted.current) { shareStarted.current = true; saveShare(); } }, []);
  const close = () => {
    const leave = () => { markDirty(false); setSheet(null); };
    if (dirty) setDialog({ title: sheet?.kind==='capture'?'放弃未保存的选择？':'保留已保存的记录，放弃当前作答？', message: '当前选择还没有保存，已保存的原件会保留。', confirmLabel: '放弃当前输入', action: leave }); else leave();
  };
  const openTopic = id => { setSheet({ kind: 'topic', id }); setError(''); };
  const exportPack = async () => {
    if(cloud){
      setBusy(true);setError('');
      try{const overview=await personalRequest('/learning');const topics=await Promise.all(overview.topics.map(topic=>personalRequest('/learning/topics/'+encodeURIComponent(topic.id))));const url=URL.createObjectURL(new Blob([JSON.stringify({schema:'eric-learning-records-v1',generated_at:new Date().toISOString(),overview,topics},null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='eric-learning-records.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);setNotice('学习记录已导出，包含已保存的来源引用。');}catch(value){setError(value.message);}finally{setBusy(false);}return;
    }
    const blob = await write('/learning/audit-pack', { responseType: 'blob' }, '审查包已准备好，只在你交给审查者后参与外部审查。');
    if (!blob) return;
    const url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url; a.download = 'eric-learning-audit.zip'; a.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <div className="learning-workspace">
    <div className="desk-page-heading learning-heading"><div>{cloud?<h2>学习工作台</h2>:<h1 tabIndex={-1}>Learning</h1>}<p>哪里还不会，下一步学什么。</p></div></div>
    <p className="learning-feedback" role="status" aria-live="polite">{notice}</p>{error ? <p className="personal-field-error" role="alert">{error}</p> : null}{share?.error ? <p className="personal-field-error" role="alert">{share.error}</p> : share ? <p role="status">{busy ? '正在保存分享的页面…' : <button type="button" className="personal-text-button" onClick={saveShare}>重试保存本次分享</button>}</p> : null}
    <ReadState resource={resource} />
    {d ? <><LearningFocus data={d} onOpen={openTopic} onClassroom={() => onNavigate('school')} onPending={() => { const capture = (d.pending_captures || []).find(c => ['needs_context', 'needs_review'].includes(c.status)); if (capture) setSheet({ kind: 'capture', id: capture.id }); }} onRetest={plan => plan.status === 'ready' && new Date(plan.due_at) <= new Date() ? setSheet({ kind: 'retest', id: plan.id }) : openTopic(plan.topic_id)}>
      <div className="learning-sync"><button type="button" className="button button-quiet" disabled={busy || d.browser_sync?.busy} onClick={() => write('/learning/sync', { method: 'POST', body: { request_id: crypto.randomUUID() } }, '整批读取已启动，进度会显示在这里。')}>{d.browser_sync?.busy ? '正在读取学习通…' : '读取学习通'}</button><p className="personal-meta" role="status">{d.browser_sync?.message || '登录后，一次读取已提交的预习与课堂题。'}</p></div>
    </LearningFocus>
    <details className="personal-section desk-disclosure learning-brief"><summary>本次学习与来源</summary>
      <div className="personal-section-heading"><h2>{d.courses.find(c => c.id === preview?.course_id)?.name || '我的学习'}</h2><button type="button" className="personal-text-button" disabled={busy} onClick={() => onNavigate('school')}><BookOpen size={16} />课程与课堂</button></div>
      <section className="learning-preview"><h3>课前预习</h3>{preview ? <><p className="personal-meta">{preview.title}{preview.activity_date ? ` · ${preview.activity_date}` : ''}</p><ExerciseScore counts={preview.exercises} />{preview.material_exposures ? <p className="personal-meta">预习材料 {preview.material_exposures} 项，{preview.material_completed} 项有完成记录；只记接触。</p> : null}<Sources sources={preview.raw_item_ids.map(id => ({ raw_item_id: id }))} label="查看此次预习原记录" /></> : <p className="learning-empty">还没有预习记录。课前把材料和习题结果一起交给 Inbox，系统会分别整理。</p>}</section>
      {preview ? <>
        <section className="learning-preweak"><h3>课前薄弱</h3>{preview.weaknesses.length ? <ul className="learning-topic-list">{preview.weaknesses.map(w => <li key={w.topic_id}><button type="button" disabled={busy} onClick={() => openTopic(w.topic_id)}><span><strong>{topicName(w.title)}</strong><small>本次预习 {w.incorrect} 题错误</small></span><ArrowUpRight size={15} /></button></li>)}</ul> : <p className="learning-empty">{preview.exercises.unknown ? '还有题目结果待核对。' : preview.exercises.total ? '本次已知结果没有错误。' : '收到习题作答后，再判断课前薄弱点。'}</p>}</section>
        <section className="learning-cycle">
          <div><h3>课堂</h3><p>{preview.lecture?.notes_available ? (preview.class_completed ? '已完成 · 已收到课堂笔记' : '已收到课堂笔记') : preview.lecture ? '待课堂笔记' : '课次暂未关联'}</p>
            {preview.lecture ? <p className="personal-meta">{preview.lecture.lecture_date}{preview.lecture.link_basis === 'inferred_unique_next_day' ? ' · 按次日唯一课次推测关联' : ''}</p> : null}
            {preview.classroom_exercises?.total ? <><ExerciseScore counts={preview.classroom_exercises} label="本次课堂题" />{preview.classroom_exercises.unknown ? <p className="personal-meta">{preview.classroom_exercises.unknown} 题结果待公布或核对，先保留作答。</p> : null}</> : preview.lecture?.notes_available ? <p className="personal-meta">本次未记录课堂题。</p> : null}
          </div>
          <div><h3>课后复测</h3><p>{preview.after_lecture.progress ? `独立复测正确 · ${preview.after_lecture.independent_successes} 道` : preview.after_lecture.pending ? `待完成 · ${preview.after_lecture.pending} 道` : '尚待课后验证'}</p>
            {preview.after_lecture.next_due_at ? <p className="personal-meta">{civil(preview.after_lecture.next_due_at)}</p> : null}
            {preview.after_lecture.progress && preview.after_lecture.pending ? <p>待完成 · {preview.after_lecture.pending} 道</p> : null}
            {preview.after_lecture.progress ? <p className="personal-meta">课前出错 → 相关课堂笔记 → 课后独立正确</p> : null}
          </div>
        </section>
        <section className="learning-preweak"><h3>学习变化</h3><p>{preview.after_lecture.progress ? '已有延迟独立复测正确证据。' : preview.after_lecture.classroom_improvements ? '课堂已有改善线索，仍需延迟复测。' : preview.weaknesses.length ? '课前薄弱仍待独立验证。' : '继续积累独立作答证据。'}</p>{preview.weakness_coverage?.some(w => w.covered_in_notes) ? <p className="personal-meta">课堂笔记已记录相关内容；覆盖不等于掌握。</p> : null}</section>
      </> : null}
      {d.classroom.total || d.received_without_date.total ? <details className="desk-disclosure learning-details"><summary>课堂习题 · 可选来源</summary>{d.classroom.total ? <ExerciseScore counts={d.classroom} label="今天课堂题" /> : null}{d.received_without_date.total ? <p className="personal-meta">今天另收 {d.received_without_date.total} 道课堂题：正确 {d.received_without_date.correct}，错误 {d.received_without_date.incorrect}，待核对 {d.received_without_date.unknown}。提交日期未提供。</p> : null}</details> : null}
      <section className="learning-next"><h3>下一步</h3>{d.due.length ? <><p>今天做 {d.total_due} 道复测，先从这一道开始。</p><button type="button" className="button button-primary" disabled={busy || d.due[0].status !== 'ready'} onClick={() => setSheet({ kind: 'retest', id: d.due[0].id })}>{d.due[0].status === 'ready' ? '开始一道复测' : '复测题暂未准备好'}</button>{d.due[0].status !== 'ready' ? <button type="button" className="personal-text-button" onClick={() => openTopic(d.due[0].topic_id)}>先看原题与教材</button> : null}</> : d.upcoming.length ? <p><Clock3 size={16} />{civil(d.upcoming[0].due_at)}做一道「{topicName(d.upcoming[0].title)}」复测。</p> : d.history_pending_diagnostic ? <><p>先用简短诊断，看看以前的章节还记得多少。</p><button type="button" className="button button-primary" disabled={busy} onClick={() => setSheet({ kind: 'history' })}>补一节历史内容</button></> : <p className="learning-empty">暂时没有需要安排的复测。有真实错误后，系统会自动准备。</p>}</section>
      <div className="learning-pair">{!preview || otherWeak.length ? <section><h3>最近薄弱</h3><TopicRows items={otherWeak} onOpen={openTopic} empty="还没有足够答题证据判断薄弱点。" /></section> : null}<section><h3>最近进步</h3><TopicRows progress items={d.progress} onOpen={openTopic} empty="间隔后独立做对，进步会出现在这里。" /></section></div>
      <section className="learning-history"><div><h3>历史待补</h3><p>{d.history_pending_diagnostic ? `${d.history_pending_diagnostic} 个以前章节还缺诊断。${d.history.some(t => t.history_status === 'suggested') ? '集中确认，先补一个。' : '已记为以前学过，先做一组简短诊断。'}` : '以前学过的内容也可以从简短诊断开始。'}</p></div>{d.history.length ? <button type="button" className="button button-quiet" disabled={busy} onClick={() => setSheet({ kind: 'history' })}>查看历史章节</button> : null}</section>
      {d.pending_captures.length ? <details className="desk-disclosure learning-details"><summary>{d.pending_captures.length} 份材料还需要辨认</summary><ul className="learning-simple-list">{d.pending_captures.map(c => <li key={c.id}><span>{c.error?.message || '题目或课程有些不确定，原件已保存。'}</span><button type="button" className="personal-text-button" onClick={() => setSheet({ kind: 'capture', id: c.id })}>查看这一份</button></li>)}</ul></details> : null}
      <details className="desk-disclosure learning-details"><summary>全部学习内容</summary><TopicRows items={d.topics} onOpen={openTopic} empty="收到学习材料后，会逐步整理到这里。" />{d.limits.topics_total > d.limits.topics_shown ? <p className="personal-meta">显示最近 {d.limits.topics_shown} 项；完整记录可导出审查包。</p> : null}</details>
    </details></> : null}
    <details className="personal-section desk-disclosure learning-tools"><summary>收集与第二意见</summary><p>学习通：你正常答题。结果页可一键分享，也可把截图、复制文字或 PDF 交给 Inbox。</p><ShareSetup /><p className="personal-meta">尚未接通已登录会话的自动监控。课堂题始终由你自己答。</p>{d?.background_analysis ? <BackgroundAnalysis status={d.background_analysis} busy={busy} write={write} /> : null}<div className="personal-form-actions"><button type="button" className="button button-quiet" disabled={busy} onClick={exportPack}><Download size={16} />{cloud?"导出学习记录":"Export Learning Audit Pack"}</button><button type="button" className="button button-quiet" disabled={busy} onClick={() => setSheet({ kind: 'external' })}>保存外部审查</button></div><p className="personal-meta">把审查包交给 Grok Bot。它的意见只作第二意见，不自动改学习记录。</p></details>
    {sheet?.kind === 'history' && d ? <HistorySheet data={d} busy={busy} error={error} onClose={close} onDirty={markDirty} onOpen={openTopic} onSave={(ids, learned) => write('/learning/history/confirm', { method: 'POST', body: { topic_ids: ids, learned } }, learned ? '已记为以前学过，正在准备补档和诊断。' : '已保留你的进度选择。', () => { markDirty(false); setSheet(null); })} /> : null}
    {sheet?.kind === 'topic' ? <TopicSheet key={sheet.id} id={sheet.id} busy={busy} error={error} onClose={close} onDirty={markDirty} write={write} /> : null}
    {sheet?.kind === 'retest' ? <RetestSheet id={sheet.id} busy={busy} error={error} onClose={close} onDirty={markDirty} write={write} /> : null}
    {sheet?.kind === 'capture' ? <CaptureSheet key={sheet.id} id={sheet.id} courses={d?.courses || []} busy={busy} error={error} onClose={close} onDirty={markDirty} onLecture={id=>onNavigate(cloud?'lecture':'school',id)} write={write} /> : null}
    {sheet?.kind === 'external' ? <ExternalSheet busy={busy} error={error} onDirty={markDirty} onClose={close} onSave={text => write('/learning/external-reviews', { method: 'POST', body: { text } }, '已保存为 External Review，学习数据未被改动。', () => { markDirty(false); setSheet(null); })} /> : null}
    {dialog ? <ConfirmDialog {...dialog} onCancel={() => setDialog(null)} onConfirm={() => { const action = dialog.action; setDialog(null); action(); }} /> : null}
  </div>;
}

function BackgroundAnalysis({ status, busy, write }) {
  const enabled = status.enabled;
  const toggle = () => write('/learning/analysis/authorization', { method: 'POST', body: {
    scope_version: status.scope_version, enabled: !enabled, confirmation_source: 'explicit_local_ui',
    confirmation_text: enabled ? '暂停后台处理' : '同意并启用DeepSeek后台处理',
  } }, enabled ? '后台文字处理已暂停，已有记录保留。' : '已启用官方 DeepSeek 文字整理。');
  return <div className="learning-background"><p>后台文字整理：{enabled && status.available ? '已启用' : enabled ? '等待服务就绪' : '已暂停'}</p><p className="personal-meta">使用已配置的官方 DeepSeek，处理你分享的学习文字、题目、本人答案、结果和相关教材文字，用于整理、错因建议与复测。原始文件、密码和浏览器会话不发送。每项分析失败后不自动重发；每天最多 {status.daily_request_limit} 次，每月估算上限 {status.monthly_estimate_limit_cny} 元。</p>{status.available ? <button type="button" className="personal-text-button" disabled={busy} onClick={toggle}>{enabled ? '暂停后台文字处理' : '同意并启用后台文字整理'}</button> : null}</div>;
}

function ShareSetup() {
  const [copied, setCopied] = useState(false), [failed, setFailed] = useState(false);
  const bookmarklet = learningBookmarklet(window.location.origin + '/');
  return <div><button type="button" className="personal-text-button" onClick={async () => { try { await navigator.clipboard.writeText(bookmarklet); setCopied(true); } catch { setFailed(true); } }}>复制一键收集书签</button><p className="personal-meta" role="status">{copied ? '已复制。新建浏览器书签，名称设为 Send to Eric OS，把网址替换为刚复制的内容。以后在结果页点击即可。' : '首次设置一次书签，以后在学习通结果页点 Send to Eric OS。只读取页面可见文字，不操作答题。'}</p>{failed ? <Field name="bookmarklet" label="复制下面的书签内容"><textarea className="resize-none" readOnly rows={6} style={{ resize: 'none' }} value={bookmarklet} onFocus={e => e.target.select()} /></Field> : null}</div>;
}

function HistorySheet({ data, busy, error, onClose, onDirty, onSave, onOpen }) {
  const suggestions = data.history.filter(t => t.history_status === 'suggested');
  const [selected, setSelected] = useState(() => suggestions.map(t => t.id));
  return <DeskSheet title="把以前学过的内容补回来" onClose={onClose} busy={busy} wide><p>根据教材位置和已有课次，以下内容可能以前学过，但没有对应课堂笔记。一次确认即可，不代表已掌握。</p>{error ? <p role="alert" className="personal-field-error">{error}</p> : null}<ul className="learning-history-list">{data.history.map(t => <li key={t.id}>{t.history_status === 'suggested' ? <label><input type="checkbox" disabled={busy} checked={selected.includes(t.id)} onChange={e => { setSelected(current => e.target.checked ? [...current, t.id] : current.filter(id => id !== t.id)); onDirty(true); }} /><span>{t.title}<small>教材与进度推测 · 等你确认</small></span></label> : <button type="button" className="personal-event-row" disabled={busy} onClick={() => onOpen(t.id)}><span><strong>{topicName(t.title)}</strong><small>以前学过 · 练习证据仍需诊断</small></span><ArrowUpRight size={16} /></button>}</li>)}</ul>{suggestions.length ? <div className="personal-form-actions"><button type="button" className="button button-primary" disabled={busy || !selected.length} onClick={() => onSave(selected, true)}>确认这些以前学过</button><button type="button" className="button button-quiet" disabled={busy || !selected.length} onClick={() => onSave(selected, false)}>这些暂不确认</button></div> : null}</DeskSheet>;
}

function Sources({ sources, label = '查看教材依据与原页' }) {
  const [opened, setOpened] = useState(null);
  const unique = [...new Map(sources.filter(s => s.document_id && s.page).map(s => [s.document_id + ':' + s.page, s])).values()];
  const originals = [...new Set(sources.map(s => s.raw_item_id).filter(Boolean))];
  return unique.length || originals.length ? <details className="desk-disclosure learning-source"><summary>{label}</summary>{originals.map((id,index) => <p key={id}><a className="personal-text-button" href={`?personal_view=eric-inbox&inbox_item=${encodeURIComponent(id)}`} target="_blank" rel="noreferrer">{originals.length > 1 ? `第 ${index+1} 份原始学习记录` : '查看原始学习记录'}<ArrowUpRight size={14} /></a></p>)}{unique.map(s => <div key={s.source_id}><button type="button" className="personal-text-button school-source-link" onClick={() => setOpened(s)}>{s.title} · PDF {s.page}{s.printed_page_label ? ` / 印刷 ${s.printed_page_label}` : ''}</button></div>)}{opened ? <SourceImage source={opened} /> : null}</details> : null;
}

function TopicSheet({ id, busy, error, onClose, onDirty, write }) {
  const r = usePersonalResource(`/learning/topics/${encodeURIComponent(id)}`, { refreshInterval: 5000, paused: busy });
  const d = r.data, summary = d?.artifacts.find(a => a.kind === 'retro_summary');
  const answered = d?.attempts.some(a => a.kind === 'diagnostic');
  const summarySources = new Set((summary?.source || []).map(s => s.source_id));
  const relatedSources = [...(d?.artifacts || []).filter(a => a.kind === 'mistake_analysis').flatMap(a => a.source || []).filter(s => !summarySources.has(s.source_id)), ...(d?.attempts || []).filter(a => a.source_raw_item_id).map(a => ({ raw_item_id: a.source_raw_item_id })), ...(d?.exposure || []).filter(e => e.raw_item_id).map(e => ({ raw_item_id: e.raw_item_id }))];
  return <DeskSheet title={d?.title || '学习内容'} onClose={onClose} busy={busy} wide><ReadState resource={r} />{d ? <><p className="learning-state">{d.learning_state.label}{d.history_status === 'confirmed' ? <span>以前学过 · 没有当时课堂笔记</span> : null}</p><ul className="learning-reasons">{d.learning_state.reasons.map(x => <li key={x}>{x}</li>)}</ul>{summary ? <section className="learning-retro"><h3>事后补档</h3><p className="personal-meta">{summary.payload?.notice}</p>{summary.error ? <p className="personal-field-error">{summary.error.message}</p> : null}{summary.payload?.core.map((c,i) => <p key={i}>{c.text}</p>)}<p className="personal-meta">{summary.payload?.scope}</p><Sources sources={summary.source} /></section> : null}{d.diagnostic.length ? <QuizForm questions={d.diagnostic} busy={busy} error={error} onDirty={onDirty} write={write} previousResult={d.last_answer} /> : d.history_status === 'confirmed' && !answered ? <p className="personal-meta" role="status">{d.artifacts.find(a => a.kind === 'diagnostic')?.error?.message || '正在准备 3–5 道简短诊断。可靠教材文字不足时会保留原页，不补猜答案。'}</p> : null}
      {d.attempts.length ? <section className="learning-attempts"><h3>做过的题</h3><ul className="learning-simple-list">{d.attempts.slice(-20).reverse().map(a => <li key={a.id}><span><strong>{a.question}</strong><small>{activityLabel[a.activity_type] || '学习练习'} · 我的答案：{a.answer} · {resultLabel[a.result]}{a.retest_success ? ' · 间隔后独立复测正确' : ''}</small>{a.result === 'incorrect' ? <small>{d.artifacts.find(x => x.kind === 'mistake_analysis' && x.payload?.attempt_id === a.id)?.payload?.reason || a.mistake.reason || '错因暂时无法判断；已保留复测建议。'}</small> : null}</span></li>)}</ul></section> : null}<Sources sources={relatedSources} label={relatedSources.some(s => s.document_id) ? '查看原题与相关教材' : '查看原题来源'} /></> : null}</DeskSheet>;
}

function RetestSheet({ id, busy, error, onClose, onDirty, write }) {
  const r = usePersonalResource(`/learning/retests/${encodeURIComponent(id)}`);
  return <DeskSheet title="一道复测，看看是否真的会了" onClose={onClose} busy={busy} wide><ReadState resource={r} />{r.data ? <QuizForm questions={[r.data]} busy={busy} error={error} onDirty={onDirty} write={write} /> : null}</DeskSheet>;
}

function QueuedAnswer({id}){
  const resource=usePersonalResource('/commands/'+encodeURIComponent(id));
  const result=resource.data;
  if(result?.state==='completed'&&result.result?.items)return <QuizForm questions={[]} previousResult={result.result}/>;
  return <section className="learning-quiz-result"><h3>作答已保存</h3><ReadState resource={resource}/><p role="status">{['needs_confirmation','failed'].includes(result?.state)?result.result?.message||result.error?.message||'这份作答需要核对，已经保留。':'整理电脑上线后会自动核对作答，结果会显示在这里。你可以先继续查看教材。'}</p></section>;
}

function QuizForm({ questions, busy, error, onDirty, write, onSaved, previousResult }) {
  const draft = useDraft(Object.fromEntries(questions.map(q => [q.id, ''])));
  const request = useRef(null), [result, setResult] = useState(null);
  const save = async independent => {
    const errors = Object.fromEntries(questions.filter(q => !draft.values[q.id]).map(q => [q.id, '请选择一个答案。']));
    if (!draft.validate(errors)) return;
    const signature = JSON.stringify([draft.values, independent]);
    if (request.current?.signature !== signature) request.current = { signature, id: crypto.randomUUID() };
    await write('/learning/answers', { method: 'POST', body: { answers: draft.values, independent, request_id: request.current.id } }, '真实作答已保存。', value => { setResult(value); onDirty(false); onSaved?.(); });
  };
  const shown = result || previousResult;
  if(shown?.queued)return <QueuedAnswer id={shown.id}/>;
  if (shown) return <section className="learning-quiz-result"><h3>{shown.judgement}</h3><p>{shown.scope}</p>{shown.items.map(a => <div key={a.id}><h4>{a.question}</h4><p>{resultLabel[a.result]} · 你的选择 {a.answer} · 参考答案 {a.correct_key}</p><p>{a.explanation}</p>{a.retest_success ? <p>已记为 Retest Success。</p> : null}</div>)}</section>;
  return <form ref={draft.formRef} className="personal-form learning-quiz" noValidate onKeyDown={preventImeSubmit} onSubmit={e => { e.preventDefault(); if (!busy) save(true); }}><h3>{questions.length > 1 ? `${questions.length} 道简短诊断` : '独立做一道类似题'}</h3><p className="personal-meta">根据已核对的教材要点准备。先自己作答，保存后再显示答案和解析；本次只判断抽查范围。</p>{questions.map((q,i) => <fieldset key={q.id} disabled={busy} aria-describedby={draft.errors[q.id] ? `${q.id}-error` : undefined}><legend>{i+1}. {q.question}</legend>{Object.entries(q.options).map(([key,text]) => <label className="learning-option" key={key}><input type="radio" name={q.id} value={key} checked={draft.values[q.id] === key} aria-invalid={Boolean(draft.errors[q.id])} onChange={() => { draft.field(q.id,key); onDirty(true); }} /><span>{key}. {text}</span></label>)}{draft.errors[q.id] ? <p id={`${q.id}-error`} className="personal-field-error">{draft.errors[q.id]}</p> : null}</fieldset>)}{error ? <p className="personal-field-error" role="alert">{error}</p> : null}<div className="personal-form-actions"><SubmitButton busy={busy}>独立作答，查看结果</SubmitButton><button type="button" className="button button-quiet" disabled={busy} onClick={() => save(false)}>用了提示，按练习保存</button></div></form>;
}

function CaptureSheet({ id, courses, busy, error, onClose, onDirty, onLecture, write }) {
  const r = usePersonalResource(`/learning/captures/${encodeURIComponent(id)}`);
  const draft=useDraft({course:'','note-lecture':'','note-day':''});
  const {course,'note-lecture':lecture,'note-day':day}=draft.values;
  const [saved,setSaved]=useState(false),[commandId,setCommandId]=useState(null),[editing,setEditing]=useState(false);
  const restored=useRef(null),locallyEdited=useRef(false),expectedRevision=useRef(null),refreshRevision=useRef(false);
  const [otherWindow,setOtherWindow]=useState(false);
  const command=usePersonalResource(commandId?'/commands/'+encodeURIComponent(commandId):'',{paused:!commandId,refreshInterval:10000});
  const commandState=commandId?command.data?.state:null;
  const commandFailed=['failed','needs_confirmation'].includes(commandState);
  const waiting=Boolean(commandId)&&!['completed','failed','needs_confirmation'].includes(commandState);
  useEffect(()=>{if(commandState==='completed'||commandFailed){setSaved(!commandFailed&&!locallyEdited.current);if(!commandFailed&&!locallyEdited.current)setEditing(false);if(commandFailed||otherWindow)refreshRevision.current=true;r.retry();}},[commandState]);
  const result=commandState==='completed'&&command.data?.result?.id===id?command.data.result:null;
  const applied=result?.context&&Number.isSafeInteger(command.data?.expected_revision)&&(r.data?._cloud?.revision||0)<=command.data.expected_revision&&!locallyEdited.current;
  const d=applied?{...r.data,...result,_cloud:r.data?._cloud}:r.data;
  useEffect(()=>{
    const pending=d?.pending_confirmation;
    if(pending&&restored.current!==pending.id&&!commandId){
      restored.current=pending.id;setCommandId(pending.id);setEditing(true);setSaved(!locallyEdited.current);setOtherWindow(locallyEdited.current);
      if(!locallyEdited.current)draft.setValues({course:pending.body?.course_id||'','note-lecture':pending.body?.lecture_id||'','note-day':pending.body?.lecture_date||''});
    }
  },[d?.pending_confirmation?.id,commandId]);
  const isNote = d?.context.note_share;
  const effectiveCourse=course||d?.context.course_id;
  const contextReady=isNote&&d.context.course_id&&d.context.lecture_id&&d.context.note_automation?.status!=='needs_context';
  const inventory=usePersonalResource('/school?limit=100'+(effectiveCourse?'&course_id='+encodeURIComponent(effectiveCourse):''),{paused:!isNote||Boolean(d?.lecture_choices?.length)||Boolean(contextReady&&!editing)});
  const lectureChoices=d?.lecture_choices?.length?d.lecture_choices:[...(inventory.data?.items||inventory.data?.lectures||[])].sort((a,b)=>String(b.lecture_date||'').localeCompare(String(a.lecture_date||''))||a.id.localeCompare(b.id)).slice(0,30);
  const chosen=lectureChoices.find(item=>item.id===(lecture||d?.context.lecture_id));
  const edit=()=>{if(!locallyEdited.current)expectedRevision.current=d?._cloud?.revision;locallyEdited.current=true;setEditing(true);setSaved(false);setCommandId(null);onDirty(true);};
  const save=event=>{
    event.preventDefault();if(busy||waiting||saved||refreshRevision.current&&r.loading)return;
    const errors={};if(!effectiveCourse)errors.course='请选择这份材料所属课程。';
    if(isNote&&day&&!validTime(day,'date'))errors['note-day']='请输入有效的课堂日期，格式为 YYYY-MM-DD。';
    if(!draft.validate(errors))return;
    const revision=refreshRevision.current?d?._cloud?.revision:expectedRevision.current??d?._cloud?.revision;
    void write(`/learning/captures/${encodeURIComponent(id)}/confirm`,{method:'POST',...(Number.isSafeInteger(revision)?{expectedRevision:revision}:{}),body:{course_id:effectiveCourse,lecture_id:isNote?lecture||null:null,lecture_date:isNote?day.trim()||null:null}},isNote?'课程与课次选择已保存。':'整页结果已核对。',value=>{
      locallyEdited.current=false;refreshRevision.current=false;expectedRevision.current=null;setOtherWindow(false);setSaved(true);onDirty(false);
      if(value.queued){restored.current=value.id;setCommandId(value.id);}else{setEditing(false);r.retry();}
    });
  };
  const courseChanged=value=>{draft.setValues(current=>({...current,course:value,'note-lecture':'',...(current['note-lecture']?{'note-day':''}:{})}));draft.clearError('course');edit();};
  const lectureChanged=value=>{const item=lectureChoices.find(item=>item.id===value);draft.setValues(current=>({...current,'note-lecture':value,...(item?{course:item.course_id,'note-day':item.lecture_date}:{})}));draft.clearError('note-day');edit();};
  return <DeskSheet title={isNote ? '继续整理课堂笔记' : '核对这一份学习记录'} onClose={onClose} busy={busy} wide><ReadState resource={r} />{d ? <>
    <p>{isNote ? waiting?'原件和本次选择已保存，等待继续整理。':contextReady?'原件已保存，课程和课堂已经关联。':'原件已保存。只需补充无法判断的课程或课次，再继续自动整理。' : '原件已经保存。请对照这一页的题目与结果，一次核对整页即可。'}</p>
    {isNote && !waiting && d.context.course_inference?.confidence === 'medium' ? <p className="personal-meta">建议课程：{d.context.course_inference.course_name}。{d.context.course_inference.reasons?.join(' ')} 请核对后选择。</p> : null}
    {d.error&&!waiting ? <p className="personal-field-error">{d.error.message}</p> : null}<a className="personal-text-button" href={`?personal_view=eric-inbox&inbox_item=${encodeURIComponent(d.raw_item_id)}`} target="_blank" rel="noreferrer">查看 Inbox 原件<ArrowUpRight size={14} /></a>
    {d.parsed?.preview_materials?.length ? <p>预习材料 {d.parsed.preview_materials.length} 项，只记接触，不计入答题表现。</p> : null}
    {d.parsed?.questions.map((q,i) => <div className="learning-captured-question" key={i}><strong>{q.question}</strong><p>我的答案：{q.my_answer || '未发现'} · {resultLabel[q.result]}</p><p className="personal-meta">原文结果：{q.result_quote || '未发现明确结果'}</p></div>)}
    {contextReady&&!editing?<><p>{courses.find(item=>item.id===d.context.course_id)?.name||'已关联课程'} · {chosen?.lecture_date||d.context.confirmed_note_date||'已关联课堂'}</p><div className="personal-form-actions"><button type="button" className="button button-primary" onClick={()=>onLecture(d.context.lecture_id)}>查看课堂与来源</button><button type="button" className="personal-text-button" disabled={busy||waiting} onClick={()=>setEditing(true)}>更正课程或课次</button></div></>:<form ref={draft.formRef} className="personal-form" noValidate onSubmit={save} onKeyDown={preventImeSubmit}>
      {!d.context.course_id||isNote?<Field name="course" label="这份材料属于哪门课" error={draft.errors.course}><select value={course} disabled={busy||waiting} onChange={e=>courseChanged(e.target.value)}><option value="">{d.context.course_id?courses.find(c=>c.id===d.context.course_id)?.name||'沿用已关联课程':'请选择'}</option>{courses.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>:null}
      {isNote?<>{!d?.lecture_choices?.length?<ReadState resource={inventory}/>:null}<Field name="note-lecture" label="已有课次（可直接选择）"><select value={lecture} disabled={busy||waiting} onChange={e=>lectureChanged(e.target.value)}><option value="">按课程与日期自动关联</option>{lectureChoices.filter(item=>!effectiveCourse||item.course_id===effectiveCourse).map(item=><option key={item.id} value={item.id}>{item.lecture_date} · {item.title}</option>)}</select></Field><Field name="note-day" label="课堂日期（补充或纠正时填写）" error={draft.errors['note-day']}><input placeholder="YYYY-MM-DD" value={day} disabled={busy||waiting||Boolean(lecture)} onChange={e=>{draft.field('note-day',e.target.value);edit();}}/></Field></>:null}
      {isNote||d.parsed&&(d.parsed.questions.length||d.parsed.preview_completed_quote||d.parsed.preview_materials?.length)?<SubmitButton busy={busy} disabled={waiting||saved||refreshRevision.current&&r.loading}>{waiting?otherWindow?'等待另一窗口处理':'选择已保存，等待接续':saved?'选择已保存':isNote?'继续自动整理':'这一页信息无误，保存'}</SubmitButton>:<p className="personal-meta">暂未可靠辨认出已提交的题目。原件仍可查看。</p>}
    </form>}
    {commandId?<><ReadState resource={command}/><p role={commandFailed?'alert':'status'} className={commandFailed?'personal-field-error':'personal-meta'}>{commandFailed?command.data.error?.message||'选择尚未应用，请核对后重试；原件和当前选择已保留。':otherWindow?commandState==='completed'?'已读取另一窗口的结果。这里未提交的选择仍保留，请核对后再保存。':'另一窗口正在保存这份笔记，这里未提交的选择仍保留。':commandState==='completed'?'选择已应用，后续整理进度会自动更新。':'选择已保存在此设备，电脑上线后会自动接续；不需要重复提交。'}</p></>:null}
    {error?<p className="personal-field-error" role="alert">{error}</p>:null}
  </> : null}</DeskSheet>;
}

function ExternalSheet({ busy, error, onDirty, onClose, onSave }) {
  const draft = useDraft({ text: '' });
  return <DeskSheet title="保存外部审查" onClose={onClose} busy={busy}><form ref={draft.formRef} className="personal-form" noValidate onKeyDown={preventImeSubmit} onSubmit={e => { e.preventDefault(); if (!busy && draft.validate(draft.values.text.trim() ? {} : { text: '请粘贴审查意见。' })) onSave(draft.values.text); }}><p>Grok Bot 的回复只作第二意见。保存后不会自动改学习记录或创建任务。</p><Field name="text" label="审查意见" error={draft.errors.text}><textarea className="resize-none" rows={12} style={{ resize: 'none' }} maxLength={30000} disabled={busy} value={draft.values.text} onChange={e => { draft.field('text',e.target.value); onDirty(true); }} /></Field>{error ? <p className="personal-field-error" role="alert">{error}</p> : null}<SubmitButton busy={busy}>保存为 External Review</SubmitButton></form></DeskSheet>;
}

export function LearningPreview({ onNavigate }) {
  const r = usePersonalResource('/learning');
  const d = r.data;
  if (!d) return r.loading || r.error ? <section className="personal-section" aria-label="学习记录"><ReadState resource={r} /></section> : null;
  return <LearningFocus data={d} onAll={() => onNavigate('learning')} onClassroom={() => onNavigate('school')} onOpen={() => onNavigate('learning')} onRetest={() => onNavigate('learning')} />;
}
