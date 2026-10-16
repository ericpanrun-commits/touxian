import usePersonalResource from './usePersonalResource';
import { ReadStatus } from './PlanningViews';
import { officialTime } from './officialHelpers';

export default function WorkerStatus() {
  const resource = usePersonalResource('/system/status');
  const data = resource.data;
  return <section className="personal-section"><div className="personal-section-heading"><h2>后台运行状态</h2><button type="button" className="personal-text-button" disabled={resource.loading} onClick={resource.retry}>刷新运行状态</button></div>
    <ReadStatus resource={resource} label="后台运行状态" />
    {data ? <><p><strong>{data.runtime === 'cloud' ? '云端后台' : '本地后台'} · {({ online: '运行中', degraded: '状态异常', bootstrapping: '准备中' })[data.status] || '状态待确认'}</strong></p>
      <p className="personal-meta">环境：{data.environment === 'production' ? '生产' : '开发'} · 实例：{data.instance}<br />
        自动官网同步：{data.automatic_sync ? '已开启' : '已关闭'} · 自动手机推送：{data.automatic_bark ? '已开启' : '已关闭'}<br />
        调度：{({ healthy: '正常', stale: '长时间未运行', disabled: '未启用' })[data.scheduler]}<br />
        最近调度：{officialTime(data.last_scheduler_tick)}<br />最近官网成功：{officialTime(data.last_successful_source_sync)}<br />
        最近 Bark 成功：{officialTime(data.last_bark_success)}<br />
        数据库：{data.status === 'bootstrapping' ? '等待迁移验证' : data.database_persistent ? '已验证持久挂载' : '本地保存'} · 备份：{({ available: '可用', failed: '失败', pending: '尚未执行' })[data.backup_status]}<br />
        最近备份：{officialTime(data.last_backup_at)}</p>
      {data.bootstrap || data.dry_run ? <p className="personal-meta">正在安全验证模式中，自动通知不会发送到手机。</p> : null}
      {!data.cloud_deployed ? <p className="personal-meta">本地默认不自动推送，测试按钮可单独验证连接。云端正式上线前，电脑关闭后不会继续抓取。</p> : null}
      {data.scheduler === 'stale' || data.backup_status === 'failed' ? <p role="alert" className="personal-field-error">请检查后台运行日志和备份。已有信息与通知记录保留。</p> : null}
    </> : null}
  </section>;
}
