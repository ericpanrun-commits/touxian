import { fileResponse, parts, readableResource } from './files.ts';
import { AppFault, CloudStore, SHA, UUID, boundedJson, hash } from './store.ts';

export const VISION_SCHEMA = 'eric-os-vision-result.v1';
export const WAITING_COPY = '等待 AI 分析…';
export const FAILED_COPY = 'AI 分析失败，可重新处理';
export const LOW_COPY = '部分内容需要确认';
const IMAGE_MIME = /^image\/(jpeg|png|webp|heic|heif)$/;
const ANALYZED_AT = /^\d{4}-\d\d-\d\dT.+(?:Z|[+-]\d\d:\d\d)$/;
const CONFIDENCE = /^(?:high|medium|low|0(?:\.\d+)?|1(?:\.0+)?)$/;

export async function botAuthorized(authorization: string | null, secret: string, serviceSecret = ''): Promise<boolean> {
  const configured = (secret || '').trim();
  if (configured.length < 16 || configured.length > 256) return false;
  const token = /^Bearer ([!-~]{16,256})$/.exec((authorization || '').trim())?.[1];
  if (!token || token === serviceSecret) return false;
  const [left, right] = await Promise.all([hash(token), hash(configured)]);
  if (left.length !== right.length) return false;
  let diff = token.length === configured.length ? 0 : 1;
  for (let i = 0; i < left.length; i++) diff |= left.charCodeAt(i) ^ right.charCodeAt(i);
  return diff === 0;
}

function exactlyOne(url: URL): {capture_id: string | null; resource_id: string | null} {
  if (url.searchParams.has('object_path')) throw new AppFault(400, 'invalid_request', '请使用收集编号或文件编号，不要提交存储路径。');
  const capture = url.searchParams.get('capture_id');
  const resource = url.searchParams.get('resource_id');
  const present = [capture, resource].filter(value => value !== null && value !== '');
  if (present.length !== 1 || url.searchParams.getAll('capture_id').length > 1 || url.searchParams.getAll('resource_id').length > 1) {
    throw new AppFault(400, 'invalid_request', '请只提供 capture_id 或 resource_id 其中一个。');
  }
  return {capture_id: capture || null, resource_id: resource || null};
}

function captureIdOf(resourceId: string): string {
  const match = /^capture:([0-9a-f-]{36}):([a-f0-9]{64})$/.exec(resourceId);
  if (!match || !UUID.test(match[1]) || !SHA.test(match[2])) throw new AppFault(400, 'invalid_request', '文件编号无效。');
  return match[1];
}

async function loadCapture(store: CloudStore, captureId: string, workspaceId: string | null): Promise<any> {
  if (!UUID.test(captureId)) throw new AppFault(400, 'invalid_request', '这次收集的编号无效。');
  const scope = workspaceId ? '&workspace_id=eq.' + workspaceId : '';
  const rows = await store.db('eric_app_captures?id=eq.' + captureId + scope + '&select=id,workspace_id,raw_item_id,files,vision_status,vision_provider,vision_analysis,vision_confidence,vision_processed_at,vision_error&limit=1');
  if (!rows[0]) throw new AppFault(404, 'capture_not_found', '没有找到这次收集。');
  return rows[0];
}

function selectImage(capture: any, resourceId: string | null): any {
  const files = Array.isArray(capture.files) ? capture.files : [];
  const file = resourceId
    ? files.find((item: any) => item?.resource_id === resourceId)
    : files.find((item: any) => IMAGE_MIME.test(item?.mime || ''));
  if (!file) throw new AppFault(404, 'file_not_ready', '没有找到这张图片。');
  if (!IMAGE_MIME.test(file.mime || '') || file.resource_id !== 'capture:' + capture.id + ':' + file.sha256 || !SHA.test(file.sha256 || '')) {
    throw new AppFault(400, 'vision_image_only', '这一轮只读取这张图片原件。');
  }
  if (resourceId && resourceId !== file.resource_id) throw new AppFault(404, 'file_not_ready', '没有找到这张图片。');
  return file;
}

function stringField(result: any, key: string, max: number): void {
  if (result[key] === undefined) return;
  if (typeof result[key] !== 'string' || result[key].length > max) throw new AppFault(400, 'invalid_input', '请核对视觉结果。');
}

