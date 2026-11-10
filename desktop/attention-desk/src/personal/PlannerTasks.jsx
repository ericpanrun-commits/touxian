import { useRef, useState } from 'react';
import { personalRequest } from './api';
import usePersonalResource from './usePersonalResource';
import TaskRow from './TaskRow';
import { taskDateText } from './planningHelpers';

// A list over existing task IDs, shared by the Windows and PWA route variants.
export default function PlannerTasks({ onOpen, onAll, renderTask, readState: ReadState, onBusyChange = () => {}, preview = false }) {
  const [status, setStatus] = useState('todo'), [page, setPage] = useState(0);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const pending = useRef(false), limit = preview ? 5 : 20;
  const resource = usePersonalResource(`/planning/tasks?status=${status}&limit=${limit}&offset=${page * limit}`);
  const toggle = async task => {
    if (pending.current) return;
    pending.current = true; setBusy(true); onBusyChange(true); setError('');
    try {
      await personalRequest(`/events/${task.event_id}/tasks/${task.id}`, { method: 'PATCH', body: { expected_version: task.event_version, status: task.status === 'done' ? 'todo' : 'done' } });
      setNotice(task.status === 'done' ? '任务已恢复。' : '任务已完成；事项仍由你决定是否结束。');
      window.dispatchEvent(new Event('eric-data-changed'));
    } catch (failure) { setError(failure.message); }
    finally { pending.current = false; setBusy(false); onBusyChange(false); }
  };
  const changeStatus = event => { setStatus(event.target.value); setPage(0); setNotice(''); };
  return <section className="personal-section planner-tasks"><div className="personal-section-heading"><h2>待办任务{resource.data ? ` · ${resource.data.total}` : ''}</h2>{preview ? <button type="button" className="personal-text-button" onClick={onAll}>全部待办</button> : <label>任务状态 <select aria-label="任务状态" value={status} disabled={busy} onChange={changeStatus}><option value="todo">未完成 / 待同步</option><option value="done">已完成</option><option value="all">全部任务</option></select></label>}</div>
    {ReadState ? <ReadState resource={resource} /> : resource.loading && !resource.data ? <p role="status">正在读取任务…</p> : null}
    {(!ReadState && resource.error) || error ? <div role="alert"><p className="personal-field-error">{error || resource.error.message}{resource.data ? ' 下方保留上次结果，可能尚未更新。' : ''}</p><button type="button" className="personal-text-button" disabled={busy} onClick={() => { setError(''); resource.retry(); }}>重新读取任务</button></div> : null}
    {notice ? <p role="status" className="personal-meta">{notice}</p> : null}
    {resource.data?.items.length ? <ul className="cloud-task-list">{resource.data.items.map(task => <li key={task.id}>{renderTask ? renderTask(task) : <TaskRow task={task} checked={task.status === 'done'} disabled={busy || resource.loading} onChange={() => void toggle(task)} onOpen={() => onOpen(task.event_id)}>{task.title}<small>{task.event_title}</small><small>{taskDateText(task)}</small></TaskRow>}</li>)}</ul> : resource.data ? <p className="personal-meta">{page ? '本页暂无任务，可返回上一页。' : '这个范围暂无任务。未设截止日期的任务也会显示在这里。'}</p> : null}
    {!preview && resource.data && (page > 0 || resource.data.total > limit) ? <div className="personal-form-actions"><button type="button" className="button button-quiet" disabled={busy || resource.loading || page === 0} onClick={() => setPage(value => value - 1)}>上一页</button><span>第 {page + 1} 页 · 共 {resource.data.total} 个任务</span><button type="button" className="button button-quiet" disabled={busy || resource.loading || (page + 1) * limit >= resource.data.total} onClick={() => setPage(value => value + 1)}>下一页</button></div> : null}
  </section>;
}
