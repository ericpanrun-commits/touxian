import { assertEquals, assertRejects } from 'jsr:@std/assert@1';
import { createHandler } from './handler.ts';
import { fileResponse, FULL_DOWNLOAD_LIMIT } from './files.ts';
import { AppFault, CloudStore } from './store.ts';
import { ticket } from './files.ts';

const BASE = 'https://mantuehuzptactymjpqg.supabase.co';
const SERVICE = 'service-role-test-secret';
const WORKSPACE = '50e5a3ac-dd15-4f0a-a93c-ace4b735d16d';
const DEVICE = 'b7c2e0a1-6d44-4a1e-9c31-0a5f2e8b7c10';
const KEY = 'ework_' + 'a'.repeat(43);
const SMALL_ID = 'textbook:small';
const LARGE_ID = 'textbook:large';
const EXACT_ID = 'textbook:exact';
const SHA_SMALL = '11'.repeat(32);
const SHA_LARGE = '22'.repeat(32);
const SHA_EXACT = '33'.repeat(32);
const PART_SMALL = 'aa'.repeat(32);
const PART_LARGE = 'bb'.repeat(32);
const PART_EXACT = 'cc'.repeat(32);
const LIMIT = FULL_DOWNLOAD_LIMIT;

const CATALOG: Record<string, {id: string; sha: string; bytes: number; part: string}> = {
  [SMALL_ID]: {id: SMALL_ID, sha: SHA_SMALL, bytes: 5, part: PART_SMALL},
  [LARGE_ID]: {id: LARGE_ID, sha: SHA_LARGE, bytes: LIMIT + 1, part: PART_LARGE},
  [EXACT_ID]: {id: EXACT_ID, sha: SHA_EXACT, bytes: LIMIT, part: PART_EXACT},
};

function queryValue(url: string, key: string): string {
  const match = new RegExp('[?&]' + key + '=eq\\.([^&]*)').exec(url);
  return decodeURIComponent(match?.[1] || '');
}

function app(calls: string[]) {
  return createHandler({
    base: BASE, secret: SERVICE, origins: ['https://eric.example'], fetcher: async (input, init) => {
      const url = String(input);
      calls.push(url + ' range=' + (new Headers(init?.headers).get('Range') || ''));
      if (url.includes('/eric_app_processors?')) return Response.json([{id: 'proc-1', workspace_id: WORKSPACE}]);
      if (url.includes('/eric_app_workspaces?')) return Response.json([{id: WORKSPACE, timezone: 'Asia/Shanghai'}]);
      if (url.includes('/eric_app_devices?')) return Response.json([{id: DEVICE}]);
      if (url.includes('/eric_app_resources?')) {
        const row = CATALOG[queryValue(url, 'id')];
        return Response.json(row ? [{id: row.id, filename: 'book.pdf', mime: 'application/pdf', sha256: row.sha, bytes: row.bytes, part_count: 1, complete: true}] : []);
      }
      if (url.includes('/eric_app_resource_parts?')) {
        const row = CATALOG[queryValue(url, 'resource_id')];
        if (!row) return Response.json([]);
        return Response.json([{part_index: 0, sha256: row.part, bytes: row.bytes, object_path: WORKSPACE + '/' + row.part}]);
      }
      if (url.includes('/storage/v1/object/')) {
        const range = new Headers(init?.headers).get('Range') || '';
        const match = /^bytes=(\d+)-(\d+)$/.exec(range);
        if (!match) return new Response('missing range', {status: 400});
        const begin = Number(match[1]), end = Number(match[2]), length = end - begin + 1;
        const partSha = url.split('/').pop() || '';
        const row = Object.values(CATALOG).find(item => item.part === partSha);
        const body = new Uint8Array(length);
        body.fill(length > 64 ? 0 : 9);
        return new Response(body, {status: 206, headers: {'Content-Range': `bytes ${begin}-${end}/${row?.bytes}`}});
      }
      return new Response('unexpected ' + url, {status: 500});
    }, clock: () => new Date('2026-11-05T00:00:00Z'),
  });
}

function workerGet(id: string, headers: Record<string, string> = {}) {
  return new Request(BASE + '/functions/v1/eric-app-api/worker/files/' + encodeURIComponent(id) + '/content', {
    headers: {'X-Eric-Worker-Key': KEY, ...headers},
  });
}

Deno.test('worker download above 50MiB without Range is 416 and does not read storage', async () => {
  const calls: string[] = [];
  const response = await app(calls)(workerGet(LARGE_ID));
  assertEquals(response.status, 416);
  const body = await response.json();
  assertEquals(body.detail.code, 'range_required');
  assertEquals(body.detail.message.includes('大文件必须按范围读取'), true);
  assertEquals(body.detail.message.includes('Large files must be read in ranges.'), true);
  assertEquals(calls.some(call => call.includes('/storage/v1/object/')), false);
  assertEquals(calls.some(call => call.includes('/eric_app_resource_parts?')), false);
});

