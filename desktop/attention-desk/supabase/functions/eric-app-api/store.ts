// Server-only transport. Callers never supply a host, secret or workspace scope.
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const SHA = /^[a-f0-9]{64}$/;
export const ID = /^[A-Za-z0-9_.:-]{1,256}$/;
export const BUCKET = 'eric-app-private';
export const PART_BYTES = 16_000_000;

export class AppFault extends Error {
  constructor(public status: number, public code: string, public publicMessage: string) { super(code); }
}

export async function hash(value: string | Uint8Array): Promise<string> {
  const bytes = typeof value === 'string' ? new TextEncoder().encode(value) : value;
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes as BufferSource)), byte => byte.toString(16).padStart(2, '0')).join('');
}

export function stable(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + stable((value as any)[key])).join(',') + '}';
  const result = JSON.stringify(value);
  if (result === undefined) throw new AppFault(400,'invalid_input','内容不完整，请核对后重试。');
  return result;
}

export function identifier(value: string | null): string {
  if (!value || !ID.test(value)) throw new AppFault(400, 'invalid_reference', '这份资料的链接无效，请从列表重新打开。');
  return value;
}

export async function boundedJson(req: Request, max = 1_500_000): Promise<any> {
  const value = await boundedBytes(req, max);
  try { return JSON.parse(new TextDecoder().decode(value)); }
  catch { throw new AppFault(400, 'invalid_input', '内容没有完整发送，请保留输入后重试。'); }
}

export async function boundedBytes(req: Request, max: number): Promise<Uint8Array> {
  const length = Number(req.headers.get('Content-Length') || 0);
  if (!Number.isSafeInteger(length) || length < 0 || length > max) throw new AppFault(413, 'file_too_large', '文件较大，请拆分后再发送。');
  if (!req.body) throw new AppFault(400, 'missing_input', '还没有收到内容，请重试。');
  const reader = req.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const item = await reader.read();
      if (item.done) break;
      total += item.value.length;
      if (total > max) { await reader.cancel(); throw new AppFault(413, 'file_too_large', '文件较大，请拆分后再发送。'); }
      chunks.push(item.value);
    }
  } finally { reader.releaseLock(); }
  const result = new Uint8Array(total);
  let position = 0;
  for (const chunk of chunks) { result.set(chunk, position); position += chunk.length; }
  return result;
}

const MESSAGES: Record<string, [number, string]> = {
  eric_idempotency_conflict: [409, '同一次发送的内容发生了变化，请重新开始这次操作。'],
  eric_resource_conflict: [409, '文件版本不一致，原文件仍然保留。请重新选择文件。'],
  eric_resource_incomplete: [409, '文件还没有完整接收，会继续发送。'],
  eric_resource_missing: [503, '文件尚未确认保存，会自动继续重试。'],
  eric_resource_not_found: [404, '这份文件暂时不可用，请稍后重试。'],
  eric_capture_not_found: [404, '没有找到这次收集，请返回最近上传查看。'],
  eric_record_not_found: [404, '没有找到这份资料，请返回列表查看。'],
  eric_version_conflict: [409, '内容刚在另一处更新，请核对最新内容后再保存。'],
  eric_command_pending: [409, '上一项修改正在保存，请稍后再试。'],
  eric_device_revoked: [401, '此设备的访问已撤销，请重新登录。'],
  eric_stale_lease: [409, '处理已由另一次恢复接续，原件保留。'],
  eric_stale_projection: [409, '资料已经更新，旧结果没有覆盖新内容。'],
  eric_review_unavailable: [409, '这份复盘尚未准备好，可以先查看课堂原件。'],
  eric_invalid_vision: [400, '视觉结果不完整，请核对后重试。'],
};

export class CloudStore {
  base: string;
  secret: string;
  fetcher: typeof fetch;
  clock: () => Date;
  headers: Record<string, string>;

  constructor({base, secret, fetcher = fetch, clock = () => new Date()}: {base: string; secret: string; fetcher?: typeof fetch; clock?: () => Date}) {
    this.base = base.replace(/\/$/, ''); this.secret = secret; this.fetcher = fetcher; this.clock = clock;
    if (!/^https:\/\/[a-z0-9]+\.supabase\.co$/.test(this.base) || !secret) throw new AppFault(503, 'not_configured', 'Eric OS 暂时无法连接，请稍后重试。');
    this.headers = {apikey: secret};
    if (secret.startsWith('eyJ')) this.headers.Authorization = 'Bearer ' + secret;
  }

