export function personalApiUrl(path, base = '') {
  const workerRoute = /^\/(?:official(?:\/|$)|notifications\/bark(?:\/|$)|system(?:\/|$))/.test(path);
  if (!base || !workerRoute) return '/api/personal' + path;
  const target = new URL(base);
  if (target.protocol !== 'https:' || target.username || target.password || target.search || target.hash) throw new Error('后台 API 地址需使用不含凭据的 HTTPS URL。');
  return target.href.replace(/\/$/, '') + '/api/personal' + path;
}
