import { useEffect, useState } from 'react';
import { personalRequest } from './api';

// Shared read owner for planning surfaces: abort and discard stale responses.
export default function usePersonalResource(path) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState({ path, data: null, loading: true, error: null });
  useEffect(() => {
    const controller = new AbortController();
    setState(current => ({ path, data: current.path === path ? current.data : null, loading: true, error: null }));
    personalRequest(path, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setState({ path, data, loading: false, error: null }); })
      .catch(error => { if (!controller.signal.aborted) setState(current => ({ ...current, loading: false, error })); });
    return () => controller.abort();
  }, [path, attempt]);
  return { ...(state.path === path ? state : { data: null, loading: true, error: null }), retry: () => setAttempt(value => value + 1) };
}
