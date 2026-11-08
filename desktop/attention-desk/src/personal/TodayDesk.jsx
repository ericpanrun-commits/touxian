import { ArrowUpRight, CalendarDays, CheckCheck } from 'lucide-react';
import usePersonalResource from './usePersonalResource';
import { ReadStatus } from './PlanningViews';
import { deadlineDisplay } from './planningHelpers';
import WeChatInbox from './WeChatInbox';
import WeChatKnowledge from './WeChatKnowledge';
import WeChatMediaEntry from './WeChatMediaEntry';
import { LearningPreview } from './LearningWorkspace';

function DateGroup({ title, resource, onOpen, onAll, empty, todayOnly = false }) {
  const visible = resource.data?.items.filter(item => !todayOnly || item.state !== "overdue") || [];
  const overdue = todayOnly ? resource.data?.items.filter(item => item.state === "overdue") || [] : [];
  return <section className="personal-section desk-date-group"><div className="personal-section-heading"><h2>{title}</h2><button className="desk-icon-button" type="button" aria-label={`查看${title}`} onClick={onAll}><ArrowUpRight size={18} /></button></div><ReadStatus resource={resource} label={title} />
    {visible.length ? <ul className="desk-rows">{visible.slice(0, 4).map(item => <li key={`${item.event_id}-${item.deadline_id || item.id}`}><button type="button" onClick={() => onOpen(item.event_id)}><span className="desk-row-icon"><CalendarDays size={19} /></span><span><strong>{item.event_title}</strong>{item.course ? <small>{item.course.name}</small> : null}<small>{deadlineDisplay(item)} · {item.label}</small></span><span className={`desk-date-state ${item.state === 'overdue' ? 'is-overdue' : ''}`}>{item.relative_label}</span></button></li>)}</ul> : resource.data ? <div className="desk-quiet-empty"><CalendarDays size={28} strokeWidth={1.3} /><p>{empty}</p></div> : null}
    {overdue.length ? <p className="desk-overdue-note">另有 {overdue.length} 个已逾期的时间节点，仍保留在所有事项中。</p> : null}
  </section>;
}

export default function TodayDesk({ onNavigate, onCreated, onBusyChange }) {
  const today = usePersonalResource('/planning/today?include_terminal=false');
  const upcoming = usePersonalResource('/planning/upcoming?days=7&include_terminal=false');
  const actions = usePersonalResource('/events?limit=50&status=active');
  const pending = actions.data?.items.filter(item => item.task_count > item.completed_task_count) || [];
  const date = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric', weekday: 'long' }).format(new Date());
  return <><div className="desk-page-heading"><h1 tabIndex={-1}>今天</h1><p>{date}</p></div>
    <LearningPreview onNavigate={onNavigate} />
    <div className="desk-day-grid"><DateGroup todayOnly title="今天的安排" resource={today} onOpen={id => onNavigate('detail', id)} onAll={() => onNavigate('calendar')} empty="今天还没有安排，留一点时间给自己。" /><DateGroup title="近期截止" resource={upcoming} onOpen={id => onNavigate('detail', id)} onAll={() => onNavigate('upcoming')} empty="未来 7 天暂无已确认的截止事项。" /></div>
    <WeChatInbox preview onAll={() => onNavigate('wechat-inbox')} onSettings={() => onNavigate('wechat-settings')} onCreated={onCreated} onBusyChange={onBusyChange} />
    <section className="personal-section"><div className="personal-section-heading"><h2>需要行动</h2><button type="button" className="personal-text-button" onClick={() => onNavigate('list')}>所有事项<ArrowUpRight size={14} /></button></div><ReadStatus resource={actions} label="行动" />
      {pending.length ? <ul className="desk-rows">{pending.slice(0, 5).map(item => <li key={item.id}><button type="button" onClick={() => onNavigate('detail', item.id)}><span className="desk-row-icon"><CheckCheck size={19} /></span><span><strong>{item.title}</strong>{item.course ? <small>{item.course.name}</small> : null}<small>{item.task_count - item.completed_task_count} 项行动等待处理</small></span><span className="desk-date-state">{item.task_count - item.completed_task_count} 项待办</span></button></li>)}</ul> : actions.data ? <p className="desk-inline-empty">暂时没有待执行的任务。</p> : null}
    </section><WeChatMediaEntry onOpen={() => onNavigate('wechat-inbox')} /><WeChatKnowledge onCreated={onCreated} onOpenEvent={id => onNavigate('detail', id)} onBusyChange={onBusyChange} /></>;
}
