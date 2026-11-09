import { useEffect, useRef, useState } from 'react';
import { Activity, ExternalLink, RefreshCw } from 'lucide-react';
import { HEALTH_METRICS, HEALTH_ORIGIN, shanghaiDay } from '../../supabase/functions/eric-app-api/health_contract.js';
import { readDesktopOverview } from './transport';
import './health.css';

const states = { idle: '暂无可分析数据', pending: '等待本地分析', analyzing: '本地分析中', completed: '分析已完成', failed: '分析失败，请在原站查看', empty: '等待健康数据', unknown: '分析状态暂不可确认' };
const number = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });
const time = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' });
const formatTime = value => value ? time.format(new Date(value)) : '暂无记录';

function Trend({ metric }) {
  const points = [...metric.chart.points].sort((a, b) => a.index - b.index);
  const lines = points.slice(1).filter((point, index) => point.index === points[index].index + 1).map(point => [points.find(previous => previous.index === point.index - 1), point]);
  const label = HEALTH_METRICS[metric.id][0];
  return <><svg viewBox="0 0 320 120" className="health-chart" role="img" aria-label={`${label}最近七天趋势，详细数值见下方日期列表`}>
    <line x1="18" y1="99" x2="308" y2="99" className="health-chart-axis"/>
    {lines.map(([a, b]) => <line key={b.index} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="health-chart-line"/>)}
    {points.map(point => <circle key={point.index} cx={point.x} cy={point.y} r="3.5" className={metric.days[point.index].status === 'partial' ? 'health-point-partial' : 'health-point'}><title>{metric.days[point.index].date} · {number.format(metric.days[point.index].value)} {metric.unit}{metric.days[point.index].status === 'partial' ? '（部分数据）' : ''}</title></circle>)}
    {!points.length ? <text x="160" y="58" textAnchor="middle" className="health-chart-empty">这七天暂无数据</text> : null}
  </svg><div className="health-chart-dates"><span>{metric.days[0].date.slice(5)}</span><span>{metric.days[6].date.slice(5)}</span></div>
  <details className="health-values"><summary>查看每日数值</summary><dl>{metric.days.map(day => <div key={day.date}><dt>{day.date}</dt><dd>{day.value === null ? '缺失' : `${number.format(day.value)} ${metric.unit}${day.status === 'partial' ? ' · 部分数据' : ''}`}</dd></div>)}</dl></details></>;
}

