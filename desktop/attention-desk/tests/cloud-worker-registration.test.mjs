import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';

const source = (await readFile(new URL('../src/cloud/register-worker.js', import.meta.url), 'utf8'))
  .replace(/^export /gm, '');

function registrationFixture(hasExisting) {
  const events = new Map();
  const intervals = [];
  const counters = {register: 0, update: 0};
  let failRegister = true;
  let existing = hasExisting;
  const build = 'eric-os-shell-current';
  const worker = {
    postMessage(message, ports) {
      ports[0].postMessage({type: 'ERIC_OS_VERSION', version: build, entry: '/assets/index.js'});
    },
  };
  const registration = {
    active: worker,
    installing: null,
    addEventListener() {},
    async update() { counters.update++; },
  };
  const serviceWorker = {
    controller: worker,
    ready: Promise.resolve(registration),
    addEventListener() {},
    async getRegistration() { return existing ? registration : undefined; },
    async register() {
      counters.register++;
      if (failRegister) throw new TypeError('Network unavailable during registration');
      existing = true;
      return registration;
    },
  };
  const context = {
    navigator: {serviceWorker, onLine: true},
    document: {
      readyState: 'complete', visibilityState: 'visible', addEventListener() {},
      querySelector(selector) {
        return selector.startsWith('meta') ? {content: build} : {src: 'https://fixture.example/assets/index.js'};
      },
    },
    window: {addEventListener(name, callback) { events.set(name, callback); }},
    location: {origin: 'https://fixture.example'},
    MessageChannel, AbortController, Response, URL, setTimeout, clearTimeout,
    setInterval(callback, period) { intervals.push({callback, period}); },
    async fetch() { return Response.json({version: build}); },
  };
  runInNewContext(source + '\nglobalThis.startRegistration=registerWorker;', context);
  return {
    context, counters, events, intervals,
    allowRegistration() { failRegister = false; },
  };
}

async function waitUntil(predicate) {
  const deadline = Date.now() + 4000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  assert.fail('Worker registration did not recover');
}

test('An installed registration keeps checking updates when startup registration fetch fails', async () => {
  const fixture = registrationFixture(true);
  fixture.context.startRegistration();
  await waitUntil(() => fixture.counters.update === 1 && fixture.intervals.length === 1);
  assert.equal(fixture.counters.register, 1);
  fixture.events.get('online')();
  await waitUntil(() => fixture.counters.update === 2);
  assert.equal(fixture.intervals[0].period, 5 * 60_000);
});

test('A failed first registration is retried on reconnect instead of requiring a reload', async () => {
  const fixture = registrationFixture(false);
  fixture.context.startRegistration();
  await waitUntil(() => fixture.counters.register === 1);
  assert.equal(fixture.intervals.length, 0);
  fixture.allowRegistration();
  fixture.events.get('online')();
  await waitUntil(() => fixture.counters.register === 2 && fixture.counters.update === 1);
  assert.equal(fixture.intervals.length, 1);
});
