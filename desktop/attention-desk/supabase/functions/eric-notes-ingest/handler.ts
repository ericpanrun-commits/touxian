// No learner analysis runs here. Only private transport and durable receipts.
const BUCKET = 'eric-notes-transit';
const MAX = 50_000_000;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SHA = /^[a-f0-9]{64}$/;
const DEVICE = /^enote_[A-Za-z0-9_-]{43}$/;
const SYNC = /^esync_[A-Za-z0-9_-]{43}$/;

class Fault extends Error {
  constructor(public status: number, public code: string, public publicMessage: string) { super(code); }
}

export async function hash(value: string | Uint8Array): Promise<string> {
  const bytes = typeof value === 'string' ? new TextEncoder().encode(value) : value;
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), x => x.toString(16).padStart(2, '0')).join('');
}

async function bytes(req: Request): Promise<Uint8Array> {
  const length = Number(req.headers.get('content-length') || 0);
  if (!Number.isSafeInteger(length) || length < 0 || length > MAX) throw new Fault(413, 'file_too_large', '文件超过 50 MB，请拆分后分享。');
  if (!req.body) throw new Fault(400, 'missing_file', '没有收到笔记文件。');
  const reader = req.body.getReader(), chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const item = await reader.read();
      if (item.done) break;
      total += item.value.length;
      if (total > MAX) { await reader.cancel(); throw new Fault(413, 'file_too_large', '文件超过 50 MB，请拆分后分享。'); }
      chunks.push(item.value);
    }
  } finally { reader.releaseLock(); }
  const result = new Uint8Array(total);
  let offset = 0;
  for (const item of chunks) { result.set(item, offset); offset += item.length; }
  chunks.length = 0;
  if (total < 6 || !new TextDecoder().decode(result.subarray(0, 1024)).trimStart().startsWith('%PDF-')) throw new Fault(415, 'pdf_required', '请在分享捷径中使用“制作 PDF”。');
  return result;
}

