export class CollectorError extends Error {
  constructor(message, code = 'connection_error') { super(message); this.code = code; }
}
export const connectionMessage = '电脑当前不可连接。请确认电脑与手机在同一 Wi-Fi、工作台正在运行。当前输入仍保留，恢复连接后可重试。';
function unpack(xhr) {
  let payload;
  try { payload = JSON.parse(xhr.responseText); } catch { throw new CollectorError(connectionMessage); }
  if (xhr.status < 200 || xhr.status >= 300) throw new CollectorError(payload.error?.message || '暂时无法接收，请稍后重试。', payload.error?.code || 'request_failed');
  return payload;
}
export function collectorRequest(path, { body, csrf, signal, onProgress } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(body === undefined ? 'GET' : 'POST', path);
    xhr.timeout = body instanceof FormData ? 90000 : 15000;
    if (csrf) xhr.setRequestHeader('X-Collector-CSRF', csrf);
    if (body !== undefined && !(body instanceof FormData)) xhr.setRequestHeader('Content-Type', 'application/json');
    const abort = () => xhr.abort();
    const finish = callback => { signal?.removeEventListener('abort', abort); callback(); };
    xhr.onload = () => finish(() => { try { resolve(unpack(xhr)); } catch (error) { reject(error); } });
    xhr.onerror = xhr.ontimeout = () => finish(() => reject(new CollectorError(connectionMessage)));
    xhr.onabort = () => finish(() => reject(new CollectorError(connectionMessage, 'aborted')));
    if (onProgress) xhr.upload.onprogress = event => { if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100)); };
    if (signal?.aborted) return reject(new CollectorError(connectionMessage, 'aborted'));
    signal?.addEventListener('abort', abort, { once: true });
    xhr.send(body === undefined ? null : body instanceof FormData ? body : JSON.stringify(body));
  });
}
