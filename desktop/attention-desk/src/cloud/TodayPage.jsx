import { useState } from 'react';
import { ArrowUpRight, BookOpen, CalendarDays, CheckCheck, ChevronRight, Clock3, FileText, Inbox, MapPin, Plus, Sunset } from 'lucide-react';
import usePersonalResource from '../personal/usePersonalResource';
import CourseCard, { CourseArtwork } from '../personal/CourseCard';
import { classDetails, classTime, courseDetails, studyLocation } from '../personal/coursePresentation';
import { learningFocus } from '../personal/learningFocusModel';
import { COURSE_THEMES, courseTheme } from '../personal/courseVisuals';
import './today.css';

function Group({ title, count, action, onAction, className = '', children }) {
  return <section className={`personal-section today-group ${className}`}><div className="today-group-heading"><h2>{title}{count != null ? <span className="today-count">{count}</span> : null}</h2>{action ? <button type="button" className="personal-text-button" onClick={onAction}>{action}<ChevronRight size={14}/></button> : null}</div>{children}</section>;
}
function Quiet({ icon: Icon = CheckCheck, children }) { return <div className="today-quiet"><Icon size={24} strokeWidth={1.4}/><p>{children}</p></div>; }

function Schedule({ classes, navigate }) {
  return <ol className="today-timeline">{classes.map(item => {
    const details = classDetails(item);
    return <li key={item.uid} className={`is-${item.state}`}><span className="timeline-clock"><time dateTime={item.start}>{classTime(item.start)}</time><time dateTime={item.end}>{classTime(item.end)}</time></span><span className="timeline-track" aria-hidden="true"><i/></span>
      <button type="button" className="timeline-course" onClick={() => navigate(item.course_id ? 'course' : 'capture', item.course_id)}><span className="timeline-copy"><strong>{item.title}</strong>{details.location ? <span><MapPin size={12}/>{details.location}</span> : <span>教室尚未提供</span>}<small>{details.teacher}{details.teacher ? ' · ' : ''}{item.state === 'finished' ? '已结束' : item.state === 'in_progress' ? '正在上课' : '稍后开始'}</small></span><CourseArtwork course={item.title} className="timeline-art"/><ChevronRight size={15} className="timeline-chevron"/></button></li>;
  })}</ol>;
}

