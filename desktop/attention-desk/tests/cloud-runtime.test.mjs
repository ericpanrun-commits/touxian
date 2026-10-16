import test from 'node:test';
import assert from 'node:assert/strict';
import { personalApiUrl } from '../src/personal/apiBase.js';

test('local personal features retain their existing API and worker routes are configurable', () => {
  assert.equal(personalApiUrl('/official/feed'), '/api/personal/official/feed');
  assert.equal(personalApiUrl('/official/feed', 'https://worker.example.test'), 'https://worker.example.test/api/personal/official/feed');
  assert.equal(personalApiUrl('/notifications/bark/settings', 'https://worker.example.test/prefix'), 'https://worker.example.test/prefix/api/personal/notifications/bark/settings');
  assert.equal(personalApiUrl('/system/status', 'https://worker.example.test/'), 'https://worker.example.test/api/personal/system/status');
  for (const path of ['/events', '/reminders/settings', '/planning/calendar', '/collector/status']) assert.equal(personalApiUrl(path, 'https://worker.example.test'), '/api/personal' + path);
});
test('configured worker API rejects credentials, insecure URLs and secret query parameters', () => {
  for (const base of ['http://worker.example.test', 'javascript:alert(1)', 'https://user:password@worker.example.test', 'https://worker.example.test/?token=secret', 'https://worker.example.test/#secret']) assert.throws(() => personalApiUrl('/official/feed', base));
});