export function visionPayload(value: any): any {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.prototype.hasOwnProperty.call(value, 'object_path')) {
    throw new AppFault(400, 'invalid_input', '请核对视觉结果。');
  }
  if (value.schema !== VISION_SCHEMA || value.source !== 'original_image') throw new AppFault(400, 'invalid_input', '请核对视觉结果。');
  if (typeof value.capture_id !== 'string' || !UUID.test(value.capture_id)) throw new AppFault(400, 'invalid_input', '这次收集的编号无效。');
  if (typeof value.resource_id !== 'string' || typeof value.sha256 !== 'string' || !SHA.test(value.sha256)) throw new AppFault(400, 'invalid_input', '文件编号无效。');
  if (value.resource_id !== 'capture:' + value.capture_id + ':' + value.sha256) throw new AppFault(400, 'invalid_input', '文件与这次收集不一致。');
  if (!['pending', 'completed', 'failed'].includes(value.status)) throw new AppFault(400, 'invalid_input', '请核对视觉结果。');
  if (typeof value.analyzed_at !== 'string' || !ANALYZED_AT.test(value.analyzed_at) || !Number.isFinite(Date.parse(value.analyzed_at))) {
    throw new AppFault(400, 'invalid_input', '分析时间无效。');
  }
  const analyzer = typeof value.analyzer === 'string' && value.analyzer.trim() ? value.analyzer.trim() : '';
  const provider = typeof value.provider === 'string' && value.provider.trim() ? value.provider.trim() : analyzer;
  if (!analyzer || analyzer.length > 80 || provider.length > 80) throw new AppFault(400, 'invalid_input', '请核对视觉结果。');
  const result = value.result ?? {};
  if (!result || typeof result !== 'object' || Array.isArray(result)) throw new AppFault(400, 'invalid_input', '请核对视觉结果。');
  stringField(result, 'title', 240);
  stringField(result, 'probable_course', 200);
  stringField(result, 'topic', 240);
  stringField(result, 'summary', 4000);
  stringField(result, 'readable_content', 20000);
  for (const key of ['equations', 'key_points', 'uncertain_regions']) {
    if (result[key] === undefined) continue;
    if (!Array.isArray(result[key]) || result[key].length > 100) throw new AppFault(400, 'invalid_input', '请核对视觉结果。');
  }
  if (value.confidence !== undefined && value.confidence !== null && !CONFIDENCE.test(String(value.confidence))) {
    throw new AppFault(400, 'invalid_input', '请核对视觉结果。');
  }
  if (value.error !== undefined && value.error !== null && (typeof value.error !== 'object' || Array.isArray(value.error))) {
    throw new AppFault(400, 'invalid_input', '请核对视觉结果。');
  }
  const hasContent = ['title','probable_course','topic','summary','readable_content'].some(key => typeof result[key] === 'string' && result[key].trim()) ||
    ['equations','key_points','uncertain_regions'].some(key => Array.isArray(result[key]) && result[key].length > 0);
  if (value.status === 'completed' && (!hasContent || value.error != null)) {
    throw new AppFault(400, 'invalid_input', '视觉分析没有完成，原件保留；请提交失败状态和原因。');
  }
  return value;
}

async function actor(store: CloudStore, req: Request, visionSecret: string): Promise<{via: 'bot' | 'worker'; workspaceId: string | null}> {
  if (await botAuthorized(req.headers.get('Authorization'), visionSecret, store.secret)) return {via: 'bot', workspaceId: null};
  if (req.headers.get('X-Eric-Worker-Key')) {
    const principal = await store.worker(req);
    return {via: 'worker', workspaceId: principal.workspace.id};
  }
  throw new AppFault(401, 'unauthorized', '此连接不可用。');
}

export async function visionRoute(store: CloudStore, req: Request, url: URL, path: string, visionSecret: string, json: (value: any, status?: number) => Response): Promise<Response> {
  const principal = await actor(store, req, visionSecret);
  if (path === '/worker/vision/content' && req.method === 'GET') {
    const query = exactlyOne(url);
    const captureId = query.capture_id || captureIdOf(query.resource_id || '');
    const capture = await loadCapture(store, captureId, principal.workspaceId);
    const file = selectImage(capture, query.resource_id);
    return fileResponse(store, req, capture.workspace_id, file.resource_id);
  }
  if (path === '/worker/vision/manifest' && req.method === 'GET') {
    const query = exactlyOne(url);
    const captureId = query.capture_id || captureIdOf(query.resource_id || '');
    const capture = await loadCapture(store, captureId, principal.workspaceId);
    const file = selectImage(capture, query.resource_id);
    const {resource} = await readableResource(store, capture.workspace_id, file.resource_id);
    const rows = await parts(store, capture.workspace_id, resource);
    return json({filename: resource.filename, mime: resource.mime, bytes: resource.bytes, sha256: resource.sha256, part_count: rows.length});
  }
  if (path === '/worker/vision/results' && req.method === 'POST') {
    const payload = visionPayload(await boundedJson(req, 200_000));
    const capture = await loadCapture(store, payload.capture_id, principal.workspaceId);
    selectImage(capture, payload.resource_id);
    const row = await store.rpc('append_vision_result', {p_workspace: capture.workspace_id, p_payload: payload});
    return json({ok: true, kind: row.kind, id: row.id, revision: row.revision});
  }
  if (path === '/worker/vision/results' && req.method === 'GET') {
    if (url.searchParams.has('object_path') || url.searchParams.has('resource_id')) throw new AppFault(400, 'invalid_request', '请使用 capture_id 读取视觉结果。');
    const captureId = url.searchParams.get('capture_id') || '';
    const capture = await loadCapture(store, captureId, principal.workspaceId);
    const latest = url.searchParams.get('latest') === '1';
    const items = await store.db('eric_app_records?workspace_id=eq.' + capture.workspace_id + '&kind=eq.vision_result&deleted=eq.false&payload->>capture_id=eq.' + capture.id + '&order=revision.desc&limit=' + (latest ? 1 : 20) + '&select=kind,id,revision,payload,updated_at');
    const inbox = capture.raw_item_id
      ? (await store.db('eric_app_records?workspace_id=eq.' + capture.workspace_id + '&kind=eq.inbox_item&id=eq.' + encodeURIComponent(capture.raw_item_id) + '&deleted=eq.false&select=id,revision,payload&limit=1'))[0] || null
      : null;
    return json({
      ok: true,
      capture_id: capture.id,
      ...(latest ? {latest: items[0] || null} : {}),
      items,
      capture: {
        vision_status: capture.vision_status ?? null,
        vision_provider: capture.vision_provider ?? null,
        vision_analysis: capture.vision_analysis ?? null,
        vision_confidence: capture.vision_confidence ?? null,
        vision_processed_at: capture.vision_processed_at ?? null,
        vision_error: capture.vision_error ?? null,
      },
      inbox: inbox ? {id: inbox.id, revision: inbox.revision, preview: inbox.payload?.preview ?? null, preview_source: inbox.payload?.preview_source ?? null, vision_status: inbox.payload?.vision_status ?? null, vision_analysis: inbox.payload?.vision_analysis ?? null} : null,
    });
  }
  throw new AppFault(404, 'not_found', '此同步操作不存在。');
}
