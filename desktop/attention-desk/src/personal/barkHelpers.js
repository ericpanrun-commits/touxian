export const BARK_STATUS = { unconfigured: '未配置', configured: '已配置 · 等待首次发送', healthy: '连接正常', failed: '连接失败', disabled: '已关闭' };
export const DELIVERY_STATUS = { not_sent: '未发送', pending: '等待发送', sending: '正在发送', sent: '已发送', failed: '发送失败', suppressed: '被过滤' };
const REASONS = { historical: '历史信息保护', low_importance: '重要程度低', low_relevance: '相关度不足', expired: '通知已结束',
  unknown_publication: '发布日期不明确', untrusted_source: '来源未获信任或已停用', category_filtered: '分类已过滤',
  source_priority_filtered: '来源优先级已过滤', policy_disabled: '此类推送已关闭', policy_filtered: '未达到推送条件', disabled: '手机推送已关闭',
  unauthorized: 'Device Key 无效，请检查后端配置', rejected: '服务拒绝请求', server_error: '服务暂时不可用',
  rate_limited: '服务限流', connect_timeout: '连接超时', unreachable: '服务器不可达',
  delivery_unknown: '无法确认送达，为避免重复已停止重发', client_error: '发送组件不可用', invalid_config: '后端配置无效' };
export const barkReason = value => REASONS[value] || '请查看后端通知设置';
export const validQuietTime = value => /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value);
