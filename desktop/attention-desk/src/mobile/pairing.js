// Phone navigation stays on the Collector origin, separate from desktop APIs.
export function verifyCollectorOrigin(config, currentOrigin) {
  const url = new URL(config.public_base_url);
  const parts = url.hostname.split('.').map(Number);
  const ipv4 = parts.length === 4 && parts.every(n => Number.isInteger(n) && n >= 0 && n <= 255);
  const lan = ipv4 && (parts[0] === 10 || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) || (parts[0] === 192 && parts[1] === 168));
  const fixture = config.fixture === true && ipv4 && parts[0] === 127;
  if (url.origin !== currentOrigin || url.href !== `${url.origin}/` || url.protocol !== 'http:' || !url.port || (!lan && !fixture)) {
    throw new Error('请使用电脑当前显示的局域网二维码打开收集页。');
  }
  return url.origin;
}
export const collectorHome = '/collect';
