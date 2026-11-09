import { AppFault } from './store.ts';
import { HEALTH_ORIGIN, HEALTH_PATH, HEALTH_LIMIT, validateHealthOverview } from './health_contract.js';

export function createHealthOverviewReader(options: {token:string;owner:string;workspace:string;mode:string;fetcher?:typeof fetch}) {
  return async (workspace: string, owner: string) => {
    if (!options.owner || !options.workspace || owner !== options.owner || workspace !== options.workspace)
      throw new AppFault(403, 'health_not_authorized', '此账号尚未连接 HealthData。');
    if (!['trend', 'summary'].includes(options.mode) || !/^[A-Za-z0-9_-]{32,256}$/.test(options.token))
      throw new AppFault(503, 'health_unavailable', 'Health 暂未连接，可以打开 HealthData 查看。');
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 8_000);
    try {
      const response = await (options.fetcher || fetch)(HEALTH_ORIGIN + HEALTH_PATH + '?view=' + options.mode, {
        method: 'GET', redirect: 'error', cache: 'no-store', credentials: 'omit',
        headers: { Authorization: 'Bearer ' + options.token }, signal: controller.signal,
      });
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json') || !response.body || Number(response.headers.get('content-length') || 0) > HEALTH_LIMIT) {
        await response.body?.cancel(); throw new Error('health_upstream_unavailable');
      }
      const reader = response.body.getReader(), chunks: Uint8Array[] = [];
      let length = 0;
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          length += value.length;
          if (length > HEALTH_LIMIT) throw new Error('health_response_too_large');
          chunks.push(value);
        }
      } finally { await reader.cancel(); }
      const bytes = new Uint8Array(length); let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
      const result = validateHealthOverview(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)));
      if (result.mode !== options.mode) throw new Error('health_mode_mismatch');
      return result;
    } catch {
      throw new AppFault(503, 'health_unavailable', '暂时无法读取 HealthData，请稍后刷新或打开原站。');
    } finally { clearTimeout(timer); }
  };
}