export default function HealthOverview({ readOverview = readDesktopOverview, subscribePrivacy }) {
  const [data, setData] = useState(null), [loading, setLoading] = useState(false), [message, setMessage] = useState('');
  const alive = useRef(false), pending = useRef(null), generation = useRef(0), displayed = useRef(null);
  const clear = (reason = '') => {
    generation.current++; pending.current?.abort(); pending.current = null; displayed.current = null;
    setData(null); setLoading(false); setMessage(reason);
  };
  const load = async () => {
    if (pending.current) return;
    if (navigator.onLine === false) { clear('当前离线，联网后点击刷新。'); return; }
    const controller = new AbortController(), version = ++generation.current;
    pending.current = controller; setLoading(true); setData(null); displayed.current = null; setMessage('');
    const timeout = setTimeout(() => controller.abort(), 10_000);
    try {
      const value = await readOverview({ signal: controller.signal });
      if (!alive.current || version !== generation.current || controller.signal.aborted || navigator.onLine === false) return;
      if (value.window.end !== shanghaiDay()) throw new Error('健康数据窗口已变化，请刷新。');
      displayed.current = value; setData(value);
    } catch (error) {
      if (alive.current && version === generation.current) { displayed.current = null; setData(null); setMessage('暂时无法读取健康摘要，请刷新或打开 HealthData。'); }
    } finally {
      clearTimeout(timeout);
      if (alive.current && version === generation.current) { pending.current = null; setLoading(false); }
    }
  };
  useEffect(() => {
    alive.current = true;
    const offline = () => clear('当前离线，联网后点击刷新。');
    const expired = () => clear('登录状态已变化，请重新打开 Health。');
    const pagehide = () => clear('健康内容已清除，点击刷新查看。');
    const resume = () => {
      if (document.visibilityState === 'visible' && displayed.current?.window.end !== undefined && displayed.current.window.end !== shanghaiDay()) clear('已进入新的一天，点击刷新查看最近七天。');
    };
    window.addEventListener('offline', offline); window.addEventListener('pagehide', pagehide);
    window.addEventListener('pageshow', resume); window.addEventListener('focus', resume);
    window.addEventListener('eric-auth-required', expired); document.addEventListener('visibilitychange', resume);
    const unsubscribe = subscribePrivacy?.(expired);
    void load();
    return () => {
      alive.current = false; generation.current++; pending.current?.abort(); pending.current = null; displayed.current = null;
      unsubscribe?.();
      window.removeEventListener('offline', offline); window.removeEventListener('pagehide', pagehide);
      window.removeEventListener('pageshow', resume); window.removeEventListener('focus', resume);
      window.removeEventListener('eric-auth-required', expired); document.removeEventListener('visibilitychange', resume);
    };
  }, [readOverview, subscribePrivacy]);
  const stale = data?.latest_health_date && data.latest_health_date < data.window.end;
  return <section className="health-overview" aria-labelledby="health-heading">
    <header className="desk-page-heading health-heading"><div><p className="personal-meta">HEALTH OVERVIEW</p><h1 id="health-heading"><Activity size={26} aria-hidden="true"/>健康概览</h1><p>六项日常指标，留意最近七天的变化。</p></div><div className="health-actions"><button type="button" className="button button-quiet" onClick={() => void load()} disabled={loading} aria-busy={loading}><RefreshCw size={16} aria-hidden="true"/>刷新</button><a className="button button-quiet" href={HEALTH_ORIGIN + '/'} target="_blank" rel="noopener noreferrer">打开 HealthData<ExternalLink size={16} aria-hidden="true"/></a></div></header>
    <p className="personal-meta">完整报告与 30 天趋势在 HealthData 查看。健康内容仅在此页临时显示。</p>
    {loading ? <p role="status" className="personal-meta">正在读取健康摘要…</p> : null}
    {message ? <p role="status" className="personal-section">{message}</p> : null}
    {data ? <><section className="personal-section health-status" aria-label="健康数据状态"><div><span>数据覆盖至</span><strong>{data.latest_health_date || '暂无数据'}</strong>{stale ? <small>晚于此日期的数据尚未收到</small> : null}</div><div><span>自动分析</span><strong>{states[data.analysis.state]}</strong>{data.analysis.completed_at ? <small>{formatTime(data.analysis.completed_at)}</small> : null}</div><div><span>最近七天 · UTC+8</span><strong>{data.window.start} — {data.window.end}</strong></div></section>
      <div className="health-grid">{data.metrics.map(metric => <article key={metric.id} className="personal-section health-metric" aria-label={HEALTH_METRICS[metric.id][0]}><h2>{HEALTH_METRICS[metric.id][0]}</h2><p className="health-value">{metric.latest_value === null ? '—' : number.format(metric.latest_value)}<span>{metric.unit}</span></p><p className="personal-meta">{metric.latest_date ? `最新完整值 · ${metric.latest_date}` : '近 30 天暂无完整数据'}</p>{data.mode === 'trend' ? <Trend metric={metric}/> : null}</article>)}</div>
      <footer className="personal-section health-times"><p>来源生成：{formatTime(data.source_generated_at)}</p><p>上传接收：{formatTime(data.received_at)}</p><p>本次读取：{formatTime(data.fetched_at)}</p><p className="personal-meta">最新完整值不包含部分数据。曲线中的空心点表示部分数据；缺失日期留空。</p></footer></> : null}
  </section>;
}
