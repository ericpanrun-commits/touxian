import { useEffect, useRef, useState } from 'react';
import { Download, LoaderCircle } from 'lucide-react';
import { personalRequest } from './api';

export default function IcsExport({ eventId, deadlineId, future = true, includeTerminal = false }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const request = useRef(null);
  const objectUrl = useRef(null);
  useEffect(() => () => { request.current?.abort(); if (objectUrl.current) URL.revokeObjectURL(objectUrl.current); }, []);
  const download = async () => {
    if (request.current) return;
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError(''); setNotice('');
    try {
      const params = new URLSearchParams({ future: String(future), include_terminal: String(includeTerminal) });
      if (eventId) params.set('event_id', eventId);
      if (deadlineId) params.set('deadline_id', deadlineId);
      const blob = await personalRequest(`/planning/export.ics?${params}`, { signal: controller.signal, responseType: 'blob' });
      if (controller.signal.aborted) return;
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
      objectUrl.current = URL.createObjectURL(blob);
      const link = document.createElement('a'); link.href = objectUrl.current; link.download = 'attention-deadlines.ics'; link.click();
      setNotice('已生成日历文件，可手动导入。');
    } catch (failure) { if (!controller.signal.aborted) setError(failure.message); }
    finally { if (!controller.signal.aborted) { request.current = null; setBusy(false); } }
  };
  return <div className="planning-export"><button type="button" className="button button-quiet" disabled={busy} aria-busy={busy} onClick={download}>
    {busy ? <LoaderCircle size={15} className="spin" /> : <Download size={15} />}导出 ICS
  </button><span className="personal-meta" role="status">{notice}</span>{error ? <p role="alert" className="personal-field-error">{error} 可再次点击导出重试。</p> : null}</div>;
}
