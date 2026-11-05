import { assertEquals } from 'jsr:@std/assert@1';
import { createHandler } from './handler.ts';
import { inboxCard, presentInbox } from './views.ts';
import { botAuthorized } from './vision.ts';

const CAPTURE = '88584ff1-867f-47e1-8276-a8412fb96830';
const WORKSPACE = '50e5a3ac-dd15-4f0a-a93c-ace4b735d16d';
const SHA = '506180e3adc4d31a83746d89c202e3188ab7f0b7f728c9b53ac5239c5fbd2ce9';
const RESOURCE = 'capture:' + CAPTURE + ':' + SHA;
const PART = 'ab'.repeat(32);
const SECRET = 'vision-bot-secret-ok';
const SERVICE = 'service-role-test-secret';
const BASE = 'https://mantuehuzptactymjpqg.supabase.co';

function captureRow() {
  return {
    id: CAPTURE, workspace_id: WORKSPACE, raw_item_id: 'e32765f5-43c9-4a20-8e50-d7afc9e7c773',
    files: [{resource_id: RESOURCE, sha256: SHA, mime: 'image/png', filename: 'IMG_1080.png', bytes: 5, part_count: 1}],
    vision_status: 'pending', vision_provider: null, vision_analysis: null, vision_confidence: null, vision_processed_at: null, vision_error: null,
  };
}

function handler(fetcher: typeof fetch) {
  return createHandler({base: BASE, secret: SERVICE, visionSecret: SECRET, origins: [], fetcher, clock: () => new Date('2026-11-02T08:00:00Z')});
}

Deno.test('bot bearer rejects empty, short, and the service secret', async () => {
  assertEquals(await botAuthorized(null, SECRET, SERVICE), false);
  assertEquals(await botAuthorized('Bearer short-secret', SECRET, SERVICE), false);
  assertEquals(await botAuthorized('Bearer ' + SERVICE, SERVICE, SERVICE), false);
  assertEquals(await botAuthorized('Bearer ' + SECRET, 'short', SERVICE), false);
  assertEquals(await botAuthorized('Bearer  ' + SECRET + '  ', SECRET, SERVICE), false);
  assertEquals(await botAuthorized('Bearer ' + SECRET, SECRET, SERVICE), true);
});

Deno.test('vision routes require the bot secret and one business id', async () => {
  const calls: string[] = [];
  const app = handler(async (input) => { calls.push(String(input)); return new Response('[]', {status: 200, headers: {'content-type': 'application/json'}}); });
  const base = BASE + '/functions/v1/eric-app-api';
  const denied = await app(new Request(base + '/worker/vision/content?capture_id=' + CAPTURE));
  assertEquals(denied.status, 401);
  const service = await app(new Request(base + '/worker/vision/content?capture_id=' + CAPTURE, {headers: {Authorization: 'Bearer ' + SERVICE}}));
  assertEquals(service.status, 401);
  const both = await app(new Request(base + '/worker/vision/content?capture_id=' + CAPTURE + '&resource_id=' + RESOURCE, {headers: {Authorization: 'Bearer ' + SECRET}}));
  assertEquals(both.status, 400);
  const path = await app(new Request(base + '/worker/vision/content?capture_id=' + CAPTURE + '&object_path=' + WORKSPACE + '/' + SHA, {headers: {Authorization: 'Bearer ' + SECRET}}));
  assertEquals(path.status, 400);
  assertEquals(calls.length, 0);
});

