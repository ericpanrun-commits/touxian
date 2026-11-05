import { CheckCircle2, CirclePause, Database, LoaderCircle, Moon, TriangleAlert } from 'lucide-react';
import { displayWechatTime } from './wechatHelpers';

export default function WeChatCollectorStatus({ resource, compact = false }) {
  const data = resource.data;
  if (!data) return <div className="wechat-collector-status wechat-status-loading" role={resource.error ? 'alert' : 'status'}>{resource.error ? <>采集状态暂时无法读取。<button type="button" className="personal-text-button" onClick={resource.retry}>重试读取状态</button></> : <><LoaderCircle size={15} className="spin" />正在读取微信采集状态…</>}</div>;
  const stopped = data.collector_alive === false && data.status !== 'disabled' && !data.quiet;
  const partial = data.status === 'partial' && data.chats?.some(chat => chat.last_error);
  const problem = stopped || partial || data.database_state === 'unavailable' || ['adapter_error', 'database_worker_failed', 'error'].includes(data.status);
  const paused = data.status === 'disabled';
  const waiting = !data.runtime_verified && !paused;
  const Icon = problem || waiting ? TriangleAlert : data.quiet ? Moon : paused ? CirclePause : CheckCircle2;
  const label = stopped ? '后台采集暂未运行' : partial ? '部分群暂未更新' : problem ? '采集需要检查' : data.quiet ? '夜间暂停' : paused ? '采集已停用' : waiting ? '等待本机数据库验证' : data.status === 'running' ? '正在收集新消息' : '采集运行正常';
  const database = data.database_state === 'readable' || data.wechat_available === true ? '最近读取正常' : data.database_state === 'unavailable' ? '暂时无法读取' : data.runtime_verified ? '数据库已验证' : '数据库等待验证';
  const metrics = <dl className="wechat-collector-metrics">
      <div><dt>授权群</dt><dd>{data.authorized_chat_count ?? data.chats?.length ?? 0}<small> 个 · {data.active_chat_count ?? data.chats?.filter(chat => chat.enabled).length ?? 0} 个启用</small></dd></div>
      <div><dt>今日新增消息</dt><dd>{data.stats?.messages_new ?? 0}<small> 条</small></dd></div>
      <div><dt>今日候选通知</dt><dd>{data.stats?.candidates_today ?? data.stats?.important_items_found ?? 0}<small> 条</small></dd></div>
      <div><dt>今日 DeepSeek 调用</dt><dd>{data.stats?.llm_calls ?? 0}<small> 次</small></dd></div>
      <div><dt>上次扫描</dt><dd className="wechat-metric-time">{displayWechatTime(data.last_scan?.[0]?.completed_at)}</dd></div>
      <div><dt>下次扫描</dt><dd className="wechat-metric-time">{displayWechatTime(data.next_scan)}</dd></div>
    </dl>;
  return <div className="wechat-collector-status" aria-label="微信采集状态">
    <div className={`wechat-health ${problem || waiting ? 'is-warning' : paused || data.quiet ? 'is-paused' : 'is-healthy'}`}><span><Icon size={17} /><strong>{label}</strong></span><span><Database size={15} />{database}</span></div>
    {compact ? <details className="wechat-advanced"><summary>查看采集用量与计划</summary>{metrics}</details> : metrics}
    {problem ? <p className="personal-field-error">微信读取暂未完成，已保存的原文和候选仍可处理。下次计划会重新尝试。</p> : null}
    <p className="personal-meta wechat-schedule-note">北京时间 06:00–24:00，每 15 分钟收集一次；夜间暂停，06:00 补扫。没有新消息不调用 AI。</p>
    {resource.error ? <p role="alert" className="personal-field-error">刷新失败，正在显示上次保存的状态。<button type="button" className="personal-text-button" onClick={resource.retry}>重试</button></p> : null}
  </div>;
}