Deno.test('worker ranged read of a large file stays 206 and a small unranged file stays 200', async () => {
  const calls: string[] = [];
  const handler = app(calls);
  const ranged = await handler(workerGet(LARGE_ID, {Range: 'bytes=0-3'}));
  assertEquals(ranged.status, 206);
  assertEquals(ranged.headers.get('content-range'), 'bytes 0-3/' + (LIMIT + 1));
  assertEquals(ranged.headers.get('etag'), '"' + SHA_LARGE + '"');
  assertEquals(ranged.headers.get('cache-control'), 'private, max-age=86400, immutable');
  assertEquals(new Uint8Array(await ranged.arrayBuffer()), new Uint8Array([9, 9, 9, 9]));
  const small = await handler(workerGet(SMALL_ID));
  assertEquals(small.status, 200);
  assertEquals(small.headers.get('etag'), '"' + SHA_SMALL + '"');
  assertEquals(new Uint8Array(await small.arrayBuffer()), new Uint8Array([9, 9, 9, 9, 9]));
  const exact = await handler(workerGet(EXACT_ID));
  assertEquals(exact.status, 200);
  assertEquals((await exact.arrayBuffer()).byteLength, LIMIT);
});

Deno.test('If-None-Match of the content sha256 returns 304 and skips storage', async () => {
  const calls: string[] = [];
  const handler = app(calls);
  for (const header of ['"' + SHA_SMALL + '"', 'W/"' + SHA_SMALL + '"', '*']) {
    const response = await handler(workerGet(SMALL_ID, {'If-None-Match': header}));
    assertEquals(response.status, 304);
    assertEquals(response.headers.get('etag'), '"' + SHA_SMALL + '"');
    assertEquals(response.headers.get('cache-control'), 'private, max-age=86400, immutable');
    assertEquals(await response.arrayBuffer(), new ArrayBuffer(0));
  }
  assertEquals(calls.some(call => call.includes('/storage/')), false);
  const missed = await handler(workerGet(SMALL_ID, {'If-None-Match': '"' + SHA_LARGE + '"'}));
  assertEquals(missed.status, 200);
  await missed.body?.cancel();
});

Deno.test('ticket and direct file reads above 50MiB stay 200 without a worker key', async () => {
  const calls: string[] = [];
  const handler = app(calls);
  const signer = new CloudStore({base: BASE, secret: SERVICE, clock: () => new Date('2026-11-05T00:00:00Z'), fetcher: async () => Response.json([])});
  const link = await ticket(signer, {workspace: {id: WORKSPACE}, device: {id: DEVICE}}, LARGE_ID);
  const browser = await handler(new Request(BASE + '/functions/v1/eric-app-api/files/' + encodeURIComponent(LARGE_ID) + '/content?ticket=' + encodeURIComponent(link), {
    headers: {Origin: 'https://eric.example'},
  }));
  assertEquals(browser.status, 200);
  assertEquals(browser.headers.get('etag'), '"' + SHA_LARGE + '"');
  assertEquals(browser.headers.get('access-control-expose-headers'), 'Content-Range,Accept-Ranges,Content-Length,ETag');
  assertEquals((await browser.arrayBuffer()).byteLength, LIMIT + 1);

  const direct = await fileResponse({
    async resource() { return {id: LARGE_ID, filename: 'book.pdf', mime: 'application/pdf', sha256: SHA_LARGE, bytes: LIMIT + 1, part_count: 1, complete: true}; },
    async db() { return [{part_index: 0, sha256: PART_LARGE, bytes: LIMIT + 1, object_path: WORKSPACE + '/' + PART_LARGE}]; },
    async fetch(_path: string, init?: RequestInit) {
      const range = new Headers(init?.headers).get('Range') || '';
      const match = /^bytes=(\d+)-(\d+)$/.exec(range);
      if (!match) throw new Error('expected a storage range');
      const length = Number(match[2]) - Number(match[1]) + 1;
      return new Response(new Uint8Array(length), {status: 206, headers: {'Content-Range': `bytes ${match[1]}-${match[2]}/${LIMIT + 1}`}});
    },
  } as unknown as CloudStore, new Request(BASE + '/files/' + LARGE_ID + '/content'), WORKSPACE, LARGE_ID);
  assertEquals(direct.status, 200);
  assertEquals((await direct.arrayBuffer()).byteLength, LIMIT + 1);
  await assertRejects(() => fileResponse({
    async resource() { return {id: LARGE_ID, filename: 'book.pdf', mime: 'application/pdf', sha256: SHA_LARGE, bytes: LIMIT + 1, part_count: 1, complete: true}; },
  } as unknown as CloudStore, new Request(BASE + '/worker/files/' + LARGE_ID + '/content'), WORKSPACE, LARGE_ID, true), AppFault);
});
