import { useEffect, useRef, useState } from 'react';
import SourceUpdate from './SourceUpdate';
import { AnalysisProgress, AnalysisHistory } from './AnalysisProgress';
import { analysisRequestBody } from './analysisState';
import { LoaderCircle } from 'lucide-react';
import { personalRequest } from './api';
import { ConfirmDialog, OriginalNotice } from './Evidence';
import RawCollectionForm from './RawCollectionForm';
import AISettingsForm from './AISettingsForm';
import ReviewEditor from './ReviewEditor';
import { Field } from './Forms';

const pending = bundle => ['running', 'waiting'].includes(bundle?.extraction?.status);

export function RawInbox({ reloadVersion, onOpen }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const [sourceId, setSourceId] = useState(() => new URLSearchParams(window.location.search).get('review_source') || '');
  const moreRequest = useRef(null);
  const query = `/raw?limit=50${sourceId ? `&source_id=${encodeURIComponent(sourceId)}` : ''}`;
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError('');
    personalRequest(query, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setData(value); })
      .catch(value => { if (!controller.signal.aborted) setError(value.message); });
    return () => { controller.abort(); moreRequest.current?.abort(); };
  }, [reloadVersion, retry, query]);
  const filterSource = value => {
    setSourceId(value);
    const url = new URL(window.location.href);
    if (value) url.searchParams.set('review_source', value); else url.searchParams.delete('review_source');
    window.history.replaceState({}, '', url);
  };
  const more = async () => {
    if (busy) return;
    const controller = new AbortController();
    moreRequest.current = controller;
    setBusy(true); setError('');
    try {
      const next = await personalRequest(`${query}&offset=${data.items.length}`, { signal: controller.signal });
      if (!controller.signal.aborted) setData(current => ({ ...next, items: [...current.items, ...next.items.filter(item => !current.items.some(value => value.raw_snapshot_id === item.raw_snapshot_id))] }));
    } catch (value) { if (!controller.signal.aborted) setError(value.message); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  return <section className="personal-section"><div className="personal-section-heading"><h2>原文与待确认预览</h2><div className="personal-evidence-links"><span className="personal-meta">{data ? `${data.total} 条` : '正在读取…'}</span><button type="button" className="personal-text-button" disabled={busy || !data} onClick={() => setRetry(value => value + 1)}>刷新原文列表</button></div></div>
    <p className="personal-meta">已保存原文；只有确认后才创建正式事项。</p>
    <Field name="review_source" label="原文来源"><select value={sourceId} disabled={busy || !data} onChange={event => filterSource(event.target.value)}><option value="">全部来源</option>{data?.sources.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</select></Field>
    {error ? <div role="alert"><p className="personal-field-error">{error}</p><button type="button" className="button button-quiet" disabled={busy} onClick={() => setRetry(value => value + 1)}>重新读取原文列表</button></div> : null}
    {data?.items.length ? <ul className="personal-event-list">{data.items.map(item => <li key={item.raw_snapshot_id}><button type="button" className="personal-event-row" onClick={() => onOpen(item.raw_snapshot_id)}>
      <span><strong>{item.title_hint || '已保存的通知'}</strong><small>{item.source_name}{item.published_at ? ` · 发布于 ${item.published_at}` : ''}{item.snapshot_version ? ` · 原文第 ${item.snapshot_version} 版` : ''}{item.attachment_count ? ` · ${item.attachment_count} 个文件 · ${item.parsed_attachment_count || 0} 个已解析` : ''}</small><small>{item.preview}</small></span><span className="personal-status needs_review">{item.status === 'cancelled' ? '已取消整理' : ['running', 'waiting'].includes(item.extraction_status) ? '分析中' : item.extraction_status === 'failed' ? '分析失败 · 原文保留' : !item.extraction_status ? '待分析 / 手动整理' : '待确认'}</span>
    </button></li>)}</ul> : data ? <p className="personal-meta">暂无未确认原文。</p> : null}
    {data && data.items.length < data.total ? <button type="button" className="button button-quiet" disabled={busy} onClick={more}>加载更多原文</button> : null}
  </section>;
}

export default function ExtractionWorkspace({ mode, snapshotId, intent, onNavigate, onCreated, onDirtyChange, onBusyChange }) {
  const [bundle, setBundle] = useState(null);
  const [configuration, setConfiguration] = useState(null);
  const [loading, setLoading] = useState(mode !== 'collect');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [selectedEvidence, setSelectedEvidence] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [retry, setRetry] = useState(0);
  const [manual, setManual] = useState(intent === 'manual');
  const [editorVersion, setEditorVersion] = useState(0);
  const [documentJob, setDocumentJob] = useState(null);
  const [sourceUpdateTarget, setSourceUpdateTarget] = useState(null);
  const inFlight = useRef(false);
  const analysisRequest = useRef(null);
  const started = useRef(false);
  const headingRef = useRef(null);
  const dirty = useRef(false);
  const reportDirty = value => { dirty.current = value; onDirtyChange(value); };
  useEffect(() => {
    const controller = new AbortController();
    if (mode === 'collect') { setLoading(false); return () => controller.abort(); }
    setLoading(true); setError('');
    personalRequest(mode === 'settings' ? '/ai-settings' : `/raw/${encodeURIComponent(snapshotId)}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) { if (mode === 'settings') setConfiguration(value); else { setBundle(value); const job = value.snapshot.attachment_jobs?.find(j => ['running', 'waiting'].includes(j.status)); if (job) setDocumentJob(job); } } })
      .catch(value => { if (!controller.signal.aborted) setError(value.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [mode, snapshotId, retry]);
  useEffect(() => {
    if (!documentJob || !['waiting', 'running'].includes(documentJob.status)) return undefined;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      personalRequest(`/document-jobs/${documentJob.id}`, { signal: controller.signal }).then(async job => {
        if (controller.signal.aborted) return;
        setDocumentJob(job);
        if (job.result_snapshot_id) { reportDirty(false); onNavigate('review', job.result_snapshot_id, intent === 'documents-analyze' ? 'analyze' : 'manual'); }
        else if (job.status === 'failed') { setError(job.error?.map(e => e.message).join('；') || '文件解析未完成，原文件保留。'); setBundle(await personalRequest(`/raw/${snapshotId}`, { signal: controller.signal })); }
      }).catch(value => { if (!controller.signal.aborted) { setError(value.message); setDocumentJob(current => ({ ...current })); } });
    }, 1000);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [documentJob, snapshotId, intent]);
  useEffect(() => { if (!loading) headingRef.current?.focus(); }, [loading]);
  useEffect(() => {
    if (mode !== 'review' || !pending(bundle)) return undefined;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      personalRequest(`/raw/${encodeURIComponent(snapshotId)}`, { signal: controller.signal }).then(value => {
        if (!controller.signal.aborted) { setBundle(current => !current || value.version >= current.version ? value : current); setError(''); }
      }).catch(value => { if (!controller.signal.aborted) { setError(value.message); setRetry(current => current + 1); } });
    }, 1200);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [mode, snapshotId, bundle]);
  const run = async action => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); onBusyChange(true); setError(''); setNotice('');
    try { await action(); }
    catch (value) { setError(value.message); }
    finally { inFlight.current = false; setBusy(false); onBusyChange(false); }
  };
  const analyze = (force = false, confirmBudget = false, scope = 'auto') => run(async () => {
    const body = analysisRequestBody(force, confirmBudget, scope);
    const fingerprint = JSON.stringify(body);
    if (!analysisRequest.current || analysisRequest.current.fingerprint !== fingerprint) analysisRequest.current = { fingerprint, key: crypto.randomUUID() };
    let value;
    try { value = await personalRequest(`/raw/${snapshotId}/analyze`, { method: 'POST', body, headers: { 'Idempotency-Key': analysisRequest.current.key } }); }
    catch (failure) {
      if (failure.code === 'budget_confirmation_required') {
        setDialog({ title: '分析更多文档区域？', message: failure.message, confirmLabel: '确认预算并继续', action: () => analyze(force, true, scope) });
        return;
      }
      throw failure;
    }
    analysisRequest.current = null; setBundle(value); setManual(false); reportDirty(false); setEditorVersion(current => current + 1);
    setNotice(pending(value) ? '原文已保存，正在分析。可以离开，回来继续核对。' : '已读取保存的分析结果，本次没有重复调用。');
  });
  const forceAnalysis = (scope = bundle?.extraction?.plan?.scope || 'auto') => setDialog({ title: bundle?.extraction?.plan ? '继续分析未完成的区域？' : '重新付费分析这条原文？',
    message: bundle?.extraction?.plan ? '成功区域复用缓存。本轮最多分析 32 个新区域、48 次 HTTP 请求、100 万 token 的保守预算；可随时取消。将生成新分析版本并替换预览草稿，原文和已有分析保留。' : '会生成一个新的分析版本，当前预览修改将被替换。原文和以往分析记录保留。',
    confirmLabel: '继续分析', action: () => analyze(true, Boolean(bundle?.extraction?.plan), scope) });
  const cancelAnalysis = () => run(async () => {
    setBundle(await personalRequest(`/raw/${snapshotId}/analysis-cancel`, { method: 'POST', body: {} }));
    setNotice('分析已取消，原文和已经成功的区域保留。');
  });
  const documentsPending = documentJob && ['waiting', 'running'].includes(documentJob.status);
  const filesUnparsed = bundle?.snapshot.attachments?.length && !bundle.snapshot.blocks?.length && !bundle.snapshot.web;
  const parseAttachment = (attachment, confirmed = false) => {
    const lastErrors = documentJob?.error || bundle?.snapshot.attachment_jobs?.[0]?.error || [];
    const requiresLarge = lastErrors.some(e => e.attachment_id === attachment.id && e.code === 'large_file_confirmation');
    const action = () => run(async () => {
      const job = await personalRequest(`/raw/${snapshotId}/documents`, { method: 'POST', body: { attachment_ids: [attachment.id], confirm_large: confirmed, redownload: attachment.parse_status === 'parsed' && Boolean(attachment.original_url) } });
      setDocumentJob(job); setNotice('原文件会保存在本机，正在解析。完成后打开新的原文版本。');
    });
    if (!confirmed && (attachment.size > 15000000 || requiresLarge)) setDialog({ title: '解析较大的文件？', message: '文件超过 15 MB，本地解析可能需要较长时间。单文件最多 50 MB，原文件保留。', confirmLabel: '继续解析', action: () => parseAttachment(attachment, true) });
    else action();
  };
  const cancel = () => setDialog({ title: '取消本次整理？', message: '原文和已保存草稿保留，未保存的预览修改将被放弃。可以从原文列表继续整理。', confirmLabel: '取消整理并保留原文', action: () => run(async () => {
    setBundle(await personalRequest(`/raw/${snapshotId}/cancel`, { method: 'POST', body: { expected_version: bundle.version } })); reportDirty(false); setNotice('已取消整理，原文仍保留。');
  }) });
  const saveSettings = (payload, clearKey) => run(async () => {
    const saved = await personalRequest('/ai-settings', { method: 'PUT', body: payload });
    clearKey(); setConfiguration(saved); reportDirty(false); setEditorVersion(current => current + 1); setNotice('提取设置已保存。');
  });
  useEffect(() => {
    if (mode === 'review' && intent === 'analyze' && bundle && !started.current) {
      started.current = true;
      analyze();
    }
  }, [bundle, intent, mode]);
  const showEditor = bundle?.status === 'pending' && !pending(bundle) && !documentsPending && !filesUnparsed && (manual || bundle.extraction || bundle.draft);
  return <>
    {error ? <div className="error-banner personal-error" role="alert"><span>{error}</span><button type="button" className="button button-quiet" disabled={busy} onClick={() => onNavigate(mode, snapshotId)}>重新读取已保存内容</button></div> : null}
    <div className="personal-feedback" role="status" aria-live="polite">{notice}</div>
    {loading ? <p className="personal-loading" role="status"><LoaderCircle size={19} className="spin" />读取已保存内容…</p> : null}
    {mode === 'collect' ? <section className="personal-section personal-collection"><h1 ref={headingRef} tabIndex={-1}>收集通知</h1><p className="personal-intro">粘贴原文，或添加文件、截图。先保存，再核对并创建事项。</p>
      <RawCollectionForm busy={busy} onDirtyChange={reportDirty} onCancel={() => onNavigate('list')} onSave={(payload, requestId, shouldAnalyze, files, confirmed) => run(async () => {
        let body = payload;
        if (files?.length) { body = new FormData(); files.forEach(file => body.append('files', file)); body.append('original_text', payload.original_text); body.append('title_hint', payload.title_hint); body.append('source_time', payload.source_time || ''); body.append('confirm_large', String(confirmed)); }
        const saved = await personalRequest(files?.length ? '/collect-files' : '/raw', { method: 'POST', headers: { 'Idempotency-Key': requestId }, body });
        // Navigation follows durable raw storage, even if the analysis request fails.
        reportDirty(false);
        onNavigate('review', saved.snapshot.id, files?.length ? shouldAnalyze ? 'documents-analyze' : 'manual' : shouldAnalyze ? 'analyze' : 'manual');
      })} />
    </section> : null}
    {mode === 'settings' && configuration ? <section className="personal-section personal-collection"><h1 ref={headingRef} tabIndex={-1}>提取设置</h1><p className="personal-intro">通知分析时，原文会发送到所配置的服务地址。密钥只保存到本机后端。</p>
      <AISettingsForm key={editorVersion} configuration={configuration} busy={busy} onDirtyChange={reportDirty} onSave={saveSettings} onCancel={() => onNavigate(snapshotId ? 'review' : 'list', snapshotId)} />
    </section> : null}
    {mode === 'review' && bundle ? <>
      <section className="personal-section"><h1 ref={headingRef} tabIndex={-1}>AI 提取结果预览</h1><p className="personal-intro">原文已保存。未经你的确认，不创建正式事项、截止时间或任务。</p>
        {bundle.snapshot.latest_document_snapshot_id ? <p className="personal-meta">这条通知已有解析后的新原文版本。<button type="button" className="personal-text-button" disabled={busy} onClick={() => onNavigate('review', bundle.snapshot.latest_document_snapshot_id, 'manual')}>打开已解析原文</button></p> : null}
        {bundle.status === 'confirmed' ? <><p>这条原文已经确认。</p>{bundle.confirmed_event_ids?.map(id => <button key={id} type="button" className="button button-quiet" onClick={() => onNavigate('detail', id)}>查看正式事项</button>)}</> : bundle.status === 'cancelled' ? <><p>整理已取消，原文和分析记录保留。</p><button type="button" className="button button-primary" disabled={busy} onClick={() => run(async () => { setBundle(await personalRequest(`/raw/${snapshotId}/resume`, { method: 'POST', body: { expected_version: bundle.version } })); setManual(true); })}>继续整理</button></> : <>
          {pending(bundle) ? <p className="personal-loading" role="status"><LoaderCircle size={19} className="spin" />DeepSeek 正在分析，候选结果尚未就绪…</p> : null}
          <AnalysisProgress extraction={bundle.extraction} busy={busy} onCancel={cancelAnalysis} />
          {documentsPending ? <p className="personal-loading" role="status"><LoaderCircle size={19} className="spin" />{documentJob.phase || '本地解析中'}… 可以离开，解析进度会保留。</p> : null}
          {bundle.snapshot.input_budget && !bundle.extraction?.plan ? <p className="personal-meta">分析范围：{bundle.snapshot.input_budget.selected_count}/{bundle.snapshot.input_budget.total_count} 个来源块，共 {bundle.snapshot.input_budget.characters} 字。全文保存在本机。</p> : null}
          {!pending(bundle) && bundle.extraction?.plan?.omitted?.length ? <button type="button" className="button button-quiet" disabled={busy || Boolean(sourceUpdateTarget)} onClick={() => forceAnalysis('remaining')}>分析尚未覆盖区域</button> : null}
          {bundle.extraction?.error ? <div role="alert" className="personal-analysis-error"><strong>分析未完成 · 原文已保留</strong><p>{bundle.extraction.error.message}</p></div> : null}
          {!pending(bundle) ? <div className="personal-form-actions"><button type="button" className="button button-primary" disabled={busy || documentsPending || Boolean(filesUnparsed) || Boolean(sourceUpdateTarget)} onClick={() => bundle.extraction ? forceAnalysis() : analyze() }>{bundle.extraction ? '重新分析' : '使用 DeepSeek 分析'}</button><button type="button" className="button button-quiet" disabled={busy || documentsPending || Boolean(filesUnparsed) || Boolean(sourceUpdateTarget)} onClick={() => { setManual(true); setNotice('可以手动填写并确认；原分析记录保留。'); }}>继续手动整理</button>{bundle.snapshot.web && !showEditor ? <button type="button" className="button button-quiet" disabled={busy || Boolean(sourceUpdateTarget)} onClick={cancel}>忽略这条通知</button> : null}<button type="button" className="personal-text-button" disabled={busy} onClick={() => onNavigate('settings', snapshotId)}>提取设置</button></div> : null}
          <SourceUpdate bundle={bundle} target={sourceUpdateTarget} busy={busy || documentsPending || pending(bundle)} onDirtyChange={reportDirty}
            onStart={target => { const action = () => { reportDirty(false); setSourceUpdateTarget(target); }; if (dirty.current) setDialog({ title: '放弃未保存的预览修改？', message: '接下来核对已有时间节点，当前未保存的预览修改会被放弃。原文和正式事项保留。', confirmLabel: '核对已有时间节点', action }); else action(); }}
            onCancel={() => { const action = () => { reportDirty(false); setSourceUpdateTarget(null); }; if (dirty.current) setDialog({ title: '放弃日期变更草稿？', message: '已有日期尚未改变，当前核对输入将被放弃。', confirmLabel: '放弃变更草稿', action }); else action(); }}
            onSave={fields => setDialog({ title: '应用来源日期变更？', message: `${sourceUpdateTarget.event.title} / ${sourceUpdateTarget.deadline.label}：${sourceUpdateTarget.deadline.value.replace('T', ' ')} → ${fields.value.replace('T', ' ')}${fields.end_value ? ` — ${fields.end_value}` : ''}。将更新同一时间节点，重算未来提醒并更新日历；历史和旧来源保留。`, confirmLabel: '确认更新日期', action: () => run(async () => {
              const saved = await personalRequest(`/events/${sourceUpdateTarget.event.id}/deadlines/${sourceUpdateTarget.deadline.id}/source-update`, { method: 'POST', body: { ...fields, expected_version: sourceUpdateTarget.event.version, source_snapshot_id: bundle.snapshot.id, acknowledged_source_change: true } }); reportDirty(false); onCreated([saved], { sourceUpdated: true });
            }) })} />
          {showEditor && !sourceUpdateTarget ? <ReviewEditor key={`${bundle.extraction_id || 'manual'}-${editorVersion}`} bundle={bundle} busy={busy} onDirtyChange={reportDirty} onView={setSelectedEvidence} onCancel={cancel}
            onSaveDraft={draft => run(async () => { setBundle(await personalRequest(`/raw/${snapshotId}/draft`, { method: 'PUT', body: { expected_version: bundle.version, draft } })); reportDirty(false); setNotice('预览草稿已保存，尚未创建正式事项。'); })}
            onConfirm={payload => run(async () => { const saved = await personalRequest(`/raw/${snapshotId}/confirm`, { method: 'POST', body: payload }); reportDirty(false); onCreated(saved.events); })} /> : null}
        </>}
      </section>
      <OriginalNotice snapshot={bundle.snapshot} selectedEvidence={selectedEvidence} onClear={() => setSelectedEvidence(null)} busy={busy || documentsPending || pending(bundle)} onParse={parseAttachment} />
      {bundle.extraction ? <details className="personal-section personal-history"><summary>分析记录</summary><p className="personal-meta">{bundle.extraction.provider} / {bundle.extraction.model} · thinking {bundle.extraction.thinking ? '开' : '关'} · {bundle.extraction.prompt_version} · schema {bundle.extraction.schema_version}</p><p className="personal-meta">状态：{bundle.extraction.status} · 耗时：{bundle.extraction.latency_ms ?? '—'} ms · tokens：{bundle.extraction.token_usage?.total_tokens ?? '未返回'}{bundle.extraction.reused_from_id ? ' · 复用缓存' : ''}</p></details> : null}
      {bundle.extraction?.plan ? <AnalysisHistory key={bundle.extraction.id} extraction={bundle.extraction} snapshot={bundle.snapshot} busy={busy} onView={setSelectedEvidence} /> : null}
    </> : null}
    {dialog ? <ConfirmDialog title={dialog.title} message={dialog.message} confirmLabel={dialog.confirmLabel} onCancel={() => setDialog(null)} onConfirm={() => {
      const action = dialog.action; setDialog(null); action();
    }} /> : null}
  </>;
}