export default function TodayPage({ data, resource, navigate, courses, localItems, TaskCheck, onComplete, taskError, ReadState, receipts }) {
  const upcoming = usePersonalResource('/planning/upcoming?days=7&include_terminal=false');
  const learning = usePersonalResource('/learning', { refreshInterval: 60_000 });
  const [coursePage, setCoursePage] = useState('all');
  if (!data) return resource.error ? null : <div className="today-loading" role="status"><CalendarDays size={28}/><p>正在读取今天的课程与安排…</p></div>;
  const classes = data.classes || [], tasks = data.tasks || [], reviews = data.reviews || [];
  const nextClass = classes.find(item => item.state !== 'finished');
  const next = data.next_action;
  const week = classes.find(item => Number.isFinite(item.week))?.week;
  const focus = learning.data ? learningFocus(learning.data) : null;
  const dates = upcoming.data?.items || [];
  const subjectOrder = Object.keys(COURSE_THEMES);
  const allCourses = [...(courses.data?.items || [])].sort((a, b) => subjectOrder.indexOf(courseTheme(a).key) - subjectOrder.indexOf(courseTheme(b).key));
  const todayIds = new Set(classes.map(item => item.course_id).filter(Boolean));
  const shownCourses = coursePage === 'today' ? allCourses.filter(course => todayIds.has(course.id)) : allCourses;
  const hour = Number(new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Shanghai', hour: '2-digit', hourCycle: 'h23' }).format(new Date()));
  const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  const dateLabel = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long', timeZone: 'Asia/Shanghai' }).format(new Date(data.today + 'T00:00:00+08:00'));
  const openNext = () => {
    if (next?.kind === 'class') navigate(next.item.course_id ? 'course' : 'capture', next.item.course_id);
    else if (next?.kind === 'inbox') navigate('inbox', null, { inbox: { status: 'needs_review', scope: 'submitted' } });
    else navigate(({ review: 'review', event: 'event', capture: 'capture' })[next?.kind] || 'capture', next?.id);
  };
  const nextDetails = nextClass ? classDetails(nextClass) : null;
  return <div className="today-page">
    <header className="today-heading"><div><p>{dateLabel}{week ? <><span>·</span>第 {week} 周</> : null}</p><h2>{greeting}, Eric<span className="greeting-dot">.</span></h2><div className="today-subtitle">把注意力，留给今天。</div></div><button type="button" className="today-date-tile" onClick={() => navigate('courses')} aria-label="打开我的课程"><CalendarDays size={17}/><strong>{Number(data.today.slice(-2))}</strong><span>{Number(data.today.slice(5, 7))} 月</span></button></header>
    <button type="button" className="today-next" onClick={openNext}><span className="next-icon">{nextClass ? <Clock3 size={21}/> : <BookOpen size={21}/>}</span><span className="next-copy"><small>{nextClass ? nextClass.state === 'in_progress' ? '正在上课' : '下一节课' : '接下来'}</small><strong>{nextClass?.title || next?.title || '随手记下今天的新想法'}</strong>{nextClass ? <span>{classTime(nextClass.start)}–{classTime(nextClass.end)}{nextDetails.location ? <> · {nextDetails.location}</> : null}</span> : null}</span><ArrowUpRight size={20}/></button>
    <div className="today-layout">
      <Group title="今日课程" count={`${classes.length} 节`} action="课程" onAction={() => navigate('courses')} className="today-schedule">{classes.length ? <Schedule classes={classes} navigate={navigate}/> : <Quiet icon={CalendarDays}>今天没有排课，按自己的节奏学习。</Quiet>}<div className="today-study-place"><Sunset size={15}/><span>晚修地点</span><strong>{studyLocation}</strong></div></Group>
      <Group title="本周重点" action="学习" onAction={() => navigate('learning')} className="today-focus"><ReadState resource={learning}/>{focus?.priorities.length ? <ul className="today-grouped-list">{focus.priorities.map(item => <li key={item.id}><button type="button" onClick={() => navigate('learning', item.id)}><span className="focus-marker"/><span>{item.title}</span><ChevronRight size={14}/></button></li>)}</ul> : <p className="today-note">还没有足够的学习记录判断薄弱点。</p>}{focus?.retests.length ? <button type="button" className="today-summary-link" onClick={() => navigate('learning')}><Clock3 size={16}/>{focus.retests.reduce((sum, item) => sum + item.count, 0)} 题等待独立复测<ChevronRight size={14}/></button> : null}<button type="button" className="today-summary-link" onClick={() => navigate('inbox', null, { inbox: { status: 'needs_review', scope: 'submitted' } })}><Inbox size={16}/>{data.needs_confirmation ? `${data.needs_confirmation} 份内容需要核对` : '收集新的学习材料'}<ChevronRight size={14}/></button></Group>
      <Group title="今日任务" count={tasks.length || undefined} action="全部" onAction={() => navigate('tasks')} className="today-tasks">{taskError ? <p className="personal-field-error" role="alert">{taskError}</p> : null}{tasks.length ? <ul className="cloud-task-list today-task-list">{tasks.map(task => <li key={task.id}><TaskCheck task={task} commands={localItems.commands} onChange={item => void onComplete(item)} onConfirm={item => void onComplete(item, item.progress.completed)} onOpen={() => navigate('event', task.event_id)}>{task.title}<small>{task.event_title}</small></TaskCheck></li>)}</ul> : <Quiet>当前没有未完成任务。</Quiet>}</Group>
      <Group title="学习进度" action="详情" onAction={() => navigate('learning')} className="today-progress"><ReadState resource={learning}/>{focus ? <><p className="progress-course">{focus.course}</p><p className="today-note">{focus.day ? `最近学习 · ${focus.day}` : focus.preview ? '最近收到的预习 · 日期待确认' : '等待第一份学习记录'}</p><dl className="today-progress-stats"><div><dt>预习答对</dt><dd>{focus.previewScore.correct}<span> / {focus.previewScore.total} 题</span></dd></div><div><dt>独立复测进步</dt><dd>{focus.progress.length}<span> 项已验证</span></dd></div></dl><p className="today-fine-print">以实际作答和复测记录为准</p></> : !learning.loading ? <p className="today-note">学习记录暂未读到，可以重新读取。</p> : null}</Group>
      <Group title="我的课程" count={courses.data?.total != null ? `${courses.data.total} 门` : null} action="全部" onAction={() => navigate('courses')} className="today-courses"><div className="today-course-toolbar"><span>从一门课，接着学起。</span><div className="eric-segmented" role="group" aria-label="课程范围">{[['all', '全部'], ['today', '今天']].map(([key, label]) => <button type="button" key={key} aria-pressed={coursePage === key} onClick={() => setCoursePage(key)}>{label}</button>)}</div></div><ReadState resource={courses}/>{shownCourses.length ? <div className="today-course-grid">{shownCourses.map(course => <CourseCard key={course.id} course={course} detail={courseDetails(course)} onOpen={() => navigate('course', course.id)}/>)}</div> : courses.data ? <Quiet icon={BookOpen}>{coursePage === 'today' ? '今天没有已关联的课程。' : '保存笔记或添加课程后，从这里继续。'}</Quiet> : null}</Group>
      <Group title="近期事项" action="全部" onAction={() => navigate('tasks')} className="today-upcoming"><ReadState resource={upcoming}/>{dates.length ? <ul className="today-grouped-list">{dates.slice(0, 4).map(item => <li key={item.deadline_id || item.id}><button type="button" onClick={() => navigate('event', item.event_id)}><CalendarDays size={17}/><span><strong>{item.event_title}</strong><small>{item.relative_label}</small></span><ChevronRight size={14}/></button></li>)}</ul> : upcoming.data ? <p className="today-note">未来 7 天暂无已确认的截止事项。</p> : null}</Group>
      <Group title="课堂复盘" count={data.review_count || undefined} action="全部 Review" onAction={() => navigate('reviews')} className="today-reviews">{reviews.length ? <ul className="today-grouped-list today-review-list">{reviews.slice(0, 3).map(item => <li key={item.id}><button type="button" onClick={() => navigate('review', item.id)}><span className="review-icon"><FileText size={18}/></span><span><strong>{item.title}</strong><small>{item.lecture_date} · 第 {item.version} 版 · 待阅读</small></span><ChevronRight size={15}/></button></li>)}</ul> : <p className="today-note">目前没有尚未阅读的课堂复盘。</p>}{data.review_incomplete_count ? <p className="today-fine-print">另有 {data.review_incomplete_count} 堂课的复盘尚未完成。</p> : null}</Group>
    </div><div className="today-receipts">{receipts}</div>
  </div>;
}
