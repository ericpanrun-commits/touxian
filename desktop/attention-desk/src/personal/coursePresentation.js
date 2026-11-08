import reference from './courseReference.json';

export const studyLocation = '一教420'; // Explicit owner correction, 2026-11-03.
export function classDetails(item) {
  const saved = reference.sessions[item.uid] || {};
  return {
    location: item.location || item.classroom || item.room || item.place || (/晚修|晚自习/.test(item.title || '') ? studyLocation : saved.location) || '',
    teacher: item.teacher || saved.teacher || '',
  };
}
export function courseDetails(course) {
  const name = course.name || course.title || '';
  const saved = reference.courses[name] || (/高等数学/.test(name) ? reference.courses['高等数学I1'] : null);
  return course.teacher || saved?.teacher || '';
}
export const classTime = value => value ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(value)) : '';
