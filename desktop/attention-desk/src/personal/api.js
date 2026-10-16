import { personalApiUrl } from './apiBase';

export class PersonalApiError extends Error {
  constructor(message, code = 'request_failed') {
    super(message);
    this.name = 'PersonalApiError';
    this.code = code;
  }
}

export async function personalRequest(path, options = {}) {
  const { signal, body, headers, responseType = 'json', ...rest } = options;
  const controller = new AbortController();
  const forwardAbort = () => controller.abort();
  signal?.addEventListener('abort', forwardAbort, { once: true });
  if (signal?.aborted) controller.abort();
  const multipart = body instanceof FormData;
  const timeout = window.setTimeout(() => controller.abort(), multipart ? 60000 : 15000);
  try {
    const response = await fetch(personalApiUrl(path, import.meta.env?.VITE_API_BASE_URL || ''), {
      ...rest,
      cache: 'no-store',
      signal: controller.signal,
      headers: { ...(body !== undefined && !multipart ? { 'Content-Type': 'application/json' } : {}), ...headers },
      ...(body !== undefined ? { body: multipart ? body : JSON.stringify(body) } : {}),
    });
    const payload = response.ok && responseType === 'blob' ? await response.blob() : await response.json();
    if (!response.ok) {
      const detail = payload.detail;
      throw new PersonalApiError(
        typeof detail === 'string' ? detail : detail?.message || '输入未通过校验，请核对填写内容与原文引用。',
        detail?.code || 'invalid_input',
      );
    }
    return payload;
  } catch (error) {
    if (error instanceof PersonalApiError || signal?.aborted) throw error;
    throw new PersonalApiError('无法确认是否保存成功。请保留当前输入，恢复连接后重试或重新读取已保存内容。', 'connection_error');
  } finally {
    window.clearTimeout(timeout);
    signal?.removeEventListener('abort', forwardAbort);
  }
}
