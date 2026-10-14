import { useEffect, useRef, useState } from 'react';
import { personalRequest } from './api';
import usePersonalResource from './usePersonalResource';

export default function useOfficialSync() {
  const resource = usePersonalResource('/official/overview');
  const [busy, setBusy] = useState(false); const [error, setError] = useState(null); const [notice, setNotice] = useState(''); const [completed, setCompleted] = useState(0);
  const lifecycle = useRef({ alive: false, pending: false, controller: null, running: false });
  useEffect(() => { const state = lifecycle.current; state.alive = true; return () => { state.alive = false; state.controller?.abort(); }; }, []);
  const running = Boolean(resource.data?.scan.running);
  useEffect(() => {
    const state = lifecycle.current;
    if (state.running && !running && resource.data) { const scan = resource.data.scan; setNotice(`同步完成：新增 ${scan.new_count} 条，更新 ${scan.changed_count} 条，失败 ${scan.failed} 个来源。`); setCompleted(value => value + 1); }
    state.running = running;
  }, [running, resource.data]);
  useEffect(() => { const timer = window.setTimeout(resource.retry, running ? 1500 : 30000); return () => window.clearTimeout(timer); }, [resource.data, resource.error, running]);
  const mutate = async (action, body) => {
    const state = lifecycle.current; if (state.pending) return;
    state.pending = true; state.controller = new AbortController(); setBusy(true); setError(null); setNotice('');
    try { await personalRequest(`/official/${action}`, { method: 'POST', body, signal: state.controller.signal }); if (state.alive) { setNotice(action === 'scan' ? '同步已开始，可以离开此页面。' : '来源设置已保存。'); resource.retry(); } }
    catch (value) { if (state.alive) { setError(value); resource.retry(); } }
    finally { state.pending = false; if (state.alive) setBusy(false); }
  };
  return { ...resource, busy: busy || running, running, notice, mutationError: error, completed, mutate };
}