Deno.test('bearer fetch streams the capture image and manifest hides storage paths', async () => {
  const bytes = new Uint8Array([1, 2, 3, 4, 5]);
  let wrote: any = null;
  const app = handler(async (input, init) => {
    const url = String(input);
    if (url.includes('/eric_app_captures?')) return Response.json([captureRow()]);
    if (url.includes('/eric_app_resources?')) return Response.json([{id: RESOURCE, filename: 'IMG_1080.png', mime: 'image/png', sha256: SHA, bytes: 5, part_count: 1, complete: true}]);
    if (url.includes('/eric_app_resource_parts?')) return Response.json([{part_index: 0, sha256: PART, bytes: 5, object_path: WORKSPACE + '/' + PART}]);
    if (url.includes('/storage/v1/object/')) return new Response(bytes, {status: 206, headers: {'Content-Range': 'bytes 0-4/5'}});
    if (url.includes('/rpc/eric_app_append_vision_result')) {
      wrote = JSON.parse(String(init?.body || '{}'));
      return Response.json({ok: true, kind: 'vision_result', id: 'vision:' + CAPTURE + ':abc', revision: 9});
    }
    return new Response('not found ' + url, {status: 500});
  });
  const headers = {Authorization: 'Bearer ' + SECRET};
  const base = BASE + '/functions/v1/eric-app-api';
  const content = await app(new Request(base + '/worker/vision/content?capture_id=' + CAPTURE, {headers}));
  assertEquals(content.status, 200);
  assertEquals(content.headers.get('content-type'), 'image/png');
  assertEquals(content.headers.get('cache-control'), 'private, max-age=86400, immutable');
  assertEquals(content.headers.get('etag'), '"' + SHA + '"');
  assertEquals(new Uint8Array(await content.arrayBuffer()), bytes);
  const manifest = await app(new Request(base + '/worker/vision/manifest?resource_id=' + encodeURIComponent(RESOURCE), {headers}));
  const described = await manifest.json();
  assertEquals(described, {filename: 'IMG_1080.png', mime: 'image/png', bytes: 5, sha256: SHA, part_count: 1});
  const write = await app(new Request(base + '/worker/vision/results', {
    method: 'POST', headers: {...headers, 'content-type': 'application/json'},
    body: JSON.stringify({
      schema: 'eric-os-vision-result.v1', capture_id: CAPTURE, resource_id: RESOURCE, sha256: SHA,
      status: 'completed', analyzed_at: '2026-11-02T08:30:00Z', analyzer: 'eric-os-bot', provider: 'grok',
      source: 'original_image', confidence: 'high',
      result: {title: '极限复习', summary: '导数定义', probable_course: '高等数学 II', key_points: ['极限'], equations: [], uncertain_regions: []},
    }),
  }));
  assertEquals(write.status, 200);
  assertEquals(await write.json(), {ok: true, kind: 'vision_result', id: 'vision:' + CAPTURE + ':abc', revision: 9});
  assertEquals(wrote.p_workspace, WORKSPACE);
  assertEquals(wrote.p_payload.resource_id, RESOURCE);
  assertEquals(Object.hasOwn(wrote.p_payload, 'object_path'), false);
});

Deno.test('inbox presentation does not use OCR as the default body', () => {
  const ocr = '1： 35PM\n\n1\n\nWed Oct OCR';
  const item = {
    id: 'e32765f5-43c9-4a20-8e50-d7afc9e7c773', title: 'IMG_1080.png', input_kind: 'image', preview: ocr,
    course: {name: '高等数学 II'}, attachments: [{filename: 'IMG_1080.png', mime_type: 'image/png'}],
    review: {status: 'pending', snapshot: {original_text: ocr, id: 'snap'}},
  };
  const shown = presentInbox(item, new URL('https://example.test/inbox/x'));
  assertEquals(shown.preview, '等待 AI 分析…');
  assertEquals(shown.review.snapshot.original_text, undefined);
  assertEquals(shown.review.snapshot.id, 'snap');
  assertEquals(JSON.stringify(shown).includes('35PM'), false);
  const advanced = presentInbox(item, new URL('https://example.test/inbox/x?advanced=1'));
  assertEquals(advanced.debug.collapsed, true);
  assertEquals(advanced.debug.original_text, ocr);
  const vision = inboxCard({...item, vision_status: 'completed', vision_confidence: 'high', vision_analysis: {title: '极限复习', summary: '导数定义', key_points: ['a'], equations: ['e']}});
  assertEquals(vision.body, '导数定义');
  assertEquals(vision.title, '极限复习');
  assertEquals(vision.key_point_count, 1);
});
