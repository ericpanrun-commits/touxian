export const WECHAT_STATUS = {
  disabled: '采集未开启', quiet: '夜间静默 · 06:00 后补扫', quiet_boundary: '临近静默 · 等待明早',
  running: '扫描中', waiting_idle: '等待电脑空闲', uia_not_verified: '等待本机微信验证',
  not_tested: '等待本机微信验证', partial: '部分覆盖 · 有待补扫', no_change: '扫描完成 · 没有新增',
  changed: '发现新消息', deferred: '扫描已延期', adapter_error: '读取未完成',
};

const CATEGORIES = { task: '任务', deadline: '截止事项', event: '活动', notice: '通知', course_change: '课程变更', exam: '考试', registration: '报名', competition: '竞赛', meeting: '会议', administrative: '事务通知', other: '其他' };
export const wechatCategory = value => CATEGORIES[value] || value || '待整理';

export function displayWechatTime(value) {
  if (!value) return '尚无记录';
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short' }).format(parsed);
}

export function roleLabel(value) {
  return value === true ? '与你相关' : value === false ? '角色或范围不符' : '相关性待核对';
}

export function splitContextList(value) {
  return [...new Set(value.split(/[\n,，、]/).map(item => item.trim()).filter(Boolean))];
}
