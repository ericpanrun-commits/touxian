export const OFFICIAL_VIEWS = [['latest', '最新'], ['relevant', '与你相关'], ['college', '学院'], ['academic', '教务'], ['competition', '竞赛'], ['international', '国际交流'], ['employment', '实习就业'], ['all', '全部'], ['unread', '未读'], ['saved', '收藏']];
export const IMPORTANCE = { critical: '紧急', high: '重要', normal: '普通', low: '资讯' };
export const SOURCE_TYPES = { official_school: '学校官网', official_college: '学院官网', official_department: '部门官网' };
export const PRIORITIES = { high: '高优先级', normal: '普通优先级', low: '低优先级' };
export const HEALTH = { disabled: '已停用', not_checked: '尚未同步', checking: '同步中', ok: '正常', warning: '需留意', degraded: '连续失败', partial: '部分未取得', error: '失败', unreachable: '暂时不可达' };
const formatter = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' });
export const officialTime = value => value && Number.isFinite(Date.parse(value)) ? /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : formatter.format(new Date(value)) : '尚无记录';
export function officialLink(value) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && (url.hostname === 'zqu.edu.cn' || url.hostname.endsWith('.zqu.edu.cn')) ? url.href : undefined; } catch { return undefined; }
}
export function officialRoute(search) {
  const params = new URLSearchParams(search); const view = params.get('official_feed'); const offset = Number(params.get('official_offset')); const item = params.get('official_item') || '';
  return { view: OFFICIAL_VIEWS.some(([key]) => key === view) ? view : 'latest', offset: Number.isSafeInteger(offset) && offset >= 0 && offset <= 100000 ? offset : 0, item: /^[0-9a-f-]{36}$/.test(item) ? item : '', sources: params.get('official_sources') === '1' };
}
