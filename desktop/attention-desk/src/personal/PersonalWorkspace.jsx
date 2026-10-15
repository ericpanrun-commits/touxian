import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, Check, ClipboardPlus, LoaderCircle, Plus, RefreshCw, RotateCcw } from 'lucide-react';
import { personalRequest } from './api';
import { DeadlineForm, EventEditForm, TaskForm } from './Forms';
import { ConfirmDialog, EvidenceLinks, OriginalNotice } from './Evidence';
import ExtractionWorkspace, { RawInbox } from './ExtractionWorkspace';
import EventContext from './EventContext';
import MobileCollectorSettings from './MobileCollectorSettings';
import { CalendarView, OfficialHomePreview, PlanningWorkbench, WorkbenchTabs } from './PlanningViews';
import { ReminderCenter, ReminderSettings } from './ReminderViews';
import BarkSettings from './BarkSettings';
import IcsExport from './IcsExport';
import './personal.css';
import './planning.css';

const STATUS = { needs_review: '待确认', needs_action: '待行动', in_progress: '进行中', done: '已完成', cancelled: '已取消' };
const ORIGIN = { explicit: '原文明确要求', suggested: '辅助建议', manual: '我自行添加' };
const DECISIONS = { event_created: '创建事项', event_edited: '修改事项内容', event_status_changed: '修改事项状态', event_restored: '恢复事项', deadline_added: '添加截止时间', deadline_edited: '修改截止时间', task_added: '添加任务', task_edited: '修改任务', evidence_bound: '绑定原文依据' };
const dateFormatter = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' });
const displayTimestamp = (value) => dateFormatter.format(new Date(value));

function setEventUrl(eventId, rawId = null, settingsOpen = false, collectorOpen = false, view = null) {
  const url = new URL(window.location.href);
  if (eventId) url.searchParams.set('personal_event', eventId);
  else url.searchParams.delete('personal_event');
  if (rawId) url.searchParams.set('personal_raw', rawId);
  else url.searchParams.delete('personal_raw');
  if (settingsOpen) url.searchParams.set('personal_settings', '1');
  else url.searchParams.delete('personal_settings');
  if (collectorOpen) url.searchParams.set('personal_collector', '1');
  else url.searchParams.delete('personal_collector');
  if (view) url.searchParams.set('personal_view', view);
  else url.searchParams.delete('personal_view');
  window.history.replaceState({}, '', url);
}

function listItem(event) {
  return { ...event, task_count: event.tasks.length, completed_task_count: event.tasks.filter((task) => task.status === 'done').length, deadline_count: event.deadlines.length };
}

