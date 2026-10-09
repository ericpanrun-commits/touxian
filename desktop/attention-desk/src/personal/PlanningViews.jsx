import { useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, FileText, RefreshCw } from 'lucide-react';
import { Field } from './Forms';
import usePersonalResource from './usePersonalResource';
import { deadlineDisplay, EVENT_STATUS, groupEvents, itemsOnDay, monthDays, shiftMonth } from './planningHelpers';
import IcsExport from './IcsExport';

export function ReadStatus({ resource, label = '事项' }) {
  return <>{resource.loading ? <p className="personal-meta" role="status">正在读取{label}…</p> : null}{resource.error ? <div role="alert" className="personal-analysis-error">
    <p>{resource.error.message}{resource.data ? ' 下方保留上次读取结果，可能尚未更新。' : ''}</p><button type="button" className="button button-quiet" onClick={resource.retry}>重新读取{label}</button>
  </div> : null}</>;
}

export function DeadlineProjection({ item, onOpen }) {
  return <li className="planning-deadline">
    <div className="personal-section-heading"><strong>{item.label}</strong><span className={`personal-status ${item.state === 'overdue' ? 'planning-overdue' : ''}`}>{item.state === 'overdue' ? 'OVERDUE · 逾期' : item.relative_label}</span></div>
    <p className="personal-date-value">{deadlineDisplay(item)}</p>
    <p className="personal-meta">{item.all_day ? '全天 · 日期精度' : `精确时间 · ${item.display_timezone || item.timezone}`} · {item.precision_label}{item.reasons?.length ? ` · ${item.reasons.join('；')}` : ''}</p>
    {item.tasks.length ? <ul className="planning-task-summary" aria-label="相关未完成任务">{item.tasks.map(task => <li key={task.id}>{task.title}</li>)}</ul> : <p className="personal-meta">没有关联的未完成任务。</p>}
    <div className="personal-evidence-links"><button type="button" className="personal-text-button" onClick={() => onOpen(item.event_id)}>查看事项</button>{item.evidence.map(proof => <button key={proof.id} type="button" className="personal-text-button" onClick={() => onOpen(item.event_id, proof.id)}><FileText size={14} />查看依据{proof.page ? ` · 第 ${proof.page} 页` : proof.section ? ` · ${proof.section}` : ''}</button>)}</div>
  </li>;
}

function EventGroups({ items, onOpen }) {
  return groupEvents(items).map(event => <article key={event.id} className="planning-event"><div className="personal-section-heading">
    <h2><button type="button" className="planning-event-link" onClick={() => onOpen(event.id)}>{event.title}</button></h2><span className={`personal-status ${event.status}`}>{EVENT_STATUS[event.status]}</span>
  </div><ul className="personal-deadline-list">{event.items.map(item => <DeadlineProjection key={item.id} item={item} onOpen={onOpen} />)}</ul></article>);
}

export function WorkbenchTabs({ view, onView, onReminders }) {
  return <div className="planning-toolbar"><div className="planning-views" role="group" aria-label="工作台视图">
    {[['today', 'Today · 今天'], ['upcoming', 'Upcoming · 未来'], ['list', '全部事项']].map(([key, label]) => <button key={key} type="button" className={`button ${view === key ? 'button-primary' : 'button-quiet'}`} aria-pressed={view === key} onClick={() => onView(key)}>{label}</button>)}
  </div><button type="button" className="personal-text-button" onClick={onReminders}>提醒中心</button></div>;
}

export function PlanningWorkbench({ view, onOpen, onCollect }) {
  const [days, setDays] = useState(() => { const value = Number(new URLSearchParams(window.location.search).get('upcoming_days')); return [7, 14, 30].includes(value) ? value : 7; });
  const [includeTerminal, setIncludeTerminal] = useState(false);
  const resource = usePersonalResource(`/planning/${view}?days=${days}&include_terminal=${includeTerminal}`);
  const title = view === 'today' ? '今天要处理什么' : `未来 ${days} 天`;
  const heading = useRef(null);
  const changeDays = value => { setDays(value); const url = new URL(window.location.href); url.searchParams.set('upcoming_days', String(value)); window.history.replaceState({}, '', url); };
  return <section className="personal-section planning-workbench"><div className="personal-section-heading"><div><h1 ref={heading} tabIndex={-1}>{title}</h1><p className="personal-meta">{resource.data?.today || ''}{resource.data ? ` · ${resource.data.timezone} · ${resource.data.total} 个时间节点` : '读取已确认的日期和任务'}</p></div><button type="button" className="personal-text-button" disabled={resource.loading} onClick={resource.retry}><RefreshCw size={15} />刷新</button></div>
    <div className="planning-toolbar">{view === 'upcoming' ? <Field name="upcoming_days" label="查看范围"><select value={days} onChange={event => changeDays(Number(event.target.value))}><option value={7}>未来 7 天</option><option value={14}>未来 14 天</option><option value={30}>未来 30 天</option></select></Field> : <p className="personal-meta">逾期仍需处理；任务和事项由你决定是否完成。</p>}<label className="personal-checkbox-label"><input type="checkbox" checked={includeTerminal} onChange={event => setIncludeTerminal(event.target.checked)} />包含已完成 / 已取消</label></div>
    <ReadStatus resource={resource} />
    {resource.data?.items.length ? <EventGroups items={resource.data.items} onOpen={onOpen} /> : resource.data && !resource.loading ? <div className="personal-empty"><h2>{view === 'today' ? '今天没有待处理的日期或提醒' : '这个范围内暂无已确认日期'}</h2><p>未确定的日期请在事项中核对，确认后才会进入日历和提醒。</p><button type="button" className="button button-quiet" onClick={onCollect}>收集通知</button></div> : null}
  </section>;
}

