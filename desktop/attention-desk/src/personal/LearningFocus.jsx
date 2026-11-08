import { ArrowUpRight, BookOpen } from 'lucide-react';
import { learningFocus, shortDay, shortTopic } from './learningFocusModel';
import './learning.css';

function Score({ title, counts, empty }) {
  return <p className="learning-focus-score"><span>{title}</span>{counts.total ? <><strong>{counts.total} 题</strong><span aria-label={`正确 ${counts.correct}`}>✓ {counts.correct}</span><span aria-label={`错误 ${counts.incorrect}`}>✕ {counts.incorrect}</span>{counts.unknown ? <span aria-label={`待确认 ${counts.unknown}`}>? {counts.unknown}</span> : null}</> : <span className="personal-meta">{empty}</span>}</p>;
}

export default function LearningFocus({ data, onOpen, onRetest, onClassroom, onAll, onPending, children }) {
  const f = learningFocus(data);
  return <section className="personal-section learning-focus" aria-label="学习与下一步">
    <div className="personal-section-heading"><h2>{f.course}</h2>{onAll ? <button type="button" className="personal-text-button" onClick={onAll}>Learning<ArrowUpRight size={15} /></button> : <button type="button" className="personal-text-button" onClick={onClassroom}><BookOpen size={15} />课程与课堂</button>}</div>
    <div className="learning-focus-study"><h3>{f.isToday ? '今天学了什么' : f.day ? `最近一次学习 · ${shortDay(f.day)}` : f.preview ? '最近收到的预习' : '今天学了什么'}</h3>{f.preview?.title ? <p className="personal-meta">{f.preview.title}</p> : null}
      <Score title="预习" counts={f.previewScore} empty="暂无已提交题目" /><Score title="课堂" counts={f.classroomScore} empty={f.notesReceived ? '笔记已收到' : '暂无习题记录'} />
      {f.classroomScore.unknown ? <p className="personal-meta">? 为结果待确认，保留本人作答。</p> : null}
    </div>
    <div className="learning-focus-grid"><section><h3>现在最值得补</h3>{f.priorities.length ? <ol className="learning-focus-list">{f.priorities.map(item => <li key={item.id}><button type="button" className="personal-text-button" onClick={() => onOpen(item.id)}>{item.title}<ArrowUpRight size={14} /></button></li>)}</ol> : <p className="personal-meta">还没有足够证据判断薄弱点。</p>}</section>
      <section><h3>需要复测</h3>{f.retests.length ? <ul className="learning-focus-list">{f.retests.map(item => <li key={item.day}><button type="button" className="personal-text-button" onClick={() => onRetest(item.first)}>{item.due ? '已到时间' : shortDay(item.date)} · {item.count} 题<ArrowUpRight size={14} /></button><small>{item.titles.join('、')}</small></li>)}</ul> : <p className="personal-meta">暂无待完成复测。</p>}</section></div>
    <div className="learning-focus-progress"><h3>最近进步</h3>{f.progress.length ? <ul className="learning-focus-list">{f.progress.map(item => <li key={item.id}><button type="button" className="personal-text-button" onClick={() => onOpen(item.id)}>{shortTopic(item.title)}：{item.before_after_progress ? '课前错误 → 课后独立复测正确' : '原来出错 → 延迟独立复测正确'}</button></li>)}</ul> : <p className="personal-meta">尚无延迟独立复测的改善证据。</p>}{f.pending ? <p><button type="button" className="personal-text-button" onClick={onPending || onAll || onClassroom}>{f.pending} 份材料需要核对<ArrowUpRight size={14} /></button></p> : null}</div>
    {children}
  </section>;
}