function reply(value: unknown, status = 200): Response {
  return Response.json(value, { status, headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' } });
}

export function createHandler({ base, secret, fetcher = fetch, clock = () => new Date(), archive }: {
  base: string; secret: string; fetcher?: typeof fetch; clock?: () => Date;
  archive?: (value: {row:any;installation:string;body?:Uint8Array;filename?:string;sharedAt?:string})=>Promise<void>;
}) {
  base = base.replace(/\/$/, '');
  const headers: Record<string, string> = { apikey: secret };
  if (secret.startsWith('eyJ')) headers.Authorization = `Bearer ${secret}`;
  const jsonHeaders = { ...headers, 'Content-Type': 'application/json', Prefer: 'return=representation' };
  async function db(path: string, method = 'GET', body?: unknown): Promise<any> {
    const res = await fetcher(base + '/rest/v1/' + path, { method, headers: jsonHeaders, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    if (!res.ok) {
      const problem = await res.json().catch(() => ({}));
      if (/notes_(rate_limited|daily_limit|pending_limit)/.test(problem.message || '')) throw new Fault(429, 'notes_limit', '笔记暂存或发送次数达到上限，文件请保留待重试。');
      if (problem.code === '28000') throw new Fault(401, 'unauthorized', '分享连接已撤销或无效，请在 Eric OS 更新。');
      throw new Fault(503, 'metadata_unavailable', '暂未确认接收，请保留待发送文件后重试。');
    }
    return res.status === 204 ? [] : res.json();
  }
  async function auth(req: Request, role: 'upload' | 'sync') {
    const token = req.headers.get(role === 'upload' ? 'X-Eric-Notes-Key' : 'X-Eric-Sync-Key') || '';
    if (!(role === 'upload' ? DEVICE : SYNC).test(token)) throw new Fault(401, 'unauthorized', '分享连接无效。');
    const found = await db('rpc/eric_notes_authorize', 'POST', { p_hash: await hash(token), p_role: role });
    if (!found?.[0] || !UUID.test(found[0].id) || !UUID.test(found[0].installation_id)) throw new Fault(401, 'unauthorized', '分享连接无效。');
    return found[0];
  }
  function path(row: any, installation: string) {
    if (!SHA.test(row.sha256) || row.object_path !== `${installation}/${row.sha256}.pdf`) throw new Fault(503, 'invalid_transport_record', '暂存记录需要核对，云端原件保留。');
    return row.object_path;
  }
  async function sign(row: any, installation: string): Promise<string | null> {
    const object = path(row, installation);
    const res = await fetcher(base + `/storage/v1/object/sign/${BUCKET}/${object}`, { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ expiresIn: 300 }) });
    if (!res.ok) return null;
    const data = await res.json();
    const signed = data.signedURL || data.signedUrl;
    if (typeof signed !== 'string') return null;
    const target = new URL(signed.startsWith('/object/') ? '/storage/v1' + signed : signed, base);
    if (target.origin !== base || !target.pathname.startsWith(`/storage/v1/object/sign/${BUCKET}/${installation}/`)) throw new Fault(503, 'invalid_download_target', '暂存地址需要核对，原件保留。');
    return target.href;
  }
  async function cleanup(installation: string) {
    const rows = await db(`eric_notes_submissions?installation_id=eq.${installation}&state=eq.acked&payload_deleted_at=is.null&limit=10`);
    for (const row of rows) {
      const object = path(row, installation);
      const res = await fetcher(base + `/storage/v1/object/${BUCKET}`, { method: 'DELETE', headers: jsonHeaders, body: JSON.stringify({ prefixes: [object] }) });
      if (res.ok || res.status === 404) await db(`eric_notes_submissions?id=eq.${row.id}&installation_id=eq.${installation}&state=eq.acked`, 'PATCH', { payload_deleted_at: clock().toISOString() });
    }
  }
  async function recover(installation: string) {
    const cutoff = new Date(clock().getTime() - 120_000).toISOString();
    const rows = await db(`eric_notes_submissions?installation_id=eq.${installation}&state=eq.uploading&created_at=lt.${encodeURIComponent(cutoff)}&limit=5`);
    for (const row of rows) if (await sign(row, installation)) {
      if(archive)await archive({row,installation});
      await db(`eric_notes_submissions?id=eq.${row.id}&installation_id=eq.${installation}&state=eq.uploading`, 'PATCH', { state: 'pending', received_at: clock().toISOString() });
    }
  }
  return async function handler(req: Request): Promise<Response> {
    try {
      if (!secret || !/^https:\/\/[a-z0-9]+\.supabase\.co$/.test(base)) throw new Fault(503, 'not_configured', '笔记接收暂不可用，请保留待发送文件。');
      const route = new URL(req.url).pathname.replace(/^.*\/eric-notes-ingest/, '') || '/';
      if (req.method === 'GET' && route === '/health') return reply({ service: 'eric-notes-ingest', ready: true });
      if (req.method === 'POST' && ['/', '/upload'].includes(route)) {
        const device = await auth(req, 'upload');
        const body = await bytes(req), digest = await hash(body);
        let filename = req.headers.get('X-Eric-Filename') || 'Apple Notes 课堂笔记.pdf';
        try { filename = decodeURIComponent(filename); } catch { /* Header may already be plain text. */ }
        filename = filename.replace(/\\/g, '/').split('/').pop()!.replace(/[\x00-\x1f\x7f]/g, '').slice(0, 176);
        if (!filename.toLowerCase().endsWith('.pdf')) filename += '.pdf';
        let shared = req.headers.get('X-Eric-Shared-At') || clock().toISOString();
        const moment = Date.parse(shared);
        if (!/^\d{4}-\d\d-\d\dT/.test(shared) || !/(Z|[+-]\d\d:\d\d)$/.test(shared) || !Number.isFinite(moment) || moment > clock().getTime() + 300_000 || moment < Date.UTC(2020, 0)) shared = clock().toISOString();
        const rows = await db('rpc/eric_notes_prepare', 'POST', { p_installation: device.installation_id, p_device: device.id, p_sha256: digest, p_bytes: body.length, p_filename: filename, p_shared_at: shared });
        const row = rows[0];
        if (!row || !UUID.test(row.id)) throw new Fault(503, 'receipt_unavailable', '尚未确认接收，请保留待发送文件。');
        if (row.state !== 'uploading') {
          if(archive)await archive({row,installation:device.installation_id,body,filename,sharedAt:shared});
          return reply({ received: true, saved: true, message: '已接收', receipt_id: row.id, duplicate: true });
        }
        const object = path(row, device.installation_id);
        const upload = await fetcher(base + `/storage/v1/object/${BUCKET}/${object}`, { method: 'POST', headers: { ...headers, 'Content-Type': 'application/pdf', 'x-upsert': 'false' }, body });
        if (!upload.ok && !await sign(row, device.installation_id)) throw new Fault(503, 'storage_unavailable', '尚未确认保存，请保留待发送文件后重试。');
        if(archive)await archive({row,installation:device.installation_id,body,filename,sharedAt:shared});
        const saved = await db(`eric_notes_submissions?id=eq.${row.id}&installation_id=eq.${device.installation_id}&state=eq.uploading`, 'PATCH', { state: 'pending', received_at: clock().toISOString() });
        if (!saved.length) {
          const latest = await db(`eric_notes_submissions?id=eq.${row.id}&installation_id=eq.${device.installation_id}`);
          if (!['pending', 'acked'].includes(latest[0]?.state)) throw new Fault(503, 'receipt_unavailable', '尚未确认接收，请保留待发送文件。');
        }
        return reply({ received: true, saved: true, message: '已接收', receipt_id: row.id, duplicate: !upload.ok }, 202);
      }
      if (req.method === 'GET' && route === '/pending') {
        const device = await auth(req, 'sync');
        await cleanup(device.installation_id);
        await recover(device.installation_id);
        const rows = await db(`eric_notes_submissions?installation_id=eq.${device.installation_id}&state=eq.pending&order=created_at.asc&limit=5`);
        const submissions = [];
        for (const row of rows) {
          const download = await sign(row, device.installation_id);
          if (download) submissions.push({ id: row.id, sha256: row.sha256, bytes: row.bytes, filename: row.filename, shared_at: row.shared_at || row.received_at, received_at: row.received_at, download_url: download });
        }
        return reply({ submissions });
      }
      if (req.method === 'POST' && route === '/ack') {
        const device = await auth(req, 'sync');
        const value = await req.json();
        if (!UUID.test(value.id || '') || !SHA.test(value.sha256 || '') || !UUID.test(value.local_receipt_id || '') || !Number.isSafeInteger(value.bytes)) throw new Fault(400, 'invalid_ack', '接收确认无效，云端原件保留。');
        const rows = await db(`eric_notes_submissions?id=eq.${value.id}&installation_id=eq.${device.installation_id}`);
        const row = rows[0];
        if (!row || row.sha256 !== value.sha256 || row.bytes !== value.bytes || !['pending', 'acked'].includes(row.state)) throw new Fault(409, 'ack_mismatch', '接收确认不一致，云端原件保留。');
        // A committed ACK remains authoritative, including receipts whose
        // transit bytes were cleaned before permanent app archives existed.
        if (row.state === 'pending') {
          if(archive)await archive({row,installation:device.installation_id});
          await db(`eric_notes_submissions?id=eq.${value.id}&installation_id=eq.${device.installation_id}&state=eq.pending`, 'PATCH', { state: 'acked', acked_at: clock().toISOString(), local_receipt_id: value.local_receipt_id, filename: '', shared_at: null });
        }
        try { await cleanup(device.installation_id); } catch { /* Committed ack stays authoritative; next sync retries cleanup. */ }
        return reply({ acked: true });
      }
      if (route === '/devices' && ['GET', 'POST'].includes(req.method)) {
        const device = await auth(req, 'sync');
        if (req.method === 'GET') return reply({ devices: await db(`eric_notes_devices?installation_id=eq.${device.installation_id}&role=eq.upload&revoked_at=is.null&select=id,label,created_at,revoked_at&order=created_at.desc&limit=10`) });
        const existing = await db(`eric_notes_devices?installation_id=eq.${device.installation_id}&role=eq.upload&revoked_at=is.null&select=id&limit=10`);
        if (existing.length >= 10) throw new Fault(429, 'device_limit', '请先撤销不用的笔记分享连接。');
        const random = crypto.getRandomValues(new Uint8Array(32));
        const token = 'enote_' + btoa(String.fromCharCode(...random)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
        const added = await db('eric_notes_devices', 'POST', { installation_id: device.installation_id, role: 'upload', label: 'Eric 的笔记分享', key_hash: await hash(token) });
        return reply({ device_id: added[0].id, upload_key: token }, 201);
      }
      if (route === '/devices/revoke' && req.method === 'POST') {
        const device = await auth(req, 'sync'), value = await req.json();
        if (!UUID.test(value.id || '')) throw new Fault(400, 'invalid_device', '笔记连接标识无效。');
        const changed = await db(`eric_notes_devices?id=eq.${value.id}&installation_id=eq.${device.installation_id}&role=eq.upload`, 'PATCH', { revoked_at: clock().toISOString() });
        if (!changed.length) throw new Fault(404, 'device_not_found', '笔记连接不存在。');
        return reply({ revoked: true });
      }
      throw new Fault(404, 'not_found', '入口不存在。');
    } catch (error) {
      if (error instanceof Fault) return reply({ received: false, saved: false, code: error.code, message: error.publicMessage }, error.status);
      // Never log requests, credentials, signed URLs, filenames or note bytes.
      return reply({ received: false, saved: false, code: 'temporarily_unavailable', message: '暂未确认接收，请保留待发送文件后重试。' }, 503);
    }
  };
}