export function CalendarView({ onOpen }) {
  const now = new Date();
  const [month, setMonth] = useState(() => { const text = new URLSearchParams(window.location.search).get('calendar_month') || ''; const match = /^(\d{4})-(\d{2})$/.exec(text); return match && Number(match[1]) >= 1970 && Number(match[1]) < 9999 && Number(match[2]) > 0 && Number(match[2]) <= 12 ? { year: Number(match[1]), month: Number(match[2]) } : { year: now.getFullYear(), month: now.getMonth() + 1 }; });
  const initialDay = `${month.year}-${String(month.month).padStart(2, '0')}-01`;
  const [selected, setSelected] = useState(initialDay);
  const [includeTerminal, setIncludeTerminal] = useState(false);
  const resource = usePersonalResource(`/planning/calendar?year=${month.year}&month=${month.month}&include_terminal=${includeTerminal}`);
  const items = resource.data?.items || [];
  const days = monthDays(month.year, month.month);
  const selectedItems = itemsOnDay(items, selected);
  const selectMonth = next => {
    if (next.year < 1970 || next.year > 9998) return;
    setMonth(next); setSelected(`${next.year}-${String(next.month).padStart(2, '0')}-01`);
    const url = new URL(window.location.href); url.searchParams.set('calendar_month', `${next.year}-${String(next.month).padStart(2, '0')}`); window.history.replaceState({}, '', url);
  };
  return <section className="personal-section planning-calendar"><div className="personal-section-heading"><div><h1 tabIndex={-1}>Calendar · 日历</h1><p className="personal-meta">仅显示已确认日期 · 日期型为全天；时间型按当前系统时区显示。</p></div><IcsExport includeTerminal={includeTerminal} /></div>
    <div className="planning-toolbar"><div className="planning-month-switch"><button type="button" className="button button-quiet" aria-label="上个月" disabled={month.year === 1970 && month.month === 1} onClick={() => selectMonth(shiftMonth(month.year, month.month, -1))}><ChevronLeft size={16} /></button><h2 aria-live="polite">{month.year} 年 {month.month} 月</h2><button type="button" className="button button-quiet" aria-label="下个月" disabled={month.year === 9998 && month.month === 12} onClick={() => selectMonth(shiftMonth(month.year, month.month, 1))}><ChevronRight size={16} /></button><button type="button" className="personal-text-button" onClick={() => { selectMonth({ year: now.getFullYear(), month: now.getMonth() + 1 }); setSelected(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`); }}>今天</button></div><label className="personal-checkbox-label"><input type="checkbox" checked={includeTerminal} onChange={event => setIncludeTerminal(event.target.checked)} />包含已完成 / 已取消</label></div>
    <ReadStatus resource={resource} label="日历" />
    <table className="planning-month"><caption>{month.year} 年 {month.month} 月，选择日期查看时间节点</caption><thead><tr>{['一', '二', '三', '四', '五', '六', '日'].map(day => <th scope="col" key={day}>周{day}</th>)}</tr></thead><tbody>{Array.from({ length: days.length / 7 }, (_, week) => <tr key={week}>{days.slice(week * 7, week * 7 + 7).map(day => {
      const entries = itemsOnDay(items, day); const inMonth = Number(day.slice(5, 7)) === month.month;
      return <td key={day}><button type="button" disabled={!inMonth || resource.loading || (!resource.data && Boolean(resource.error))} aria-pressed={selected === day} aria-current={resource.data?.today === day ? 'date' : undefined} aria-label={`${day}，${entries.length} 个时间节点`} onClick={() => setSelected(day)} className={selected === day ? 'is-selected' : ''}><span>{Number(day.slice(8))}</span>{entries.length ? <strong>{entries.length}<span className="planning-day-unit"> 项</span></strong> : <span className="planning-empty-day">—</span>}</button></td>;
    })}</tr>)}</tbody></table>
    <div className="planning-day-detail" aria-live="polite"><h2>{selected} · {selectedItems.length} 个时间节点</h2>{selectedItems.length ? <EventGroups items={selectedItems} onOpen={onOpen} /> : resource.data ? <p className="personal-meta">这一天没有已确认的时间节点。</p> : null}</div><p className="personal-meta">ICS 为手动导出。重复导入的更新行为由日历软件决定，不提供订阅或双向同步。</p>
  </section>;
}
