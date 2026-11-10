import { ArrowUpRight, CalendarDays } from 'lucide-react';
import usePersonalResource from './usePersonalResource';
import { ReadStatus } from './PlanningViews';
import { deadlineDisplay, recentDeadlines } from './planningHelpers';
import PlannerTasks from './PlannerTasks';
import WeChatInbox from './WeChatInbox';
import WeChatKnowledge from './WeChatKnowledge';
import WeChatMediaEntry from './WeChatMediaEntry';
import { LearningPreview } from './LearningWorkspace';

function DateGroup({ title, resource, onOpen, onAll, empty, exclude = [] }) {
  const visible = recentDeadlines(resource.data?.items, exclude);
  return <section className="personal-section desk-date-group"><div className="personal-section-heading"><h2>{title}</h2><button className="desk-icon-button" type="button" aria-label={`查看${title}`} onClick={onAll}><ArrowUpRight size={18} /></button></div><ReadStatus resource={resource} label={title} />
    {visible.length ? <ul className="desk-rows">{visible.map(item => <li key={`${item.event_id}-${item.deadline_id || item.id}`}><button type="button" onClick={() => onOpen(item.event_id)}><span className="desk-row-icon"><CalendarDays size={19} /></span><span><strong>{item.event_title}</strong>{item.course ? <small>{item.course.name}</small> : null}<small>{deadlineDisplay(item)} · {item.label}</small></span><span className={`desk-date-state ${item.state === 'overdue' ? 'is-overdue' : ''}`}>{item.relative_label}</span></button></li>)}</ul> : resource.data ? <div className="desk-quiet-empty"><CalendarDays size={28} strokeWidth={1.3} /><p>{empty}</p></div> : null}
  </section>;
}

export default function TodayDesk({ onNavigate, onCreated, onBusyChange }) {
  const today = usePersonalResource('/planning/today?include_terminal=false');
  const upcoming = usePersonalResource('/planning/upcoming?days=7&include_terminal=false');
  const date = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric', weekday: 'long' }).format(new Date());
  return <><div className="desk-page-heading"><h1 tabIndex={-1}>今天</h1><p>{date}</p></div>
    <LearningPreview onNavigate={onNavigate} />
    <div className="desk-day-grid"><DateGroup title="今天 / 逾期与提醒" resource={today} onOpen={id => onNavigate('detail', id)} onAll={() => onNavigate('calendar')} empty="今天还没有安排，留一点时间给自己。" /><DateGroup title="近期截止" resource={upcoming} exclude={today.data?.items} onOpen={id => onNavigate('detail', id)} onAll={() => onNavigate('upcoming')} empty="未来 7 天暂无已确认的截止事项。" /></div>
    <WeChatInbox preview onAll={() => onNavigate('wechat-inbox')} onSettings={() => onNavigate('wechat-settings')} onCreated={onCreated} onBusyChange={onBusyChange} />
    <PlannerTasks preview onOpen={id => onNavigate('detail', id)} onAll={() => onNavigate('list', null, { taskList: true })} onBusyChange={onBusyChange} /><WeChatMediaEntry onOpen={() => onNavigate('wechat-inbox')} /><WeChatKnowledge onCreated={onCreated} onOpenEvent={id => onNavigate('detail', id)} onBusyChange={onBusyChange} /></>;
}
