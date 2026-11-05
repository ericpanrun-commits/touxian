const emptyScore = () => ({ total: 0, correct: 0, incorrect: 0, unknown: 0 });
export const shortTopic = (title = '') => title.replace(/^第.{1,4}章\s*第.{1,4}节\s*/, '').replace('（前置参考）', '');
export const shanghaiDay = value => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value));
export const shortDay = value => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric' }).format(new Date(value));

// Read-only presentation of the existing evidence; never infer mastery or a
// submission time from a receipt date.
export function learningFocus(data) {
  const today = data.today;
  const sessions = data.preview_sessions || (data.preview ? [data.preview] : []);
  const dayOf = s => s?.lecture?.lecture_date || s?.target_lecture_date || s?.activity_date;
  const current = sessions.find(s => dayOf(s) === today);
  const todayLesson = data.recent_lectures?.find(l => l.lecture_date === today);
  const preview = current || (!todayLesson ? data.preview : null);
  const lesson = todayLesson || data.recent_lectures?.find(l => l.id === preview?.lecture?.id);
  const day = todayLesson?.lecture_date || dayOf(preview);
  const courseId = todayLesson?.course_id || preview?.course_id;
  const course = data.courses.find(c => c.id === courseId)?.name || '我的学习';
  const weakIds = new Set();
  const priorities = [...(preview?.weaknesses || []), ...(data.weak || [])].filter(item => {
    const id = item.topic_id || item.id;
    if (weakIds.has(id)) return false;
    weakIds.add(id); return true;
  }).slice(0, 3).map(item => ({ id: item.topic_id || item.id, title: shortTopic(item.title) }));
  const retests = [];
  const plans = [...(data.due || []), ...(data.upcoming || [])];
  const seen = new Set();
  for (const plan of plans) {
    if (seen.has(plan.id)) continue;
    seen.add(plan.id);
    const day = shanghaiDay(plan.due_at);
    let group = retests.find(g => g.day === day);
    if (!group) { group = { day, date: plan.due_at, count: 0, titles: [], first: plan, due: day <= today }; retests.push(group); }
    group.count += 1;
    if (!group.titles.includes(shortTopic(plan.title))) group.titles.push(shortTopic(plan.title));
  }
  const progress = (data.progress || []).filter(t => t.before_after_progress || t.learning_state?.retest_successes > 0).slice(0, 2);
  return { course, courseId, day, isToday: day === today, preview, lesson,
    previewScore: preview?.exercises || emptyScore(),
    classroomScore: preview?.classroom_exercises || (day === today ? data.classroom : null) || emptyScore(),
    notesReceived: Boolean(lesson?.notes_available || preview?.lecture?.notes_available),
    priorities, retests: retests.slice(0, 3), progress,
    pending: data.pending_captures?.filter(c => c.error || c.status === 'needs_context').length || 0 };
}
