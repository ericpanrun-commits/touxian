import test from 'node:test';
import assert from 'node:assert/strict';
import { verifyCollectorOrigin, collectorHome } from '../src/mobile/pairing.js';
import { remainingSeconds, qrImagePath, expiryLabel } from '../src/personal/collectorPairing.js';

const origin = 'http://192.168.1.199:8473';
const status = { enabled: true, pairing_available: true, pairing_id: 'a123456789ab', server_epoch: 'process-a', pairing_expires_in: 300 };
test('phone verifies current LAN and uses a relative clean home', () => {
  assert.equal(verifyCollectorOrigin({ public_base_url: origin, fixture: false }, origin), origin);
  assert.equal(new URL(collectorHome, `${origin}/pair?token=temporary`).href, `${origin}/collect`);
});
for (const bad of ['http://127.0.0.1:8473', 'http://localhost:8473', 'http://[::1]:8473', 'http://8.8.8.8:8473', `${origin}/collect`, `${origin}?token=x`]) {
  test(`public navigation rejects ${bad}`, () => assert.throws(() => verifyCollectorOrigin({ public_base_url: bad }, bad)));
}
test('wrong interface and desktop origins cannot become phone destinations', () => {
  assert.throws(() => verifyCollectorOrigin({ public_base_url: origin }, 'http://192.168.1.200:8473'));
  assert.throws(() => verifyCollectorOrigin({ public_base_url: origin }, 'http://127.0.0.1:5173'));
});
test('countdown expires using elapsed monotonic time, independent of wall-clock timezone', () => {
  assert.equal(remainingSeconds(status, 1000, 2000), 299);
  assert.equal(expiryLabel(299), '4:59');
  assert.equal(remainingSeconds(status, 1000, 300000), 1);
  assert.equal(remainingSeconds(status, 1000, 302000), 0);
});
test('consumed, revoked, stopped or restarted states hide invitation images', () => {
  for (const changes of [{ enabled: false }, { pairing_available: false }, { pairing_id: null }]) {
    assert.equal(qrImagePath({ ...status, ...changes }), null);
    assert.equal(remainingSeconds({ ...status, ...changes }, 0, 0), 0);
  }
});
test('regeneration and process restart change QR cache identity', () => {
  assert.notEqual(qrImagePath(status), qrImagePath({ ...status, pairing_id: 'new-id' }));
  assert.notEqual(qrImagePath(status), qrImagePath({ ...status, server_epoch: 'process-b' }));
  assert.match(qrImagePath(status), /pairing_id=a123456789ab&epoch=process-a$/);
});
