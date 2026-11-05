import { useEffect, useId, useRef } from 'react';
import { X } from 'lucide-react';

// Shared modal owner for detail, review and focused formal-item editors.
export default function DeskSheet({ title, onClose, busy = false, children, wide = false }) {
  const ref = useRef(null);
  const id = useId();
  useEffect(() => {
    const opener = document.activeElement;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    ref.current.showModal();
    ref.current.querySelector('button')?.focus();
    return () => {
      ref.current?.close();
      document.body.style.overflow = previous;
      if (opener?.isConnected) opener.focus();
    };
  }, []);
  return <dialog ref={ref} className={`desk-sheet${wide ? ' desk-sheet-wide' : ''}`} aria-labelledby={id}
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <header className="desk-sheet-header"><h2 id={id}>{title}</h2><button type="button" className="desk-icon-button" aria-label="关闭详情" disabled={busy} onClick={onClose}><X size={20} /></button></header>
    <div className="desk-sheet-body">{children}</div>
  </dialog>;
}
