// Civil date arithmetic is deliberately UTC, never parsed as a local instant.
export const EVENT_STATUS = { needs_review: '待确认', needs_action: '待行动', in_progress: '进行中', done: '已完成', cancelled: '已取消' };
export const REMINDER_STATUS = { scheduled: '即将提醒', dispatching: '正在发送', fired: '已触发', snoozed: '稍后提醒', dismissed: '已关闭', cancelled: '已取消', failed: '系统通知未送达' };
export function civilDate(value) { return new Date(value + 'T12:00:00Z'); }
export function isoDay(value) { return value.toISOString().slice(0, 10); }
export function shiftDay(value, amount) { const next = civilDate(value); next.setUTCDate(next.getUTCDate() + amount); return isoDay(next); }
export function monthDays(year, month) {
  const first = new Date(Date.UTC(year, month - 1, 1, 12));
  const offset = (first.getUTCDay() + 6) % 7;
  const count = new Date(Date.UTC(year, month, 0, 12)).getUTCDate();
  return Array.from({ length: Math.ceil((offset + count) / 7) * 7 }, (_, i) => shiftDay(isoDay(first), i - offset));
}
export function shiftMonth(year, month, amount) { const next = new Date(Date.UTC(year, month - 1 + amount, 1)); return { year: next.getUTCFullYear(), month: next.getUTCMonth() + 1 }; }
export function itemsOnDay(items, day) { return items.filter(item => item.start_date <= day && item.end_date >= day); }
export function groupEvents(items) {
  const grouped = new Map();
  items.forEach(item => {
    if (!grouped.has(item.event_id)) grouped.set(item.event_id, { id: item.event_id, title: item.event_title, status: item.event_status, items: [] });
    grouped.get(item.event_id).items.push(item);
  });
  return [...grouped.values()];
}
export function deadlineDisplay(item) {
  if (item.precision === 'unknown') return '日期待确认';
  if (item.all_day || item.precision === 'date' || item.range_precision === 'date') return item.value + (item.end_value ? ` — ${item.end_value}` : '');
  if (item.start_at) {
    const format = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' });
    return format.format(new Date(item.start_at)) + (item.end_at && item.end_at !== item.start_at ? ` — ${format.format(new Date(item.end_at))}` : '');
  }
  return item.value.replace('T', ' ') + (item.end_value ? ` — ${item.end_value.replace('T', ' ')}` : '');
}
export function displayInstant(value) { return value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '尚未触发'; }
