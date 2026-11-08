import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';

const origin = 'https://eric-update-fixture.example';
const version = 'eric-os-shell-nav3-current';
const entry = '/assets/index-current.js';
const source = await readFile(new URL('../src/cloud/service-worker.js', import.meta.url), 'utf8');

function html(label, script = entry, build = version) {
  return '<!doctype html><head><link rel="manifest" href="/manifest.webmanifest">' +
    '<script type="module" src="' + script + '"></script>' +
    '<meta name="eric-build" content="' + build + '"></head>' +
    '<body><div id="root"></div>' + label + '</body>';
}

function harness(network) {
  const events = new Map();
  const responses = new Map([['/', new Response(html('CACHED_COMPLETE_VERSION'), {
    headers: {'Content-Type': 'text/html'},
  })]]);
  const requests = [];
  const messages = [];
  const cache = {
    async match(path) { return responses.get(path)?.clone(); },
    async put(path, response) { responses.set(path, response.clone()); },
    async keys() { return []; },
  };
  runInNewContext(source, {
    ERIC_SW: {cache: version, files: [], shell: [], entry},
    URL, Request, Response, Headers, AbortController, setTimeout, clearTimeout,
    self: {
      location: {origin},
      addEventListener(name, listener) { events.set(name, listener); },
      clients: {
        async claim() {},
        async matchAll() { return [{postMessage(value) { messages.push(value); }}]; },
      },
    },
    caches: {
      async open() { return cache; },
      async keys() { return [version]; },
    },
    async fetch(request) {
      requests.push(request);
      return network(request);
    },
  });
  function navigate(path = '/?view=today') {
    let response;
    events.get('fetch')({
      request: {url: origin + path, method: 'GET', mode: 'navigate'},
      respondWith(value) { response = value; },
    });
    return response;
  }
  return {events, responses, requests, messages, navigate};
}

test('An installed app navigation checks the network before its cached HTML', async () => {
  const worker = harness(() => new Response(html('FRESH_NETWORK_VERSION'), {
    headers: {'Content-Type': 'text/html'},
  }));
  assert.match(await (await worker.navigate()).text(), /FRESH_NETWORK_VERSION/);
  assert.equal(worker.requests.length, 1);
  assert.equal(worker.requests[0].cache, 'no-store');
  assert.equal(worker.requests[0].credentials, 'omit');
  assert.match(await worker.responses.get('/').text(), /FRESH_NETWORK_VERSION/);
});

test('A newer online release does not overwrite an older complete offline bundle', async () => {
  const worker = harness(() => new Response(html(
    'NEXT_RELEASE', '/assets/index-future.js', 'eric-os-shell-nav3-future',
  ), {headers: {'Content-Type': 'text/html'}}));
  assert.match(await (await worker.navigate()).text(), /NEXT_RELEASE/);
  assert.match(await worker.responses.get('/').text(), /CACHED_COMPLETE_VERSION/);
});

test('Offline navigation reuses the complete shell without clearing account storage', async () => {
  const worker = harness(() => { throw new TypeError('Offline'); });
  assert.match(await (await worker.navigate()).text(), /CACHED_COMPLETE_VERSION/);
  assert.equal(worker.responses.size, 1);
});

test('A server failure or authentication redirect cannot poison the offline shell', async () => {
  const failure = harness(() => new Response('Upstream unavailable', {status: 503}));
  assert.match(await (await failure.navigate()).text(), /CACHED_COMPLETE_VERSION/);
  const redirect = harness(() => {
    const response = new Response(html('AUTH_CALLBACK'), {headers: {'Content-Type': 'text/html'}});
    Object.defineProperty(response, 'url', {value: origin + '/?code=private-callback'});
    return response;
  });
  assert.match(await (await redirect.navigate()).text(), /CACHED_COMPLETE_VERSION/);
  assert.match(await redirect.responses.get('/').text(), /CACHED_COMPLETE_VERSION/);
});

test('Authentication, private APIs and build checks stay outside the shell cache', () => {
  const worker = harness(() => { throw new Error('Unexpected request'); });
  for (const [path, mode, method] of [
    ['/?code=private', 'navigate', 'GET'],
    ['/auth/callback', 'navigate', 'GET'],
    ['/api/private', 'navigate', 'GET'],
    ['/version.json', 'cors', 'GET'],
    ['/assets/secret.webp?signed=private', 'cors', 'GET'],
    ['/', 'navigate', 'POST'],
  ]) {
    let intercepted = false;
    worker.events.get('fetch')({
      request: {url: origin + path, method, mode},
      respondWith() { intercepted = true; },
    });
    assert.equal(intercepted, false, path);
  }
});

test('Activation tells open windows which complete version is ready without navigating them', async () => {
  const worker = harness(() => { throw new Error('Unexpected request'); });
  let finished;
  worker.events.get('activate')({waitUntil(value) { finished = value; }});
  await finished;
  assert.equal(worker.messages.length, 1);
  assert.equal(worker.messages[0].type, 'ERIC_OS_VERSION');
  assert.equal(worker.messages[0].version, version);
  const replies = [];
  worker.events.get('message')({
    data: {type: 'ERIC_OS_VERSION'},
    ports: [{postMessage(value) { replies.push(value); }}],
  });
  assert.equal(replies[0].entry, entry);
});
