import test from 'node:test';
import assert from 'node:assert/strict';
import { BARK_STATUS, DELIVERY_STATUS, barkReason, validQuietTime } from '../src/personal/barkHelpers.js';

test('Bark settings distinguish missing, configured, healthy, failed and disabled', () => {
  assert.equal(BARK_STATUS.unconfigured, '未配置');
  assert.equal(BARK_STATUS.healthy, '连接正常');
  assert.equal(BARK_STATUS.failed, '连接失败');
  assert.equal(BARK_STATUS.disabled, '已关闭');
  assert.match(BARK_STATUS.configured, /等待首次发送/);
});
test('notification lifecycle distinguishes queued, sent, suppressed and ambiguous failure', () => {
  assert.equal(DELIVERY_STATUS.sent, '已发送');
  assert.equal(DELIVERY_STATUS.pending, '等待发送');
  assert.equal(DELIVERY_STATUS.suppressed, '被过滤');
  assert.match(barkReason('historical'), /历史/);
  assert.match(barkReason('delivery_unknown'), /停止重发/);
});
test('provider error display never echoes an unrecognized response or credential', () => {
  assert.equal(barkReason('https://api.day.app/private-key'), '请查看后端通知设置');
  assert.match(barkReason('unauthorized'), /Device Key 无效/);
});
test('quiet hours require a real 24-hour clock and accept the default overnight range', () => {
  for (const value of ['23:30', '07:30', '00:00', '23:59']) assert.ok(validQuietTime(value));
  for (const value of ['24:00', '7:30', '23:60', '', ' 23:30']) assert.equal(validQuietTime(value), false);
});
