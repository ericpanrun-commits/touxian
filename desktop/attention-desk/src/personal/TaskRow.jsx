import { useId, useRef, useState } from 'react';
import { Check, ChevronRight } from 'lucide-react';

// Swipe reveals the same reversible completion/detail actions available by
// keyboard and mouse. Never translates a visual dismissal into a server delete.
export default function TaskRow({ task, checked, disabled, onChange, onOpen, children }) {
  const [revealed, setRevealed] = useState(false), start = useRef(null), swiped = useRef(false), id = useId();
  const down = event => { swiped.current = false; if (event.pointerType !== 'mouse') start.current = { x: event.clientX, y: event.clientY }; };
  const up = event => {
    const point = start.current; start.current = null;
    if (point && Math.abs(event.clientY - point.y) < 30 && Math.abs(event.clientX - point.x) > 55) { swiped.current = true; setRevealed(event.clientX < point.x); }
  };
  return <div className={`task-row${revealed ? ' is-revealed' : ''}`} onPointerDown={down} onPointerUp={up} onPointerCancel={() => { start.current = null; }} onClickCapture={event => { if (swiped.current) { event.preventDefault(); event.stopPropagation(); swiped.current = false; } }}>
    <div className="task-row-main"><label className="task-check-target" htmlFor={id}><input id={id} type="checkbox" checked={checked} disabled={disabled} onChange={onChange}/><span className="task-circle" aria-hidden="true">{checked ? <Check size={14} strokeWidth={3}/> : null}</span><span className="sr-only">{checked ? '恢复未完成：' : '完成：'}{task.title}</span></label>
      <div className="task-row-copy">{onOpen ? <button type="button" className="task-title" onClick={onOpen}>{children || task.title}</button> : <span>{children || task.title}</span>}</div>
      {onOpen ? <button type="button" className="desk-icon-button task-detail" aria-label={`查看任务：${task.title}`} onClick={onOpen}><ChevronRight size={16}/></button> : null}</div>
    {revealed ? <div className="task-swipe-actions"><button type="button" disabled={disabled} onClick={() => { onChange(); setRevealed(false); }}>{checked ? '恢复未完成' : '完成'}</button><button type="button" onClick={() => setRevealed(false)}>收起</button></div> : null}
  </div>;
}
