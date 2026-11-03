import { ArrowUpRight } from 'lucide-react';
import './learning.css';

const results = { correct: '正确', incorrect: '错误', unknown: '待核对' };

export function ExerciseScore({ counts, label = '预习题' }) {
  return counts?.total ? <p className="learning-score">{label} <strong>{counts.total}</strong><span>正确 {counts.correct}</span><span>错误 {counts.incorrect}</span>{counts.unknown ? <span>待核对 {counts.unknown}</span> : null}</p> : <p className="learning-empty">尚无习题作答记录。</p>;
}

export default function PreviewEvidence({ data, compact = false }) {
  if (!data) return null;
  const unconfirmed = Math.max(0, data.timing_unknown - (data.user_confirmed_before || 0));
  const orderUnknown = unconfirmed > 0;
  return <section className="learning-before-evidence"><h3>{orderUnknown ? '预习作答记录' : '课前预习表现'}</h3><ExerciseScore counts={data.counts} />{data.timing_unknown ? <p className="personal-meta">{orderUnknown ? `${unconfirmed} 题的提交时间不足以确认在上课前完成，先保留作答，不作为课前与课后比较的依据。` : '你已确认预习先于课堂完成；平台未提供精确提交时刻。'}</p> : null}{data.material_exposures ? <p className="personal-meta">另有 {data.material_exposures} 项预习材料，只记接触。</p> : null}{data.weaknesses.length ? <p>{orderUnknown ? '本次预习错题' : '课前薄弱'}：{data.weaknesses.map(w => w.title).join('、')}</p> : null}{!compact ? <details className="desk-disclosure"><summary>查看预习题目与结果</summary>{data.attempts.map(a => <div className="learning-captured-question" key={a.attempt_id}><strong>{a.question}</strong><p>我的答案：{a.my_answer} · {results[a.result]}</p><a className="personal-text-button" href={`?personal_view=eric-inbox&inbox_item=${encodeURIComponent(a.raw_item_id)}`} target="_blank" rel="noreferrer">查看原始学习记录<ArrowUpRight size={14} /></a></div>)}<p className="personal-meta">{data.notice}</p></details> : null}</section>;
}