export default function PersonalWorkspace({ navigationGuardRef }) {
  const initialId = new URLSearchParams(window.location.search).get('personal_event');
  const initialRaw = new URLSearchParams(window.location.search).get('personal_raw');
  const initialSettings = new URLSearchParams(window.location.search).get('personal_settings') === '1';
  const initialCollector = new URLSearchParams(window.location.search).get('personal_collector') === '1';
  const initialView = new URLSearchParams(window.location.search).get('personal_view');
  const [mode, setMode] = useState(initialCollector ? 'collector' : initialSettings ? 'settings' : initialId ? 'detail' : initialRaw ? 'review' : ['today', 'upcoming', 'list', 'calendar', 'reminders', 'reminder-settings', 'bark-settings'].includes(initialView) ? initialView : 'today');
  const [listStatus, setListStatus] = useState('active');
  const [selectedId, setSelectedId] = useState(initialId || initialRaw);
  const [intent, setIntent] = useState(null);
  const [event, setEvent] = useState(null);
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const mutationPending = useRef(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState('');
  const [editor, setEditor] = useState(null);
  const [editorDirty, setEditorDirty] = useState(false);
  const [collectionDirty, setCollectionDirty] = useState(false);
  const [selectedEvidence, setSelectedEvidence] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [reloadVersion, setReloadVersion] = useState(0);
  const headingRef = useRef(null);
  const focusAfterLoad = useRef(false);
  const dirty = editorDirty || collectionDirty;

  useEffect(() => {
    const destination = headingRef.current || document.querySelector('.personal-content h1');
    if (focusAfterLoad.current && !loading && !busy && destination) {
      destination.focus({ preventScroll: true });
      focusAfterLoad.current = false;
    }
  }, [mode, loading, busy, event?.version]);
  useEffect(() => { window.scrollTo({ top: 0, behavior: 'instant' }); }, [mode, selectedId]);

  const requestLeave = (continuation) => {
    if (mutationPending.current) return;
    if (dirty) setDialog({ title: '放弃未保存的修改？', message: '已保存的事项会保留，当前输入尚未保存。', confirmLabel: '放弃修改并离开', action: continuation });
    else continuation();
  };

  useEffect(() => {
    navigationGuardRef.current = requestLeave;
    return () => { navigationGuardRef.current = null; };
  }, [dirty]);

  useEffect(() => {
    const beforeUnload = (unloadEvent) => {
      if (dirty || mutationPending.current) { unloadEvent.preventDefault(); unloadEvent.returnValue = ''; }
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [dirty]);

  useEffect(() => {
    document.title = mode === 'collector' ? '手机收集 · Attention Desk' : mode === 'collect' ? '收集通知 · Attention Desk' : mode === 'review' ? '提取预览 · Attention Desk' : mode === 'settings' ? '提取设置 · Attention Desk' : mode === 'detail' && event ? `${event.title} · Attention Desk` : `${({ today: 'Today', upcoming: 'Upcoming', calendar: 'Calendar', reminders: '提醒中心', 'reminder-settings': '提醒设置', 'bark-settings': 'Bark 手机通知' })[mode] || '个人事项'} · Attention Desk`;
  }, [mode, event?.title]);

  useEffect(() => {
    if (!['list', 'detail'].includes(mode) || (mode === 'detail' && event?.id === selectedId)) { setLoading(false); return undefined; }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    personalRequest(mode === 'detail' ? `/events/${encodeURIComponent(selectedId)}` : `/events?limit=50&status=${listStatus}`, { signal: controller.signal })
      .then((payload) => {
        if (controller.signal.aborted) return;
        if (mode === 'detail') { setEvent(payload); if (intent?.evidenceId) setSelectedEvidence(payload.evidence.find(value => value.id === intent.evidenceId) || null); }
        else { setItems(payload.items); setTotal(payload.total); }
      })
      .catch((requestError) => { if (!controller.signal.aborted) setError(requestError); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [mode, selectedId, event?.id, reloadVersion, listStatus]);

  const navigate = (nextMode, id = null, nextIntent = null) => {
    const performNavigation = () => {
    focusAfterLoad.current = true;
    setEditor(null); setEditorDirty(false); setCollectionDirty(false); setSelectedEvidence(null); setError(null); setNotice('');
    setEvent(null); setSelectedId(id); setMode(nextMode); setIntent(nextIntent); setReloadVersion(current => current + 1);
    setEventUrl(nextMode === 'detail' ? id : null, ['review', 'settings'].includes(nextMode) ? id : null, nextMode === 'settings', nextMode === 'collector', ['today', 'upcoming', 'list', 'calendar', 'reminders', 'reminder-settings', 'bark-settings'].includes(nextMode) ? nextMode : null);
    };
    if (nextIntent) performNavigation(); else requestLeave(performNavigation);
  };

  const applyEvent = (saved) => {
    focusAfterLoad.current = true;
    setEvent(saved); setSelectedId(saved.id); setMode('detail'); setEventUrl(saved.id);
    setItems((current) => [listItem(saved), ...current.filter((value) => value.id !== saved.id)]);
    setEditor(null); setEditorDirty(false); setCollectionDirty(false); setSelectedEvidence(null);
  };

  const mutate = async (path, options, success) => {
    if (mutationPending.current) return;
    mutationPending.current = true; setBusy(true); setError(null); setNotice('');
    try {
      const saved = await personalRequest(path, options);
      applyEvent(saved); setNotice(success);
    } catch (requestError) { setError(requestError); }
    finally { mutationPending.current = false; setBusy(false); }
  };

  const openEditor = (type, value = null) => requestLeave(() => { setEditor({ type, value }); setEditorDirty(false); setError(null); setNotice(''); });
  const cancelEditor = () => { setEditor(null); setEditorDirty(false); setError(null); };
  const status = (next, confirmIncomplete = false) => mutate(`/events/${event.id}/status`, { method: 'POST', body: { expected_version: event.version, status: next, confirm_incomplete: confirmIncomplete } }, `事项${STATUS[next]}`);
  const completeEvent = () => requestLeave(() => {
    const remaining = event.tasks.filter((task) => task.status !== 'done').length;
    if (remaining) setDialog({ title: '仍有未完成任务', message: `还有 ${remaining} 个任务未完成。标记整个事项完成后，任务记录会保留，可恢复继续修改。`, confirmLabel: '仍将事项标记完成', action: () => status('done', true) });
    else status('done');
  });
  const loadMore = async () => {
    if (mutationPending.current) return;
    mutationPending.current = true; setBusy(true); setError(null);
    try {
      const payload = await personalRequest(`/events?limit=50&offset=${items.length}&status=${listStatus}`);
      setItems((current) => [...current, ...payload.items.filter((value) => !current.some((item) => item.id === value.id))]); setTotal(payload.total);
    } catch (requestError) { setError(requestError); }
    finally { mutationPending.current = false; setBusy(false); }
  };
  const refresh = () => requestLeave(() => {
    setEditor(null); setEditorDirty(false); setCollectionDirty(false); setEvent(null); setError(null);
    setReloadVersion((current) => current + 1);
  });
  const terminal = event && ['done', 'cancelled'].includes(event.status);
  const waitingParticipation = event?.context && (event.context.participation !== 'yes' || event.context.relevance === 'no' || event.context.notice_kind === 'cancellation');
  const allComplete = event?.tasks.length > 0 && event.tasks.every((task) => task.status === 'done');
  const openProjection = (id, evidenceId) => navigate('detail', id, evidenceId ? { evidenceId } : null);
  const reportChildBusy = value => { mutationPending.current = value; setBusy(value); };
  const deadlineSource = deadline => event.evidence_snapshots?.[deadline?.source_snapshot_id] || event.snapshot;
  const originalSource = selectedEvidence ? event?.evidence_snapshots?.[selectedEvidence.raw_snapshot_id] || event?.snapshot : event?.snapshot;

  return <div className="personal-workspace">
    <header className="topbar personal-topbar"><div className="group-context"><ClipboardPlus size={19} /><div><span className="context-label">个人工作台</span><strong>收集与事项</strong></div></div>
      <div className="topbar-actions"><button type="button" className="button button-quiet" disabled={busy} onClick={() => navigate('today')}>工作台</button><button type="button" className="button button-quiet" disabled={busy} onClick={() => navigate('calendar')}>Calendar</button><button type="button" className="button button-quiet" disabled={busy} onClick={() => navigate('reminder-settings')}>设置</button><button type="button" className="button button-quiet" disabled={busy} onClick={() => navigate('collector')}>手机收集</button><button type="button" className="button button-primary" disabled={busy} onClick={() => navigate('collect')}><Plus size={16} />收集通知</button></div>
    </header>
    <div className="personal-content">
      <div className="personal-feedback" aria-live="polite" role="status">{notice}</div>
      {error ? <div className="error-banner personal-error" role="alert"><span>{error.message}</span><button type="button" className="button button-quiet" disabled={busy} onClick={() => mode === 'collect' ? navigate('list') : refresh()}>重新读取已保存内容</button></div> : null}
      {loading ? <div className="personal-loading" role="status"><LoaderCircle size={19} className="spin" />正在读取已保存的事项…</div> : null}

      {['settings', 'reminder-settings', 'bark-settings'].includes(mode) ? <div className="planning-views planning-settings-nav" role="group" aria-label="设置类别"><button type="button" className={`button ${mode === 'reminder-settings' ? 'button-primary' : 'button-quiet'}`} aria-pressed={mode === 'reminder-settings'} disabled={busy} onClick={() => navigate('reminder-settings')}>提醒与日历</button><button type="button" className={`button ${mode === 'settings' ? 'button-primary' : 'button-quiet'}`} aria-pressed={mode === 'settings'} disabled={busy} onClick={() => navigate('settings', selectedId)}>AI 提取</button><button type="button" className={`button ${mode === 'bark-settings' ? 'button-primary' : 'button-quiet'}`} aria-pressed={mode === 'bark-settings'} disabled={busy} onClick={() => navigate('bark-settings')}>Bark 手机通知</button></div> : null}
      {['collect', 'review', 'settings'].includes(mode) ? <ExtractionWorkspace key={`${mode}-${selectedId}-${reloadVersion}`} mode={mode} snapshotId={selectedId} intent={intent} onNavigate={navigate}
        onDirtyChange={setCollectionDirty} onBusyChange={reportChildBusy} onCreated={(events, metadata) => { applyEvent(events[0]); setNotice(metadata?.sourceUpdated ? '来源日期已更新。未来提醒与日历会使用确认后的日期，历史保留。' : `已确认并保存 ${events.length} 个事项，任务按你的参与决定创建。`); }} /> : null}
      {mode === 'collector' ? <MobileCollectorSettings onDirtyChange={setCollectionDirty} onBusyChange={reportChildBusy} onBack={() => navigate('today')} /> : null}
      {['today', 'upcoming', 'list'].includes(mode) ? <WorkbenchTabs view={mode} onView={navigate} onReminders={() => navigate('reminders')} /> : null}
      {mode === 'today' ? <OfficialHomePreview /> : null}
      {['today', 'upcoming'].includes(mode) ? <PlanningWorkbench key={mode} view={mode} onOpen={openProjection} onCollect={() => navigate('collect')} /> : null}
      {mode === 'calendar' ? <CalendarView onOpen={openProjection} /> : null}
      {mode === 'reminders' ? <ReminderCenter onOpen={openProjection} onBusyChange={reportChildBusy} /> : null}
      {mode === 'reminder-settings' ? <ReminderSettings key={reloadVersion} onDirtyChange={setCollectionDirty} onBusyChange={reportChildBusy} /> : null}
      {mode === 'bark-settings' ? <BarkSettings key={reloadVersion} onDirtyChange={setCollectionDirty} onBusyChange={reportChildBusy} /> : null}

      {mode === 'list' && !loading && (!error || items.length > 0) ? <section className="personal-section"><div className="personal-section-heading"><h1 ref={headingRef} tabIndex={-1}>我的事项</h1><span className="personal-meta">共 {total} 件</span></div>
        <label className="personal-checkbox-label">事项状态 <select aria-label="事项状态" value={listStatus} disabled={busy} onChange={change => setListStatus(change.target.value)}><option value="active">未完成 / 未取消</option><option value="done">已完成</option><option value="cancelled">已取消</option><option value="all">全部状态</option></select></label>
        {!items.length ? <div className="personal-empty"><h2>还没有收集事项</h2><p>粘贴一条通知，把截止时间和要做的事情保留下来。</p><button type="button" className="button button-primary" onClick={() => navigate('collect')}><Plus size={16} />粘贴第一条通知</button></div> : <ul className="personal-event-list">{items.map((item) => <li key={item.id}><button type="button" className="personal-event-row" onClick={() => navigate('detail', item.id)}>
          <span><strong>{item.title}</strong><small>{item.category} · {item.deadline_count} 个截止时间 · {item.completed_task_count}/{item.task_count} 个任务完成</small><small>更新于 {displayTimestamp(item.updated_at)}</small></span><span className={`personal-status ${item.status}`}>{STATUS[item.status]}</span>
        </button></li>)}</ul>}
        {items.length < total ? <button type="button" className="button button-quiet" disabled={busy} onClick={loadMore}>加载更多事项</button> : null}
      </section> : null}

      {mode === 'list' && !loading ? <RawInbox reloadVersion={reloadVersion} onOpen={id => navigate('review', id)} /> : null}

      {mode === 'detail' && event ? <>
        <button type="button" className="personal-text-button personal-back" disabled={busy} onClick={() => navigate('list')}><ArrowLeft size={15} />返回事项列表</button>
        <section className="personal-section personal-event-header"><div className="personal-section-heading"><div><p className="personal-meta">{event.category}</p><h1 ref={headingRef} tabIndex={-1}>{event.title}</h1></div><span className={`personal-status ${event.status}`}>{STATUS[event.status]}</span></div>
          {event.summary ? <p className="personal-summary">{event.summary}</p> : null}
          {event.source_update ? <p className="planning-source-update">来源已有新版本，已确认日期保持原值。<button type="button" className="personal-text-button" disabled={busy} onClick={() => navigate('review', event.source_update.id)}>核对来源变更</button></p> : null}
          <IcsExport eventId={event.id} future={false} includeTerminal={terminal} />
          <div className="personal-event-actions">
            {terminal ? <button type="button" className="button button-primary" disabled={busy} onClick={() => mutate(`/events/${event.id}/restore`, { method: 'POST', body: { expected_version: event.version } }, '事项已恢复，可继续修改。')}><RotateCcw size={16} />恢复修改</button> : <>
              <button type="button" className="button button-primary" disabled={busy} onClick={completeEvent}><Check size={16} />完成事项</button>
              <button type="button" className="button button-quiet" disabled={busy} onClick={() => openEditor('event')}>编辑事项</button>
              {event.status !== 'in_progress' ? <button type="button" className="button button-quiet" disabled={busy || waitingParticipation} onClick={() => requestLeave(() => status(event.status === 'needs_review' ? 'needs_action' : 'in_progress'))}>{event.status === 'needs_review' ? '确认需要行动' : '开始处理'}</button> : null}
              <button type="button" className="personal-text-button personal-cancel" disabled={busy} onClick={() => requestLeave(() => status('cancelled'))}>取消事项</button>
            </>}
          </div>
          {terminal ? <p className="personal-meta">事项和原文仍保留，恢复后可以继续修改。未自动归档。</p> : null}
          {allComplete && !terminal ? <p className="personal-complete-note">所有任务已完成。你可以确认是否完成整个事项。</p> : null}
          {editor?.type === 'event' ? <EventEditForm key={event.id} event={event} busy={busy} onDirtyChange={setEditorDirty} onCancel={cancelEditor} onSave={(fields) => mutate(`/events/${event.id}`, { method: 'PATCH', body: { ...fields, expected_version: event.version } }, '事项内容已保存。')} /> : null}
        </section>

        <div className="personal-detail-grid"><section className="personal-section"><div className="personal-section-heading"><h2>截止时间</h2><button type="button" className="personal-text-button" disabled={busy || terminal} onClick={() => openEditor('deadline')}><Plus size={15} />添加截止时间</button></div>
          {!event.deadlines.length ? <p className="personal-meta">尚未填写截止时间，可保留多个不同用途的日期。</p> : <ul className="personal-deadline-list">{event.deadlines.map((deadline) => <li className="personal-deadline" key={deadline.id}>
            <div className="personal-section-heading"><strong>{deadline.label}</strong><button type="button" className="personal-text-button" aria-label={`修改截止时间：${deadline.label}`} disabled={busy || terminal} onClick={() => openEditor('deadline', deadline)}>修改</button></div>
            <p className="personal-date-value">{deadline.value.replace('T', ' ') || '日期未知'}{deadline.end_value ? ` — ${deadline.end_value.replace('T', ' ')}` : ''}</p><p className="personal-meta">{({ date: '仅日期 · 具体时刻未说明', minute: '精确到分钟', range: `范围 · ${deadline.range_precision === 'date' ? '全天日期' : '精确时间'}`, unknown: '未知日期' })[deadline.precision]} · {deadline.timezone || '跟随系统时区'} · {deadline.confirmed_by_user && !deadline.uncertain ? '已由你确认' : '待确认 · 不安排提醒'}</p>
            <p className="personal-meta">{event.priority?.deadlines[deadline.id]?.relative_label || '需要确认日期'}</p>
            {deadline.confirmed_by_user && !deadline.uncertain && deadline.precision !== 'unknown' ? <IcsExport deadlineId={deadline.id} future={false} includeTerminal={terminal} /> : null}
            {deadline.original_text ? <p className="personal-meta">原文日期表述：{deadline.original_text}</p> : null}
            <EvidenceLinks evidence={event.evidence} fieldPath={`deadlines.${deadline.id}.value`} onView={setSelectedEvidence} />
          </li>)}</ul>}
          {editor?.type === 'deadline' ? <DeadlineForm key={editor.value?.id || 'new'} deadline={editor.value} evidence={event.evidence.find((value) => value.field_path === `deadlines.${editor.value?.id}.value` && value.raw_snapshot_id === (editor.value?.source_snapshot_id || event.raw_snapshot_id))} original={deadlineSource(editor.value).original_text} blocks={deadlineSource(editor.value).blocks} busy={busy} onDirtyChange={setEditorDirty} onCancel={cancelEditor}
            onSave={(fields) => mutate(`/events/${event.id}/deadlines${editor.value ? `/${editor.value.id}` : ''}`, { method: editor.value ? 'PUT' : 'POST', body: { ...fields, expected_version: event.version } }, '截止时间和依据已保存。')} /> : null}
        </section>

        <section className="personal-section"><div className="personal-section-heading"><h2>任务</h2><button type="button" className="personal-text-button" disabled={busy || terminal || waitingParticipation} onClick={() => openEditor('task')}><Plus size={15} />添加任务</button></div>
          {waitingParticipation ? <p className="personal-meta">先在「我的决定」中确认参加 / 执行，再创建任务。取消通知不生成行动任务。</p> : null}
          <p className="personal-meta">{event.tasks.filter((task) => task.status === 'done').length}/{event.tasks.length} 个任务完成</p>
          {!event.tasks.length ? <p className="personal-meta">把需要做的事情拆成几个步骤。</p> : <ol className="personal-task-list">{event.tasks.map((task) => <li key={task.id} className={task.status === 'done' ? 'personal-task done' : 'personal-task'}>
            <label className="personal-task-title"><input type="checkbox" checked={task.status === 'done'} disabled={busy || terminal} aria-label={`完成任务：${task.title}`} onChange={(changeEvent) => {
              const completed = changeEvent.target.checked;
              requestLeave(() => mutate(`/events/${event.id}/tasks/${task.id}`, { method: 'PATCH', body: { expected_version: event.version, status: completed ? 'done' : 'todo' } }, completed ? '任务已完成。' : '任务已恢复。'));
            }} /><span>{task.title}</span></label>
            <div className="personal-task-meta"><span className="personal-meta">{ORIGIN[task.origin]}{task.deadline_id ? ` · ${event.deadlines.find((value) => value.id === task.deadline_id)?.label || '关联截止时间'}` : ''}</span><EvidenceLinks evidence={event.evidence} fieldPath={`tasks.${task.id}.title`} onView={setSelectedEvidence} /></div>
          </li>)}</ol>}
          {editor?.type === 'task' ? <TaskForm deadlines={event.deadlines} original={event.snapshot.original_text} blocks={event.snapshot.blocks} busy={busy} onDirtyChange={setEditorDirty} onCancel={cancelEditor} onSave={(fields) => mutate(`/events/${event.id}/tasks`, { method: 'POST', body: { ...fields, expected_version: event.version } }, '任务和依据已保存。')} /> : null}
        </section></div>
        <EventContext event={event} busy={busy} terminal={terminal} onView={setSelectedEvidence} onParticipation={participation => requestLeave(() => mutate(`/events/${event.id}/participation`, { method: 'POST', body: { expected_version: event.version, participation } }, participation === 'yes' ? '已参加并创建任务。' : '已忽略，未创建任务。'))} />
        <OriginalNotice key={originalSource.id} snapshot={originalSource} selectedEvidence={selectedEvidence} onClear={() => setSelectedEvidence(null)} />
        <details className="personal-section personal-history"><summary>处理记录</summary><ol>{event.decisions.map((decision) => <li key={decision.id}><strong>{decision.action === 'deadline_source_updated' ? '确认来源日期变更' : DECISIONS[decision.action] || '更新事项'}</strong><span>{displayTimestamp(decision.created_at)}（系统本地时间）</span></li>)}</ol></details>
      </> : null}
      {mode === 'detail' && !loading && !event && !error ? <p>没有找到事项，请返回列表。</p> : null}
    </div>
    {dialog ? <ConfirmDialog title={dialog.title} message={dialog.message} confirmLabel={dialog.confirmLabel} onCancel={() => setDialog(null)} onConfirm={() => { const action = dialog.action; setDialog(null); action(); }} /> : null}
  </div>;
}
