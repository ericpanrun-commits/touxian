import { HEALTH_LIMIT, validateHealthOverview } from '../../supabase/functions/eric-app-api/health_contract.js';

export async function readDesktopOverview({ signal }) {
  const response = await fetch('/api/personal/health/overview', { method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal });
  if (!response.ok) throw new Error(response.status === 403 ? '请在 Windows 本地工作台查看 Health。' : '暂时无法读取 HealthData，请稍后刷新或打开原站。');
  const text = await response.text();
  if (new TextEncoder().encode(text).length > HEALTH_LIMIT) throw new Error('健康摘要暂时无法读取，请稍后刷新。');
  return validateHealthOverview(JSON.parse(text));
}
