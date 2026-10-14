import test from 'node:test';
import assert from 'node:assert/strict';
import { officialLink, officialRoute, officialTime, OFFICIAL_VIEWS } from '../src/personal/officialHelpers.js';

test('official navigation accepts verified university web links only', () => {
  assert.equal(officialLink('https://jxqc.zqu.edu.cn/info/1/2.htm'), 'https://jxqc.zqu.edu.cn/info/1/2.htm');
  for (const value of ['javascript:alert(1)', 'file:///C:/secret', 'https://jxqc.zqu.edu.cn.evil.test/', 'https://user@jxqc.zqu.edu.cn/', 'https://127.0.0.1/']) assert.equal(officialLink(value), undefined);
});
test('feed route preserves valid filter and page and rejects malformed state', () => {
  assert.deepEqual(officialRoute('?official_feed=college&official_offset=30'), { view: 'college', offset: 30, item: '', sources: false });
  assert.equal(officialRoute('?official_feed=evil&official_offset=-1&official_item=<script>').view, 'latest');
  assert.equal(officialRoute('?official_offset=Infinity').offset, 0);
  assert.equal(officialRoute('?official_sources=1').sources, true);
});
test('publication dates retain date precision and missing dates stay explicit', () => {
  assert.equal(officialTime('2026-10-14'), '2026-10-14');
  assert.equal(officialTime(null), '尚无记录');
  assert.equal(officialTime('bad'), '尚无记录');
  assert.equal(OFFICIAL_VIEWS.length, 10);
});
