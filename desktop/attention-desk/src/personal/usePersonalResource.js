import { useEffect, useState } from 'react';
import { personalRequest } from './api';

// Shared read owner for planning surfaces: abort and discard stale responses.
export default function usePersonalResource(path, { refreshInterval = 0, paused = false } = {}) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState({ path, data: null, loading: true, error: null });
  useEffect(() => {
    const controller = new AbortController();
    let timer;
    let pending = false;
    setState(current => ({ path, data: current.path === path ? current.data : null, loading: true, error: null }));
    const read = () => {
    if (pending || controller.signal.aborted || paused) return;
    pending = true;
    personalRequest(path, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setState({ path, data, loading: false, error: null }); })
      .catch(error => { if (!controller.signal.aborted) setState(current => ({ ...current, loading: false, error })); })
      .finally(() => {
        pending = false;
        const tick = () => {
          if (document.visibilityState === 'visible') read();
          else timer = window.setTimeout(tick, refreshInterval);
        };
        if (refreshInterval && !controller.signal.aborted) timer = window.setTimeout(tick, refreshInterval);
      });
    };
    const visible = () => { if (document.visibilityState === 'visible') { window.clearTimeout(timer); read(); } };
    const clock = () => { if (/^\/(dashboard|planning\/today)/.test(path)) visible(); };
    read();
    if (refreshInterval) document.addEventListener('visibilitychange', visible);
    window.addEventListener('eric-data-changed', visible);
    window.addEventListener('eric-clock-changed', clock);
    window.addEventListener('online', visible);
    return () => { controller.abort(); window.clearTimeout(timer); document.removeEventListener('visibilitychange', visible);window.removeEventListener('eric-data-changed',visible);window.removeEventListener('eric-clock-changed',clock);window.removeEventListener('online',visible); };
  }, [path, attempt, refreshInterval, paused]);
  return { ...(state.path === path ? state : { data: null, loading: true, error: null }), retry: () => setAttempt(value => value + 1) };
}