  async fetch(path: string, init: RequestInit = {}): Promise<Response> {
    try {
      const headers = new Headers(this.headers);
      new Headers(init.headers).forEach((value,key)=>headers.set(key,value));
      return await this.fetcher(this.base + path, {...init, redirect: 'error', signal: AbortSignal.timeout(30_000),
        headers});
    } catch { throw new AppFault(503, 'temporarily_unavailable', '连接暂时中断，已保存的内容会保留，请稍后重试。'); }
  }

  async db(path: string, method = 'GET', body?: unknown): Promise<any> {
    const response = await this.fetch('/rest/v1/' + path, {method,
      headers: {'Content-Type':'application/json', Prefer:'return=representation'},
      ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    if (!response.ok) {
      const parsed = await response.json().catch(() => ({}));
      const error = parsed && typeof parsed === 'object' ? parsed : {};
      const code = /\beric_[a-z_]+\b/.exec(error.message || '')?.[0];
      if (code && MESSAGES[code]) { const [status,message] = MESSAGES[code]; throw new AppFault(status,code,message); }
      if (code?.startsWith('eric_invalid')) throw new AppFault(400,code,'请核对填写内容，当前资料和原件仍然保留。');
      if (error.code === '23505') throw new AppFault(409,'duplicate_request','这项操作已接收，请重新读取最新状态。');
      if (error.code === '42501' || response.status === 401 || response.status === 403) throw new AppFault(403,'data_access_denied','当前连接没有保存权限，请核对服务配置。');
      if (['42P01','42703','42883','PGRST202','PGRST204','PGRST205'].includes(error.code)) throw new AppFault(500,'schema_mismatch','服务的数据结构需要核对，原件保留；请联系维护者。');
      throw new AppFault(503,'data_unavailable','暂时无法确认保存结果，系统会保留内容并重试。');
    }
    return response.status === 204 ? null : response.json();
  }

  rpc(name: string, body: unknown): Promise<any> { return this.db('rpc/eric_app_' + name, 'POST', body); }

  async principal(req: Request): Promise<any> {
    const token = req.headers.get('Authorization')?.match(/^Bearer ([A-Za-z0-9_.-]{30,8192})$/)?.[1];
    if (!token || token.split('.').length !== 3) throw new AppFault(401,'sign_in_required','请先登录 Eric OS。');
    let claims;
    try {
      const part = token.split('.')[1].replace(/-/g,'+').replace(/_/g,'/');
      claims = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(part),x=>x.charCodeAt(0))));
    } catch { throw new AppFault(401,'sign_in_required','请重新登录，已保存的内容不会丢失。'); }
    if (claims.role !== 'authenticated' || !UUID.test(claims.sub || '') || !UUID.test(claims.session_id || '') ||
        !Number.isFinite(claims.exp) || claims.exp * 1000 <= this.clock().getTime()) throw new AppFault(401,'sign_in_required','请重新登录，已保存的内容不会丢失。');
    // Claims above are only a cheap format check. Supabase verifies the actual token.
    const response = await this.fetch('/auth/v1/user', {headers:{Authorization:'Bearer '+token}});
    if (!response.ok) {
      if ([401,403].includes(response.status)) throw new AppFault(401,'sign_in_required','登录状态已失效，请重新登录。');
      throw new AppFault(503,'authentication_unavailable','暂时无法核对登录状态，恢复连接后会自动重试。');
    }
    const user = await response.json();
    if (user.id !== claims.sub || !user.email_confirmed_at) throw new AppFault(401,'sign_in_required','请使用已验证的账号登录。');
    const workspaces = await this.db('eric_app_workspaces?owner_id=eq.' + user.id + '&limit=1');
    if (!workspaces[0]) throw new AppFault(403,'owner_access_required','这个账号尚未开通此 Eric OS 的访问权限。');
    return {user, workspace: workspaces[0], sessionId: claims.session_id};
  }

  async session(req: Request): Promise<any> {
    const principal = await this.principal(req);
    const rows = await this.db('eric_app_devices?workspace_id=eq.'+principal.workspace.id+'&session_id=eq.'+principal.sessionId+'&limit=1');
    if (!rows[0] || rows[0].user_id !== principal.user.id || rows[0].revoked_at) throw new AppFault(401,'device_unavailable','请在此设备重新登录。');
    return {...principal, device: rows[0]};
  }

  async worker(req: Request): Promise<any> {
    const token = req.headers.get('X-Eric-Worker-Key') || '';
    if (!/^ework_[A-Za-z0-9_-]{43}$/.test(token)) throw new AppFault(401,'unauthorized','此连接不可用。');
    const rows = await this.db('eric_app_processors?key_hash=eq.'+await hash(token)+'&revoked_at=is.null&select=id,workspace_id&limit=1');
    if (!rows[0]) throw new AppFault(401,'unauthorized','此连接已撤销。');
    const workspaces = await this.db('eric_app_workspaces?id=eq.'+rows[0].workspace_id+'&limit=1');
    if (!workspaces[0]) throw new AppFault(503,'workspace_not_ready','资料还在准备，请稍后重试。');
    return {workspace: workspaces[0], device: rows[0]};
  }

  async one(workspace: string, kind: string, id: string): Promise<any> {
    const rows = await this.db('eric_app_records?workspace_id=eq.'+workspace+'&kind=eq.'+kind+'&id=eq.'+encodeURIComponent(id)+'&deleted=eq.false&limit=1');
    return rows[0] || null;
  }

  async rows(workspace: string, kind: string, suffix = ''): Promise<any[]> {
    const rows: any[] = [];
    for (let offset=0;offset<10_000;offset+=500) {
      const batch = await this.db('eric_app_records?workspace_id=eq.'+workspace+'&kind=eq.'+kind+'&deleted=eq.false&order=id.asc&limit=500&offset='+offset+suffix);
      rows.push(...batch);
      if (batch.length<500) return rows;
    }
    throw new AppFault(503,'collection_requires_filter','资料较多，请缩小筛选范围后重试。');
  }

  async page(workspace: string,kind: string,offset: number,limit: number,filter='',order='updated_at.desc,id.asc'): Promise<any> {
    const response = await this.fetch('/rest/v1/eric_app_records?workspace_id=eq.'+workspace+'&kind=eq.'+kind+'&deleted=eq.false&order='+order+'&offset='+offset+'&limit='+limit+filter,
      {headers:{Prefer:'count=exact'}});
    if (!response.ok) throw new AppFault(503,'data_unavailable','暂时无法读取资料，请稍后重试。');
    const total = Number(response.headers.get('Content-Range')?.split('/')[1]);
    if (!Number.isSafeInteger(total)) throw new AppFault(503,'incomplete_page','列表尚未完整读取，请稍后重试。');
    return {items:await response.json(),total,offset,limit};
  }

  async putPart(workspace: string, id: string, index: number, body: Uint8Array): Promise<void> {
    if (!body.length || body.length > PART_BYTES) throw new AppFault(413,'invalid_part','文件分段未完整发送，请重试。');
    const digest = await hash(body), path = workspace + '/' + digest;
    const uploaded = await this.fetch('/storage/v1/object/'+BUCKET+'/'+path, {
      method:'POST', headers:{'Content-Type':'application/octet-stream','x-upsert':'false'},body:body as BodyInit});
    if (!uploaded.ok) {
      // An immutable retry can reuse an existing part, but a generic storage failure cannot.
      const error = await uploaded.json().catch(()=>({}));
      if (uploaded.status !== 409 && String(error.statusCode) !== '409' && error.error !== 'Duplicate') throw new AppFault(503,'storage_unavailable','文件还没有确认保存，会继续重试。');
    }
    await this.rpc('register_part',{p_workspace:workspace,p_id:id,p_index:index,p_sha256:digest,p_bytes:body.length});
  }

  async resource(workspace: string, id: string): Promise<any> {
    const rows = await this.db('eric_app_resources?workspace_id=eq.'+workspace+'&id=eq.'+encodeURIComponent(id)+'&complete=eq.true&limit=1');
    if (!rows[0]) throw new AppFault(404,'file_not_ready','这份文件还在准备，稍后会自动恢复。');
    return rows[0];
  }
}
