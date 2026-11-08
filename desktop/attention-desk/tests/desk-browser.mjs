// Browser plugin not available. Playwright runs against an isolated, synthetic HTTP fixture.
// No real reviews, phone notifications, WeChat writes or model requests are performed here.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { chromium } from 'playwright';

const base = process.env.ATTENTION_QA_URL || 'http://127.0.0.1:18765';
const output = process.env.ATTENTION_QA_OUTPUT || path.join(os.tmpdir(), 'attention-desk-daily-qa');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
const page = await context.newPage();
const errors = [], checks = [];
page.on('pageerror', error => errors.push(error.message));
const state = async () => {
  const response = await context.request.get(base + '/__test__/state');
  const result = await response.json();
  assert.equal(result.synthetic_only, true, 'Review mutations require the isolated fixture');
  return result;
};
const inbox = async () => {
  await page.goto(base + '/?personal_view=wechat-inbox');
  await page.getByRole('heading', { name: '待确认', exact: true }).waitFor();
  await page.locator('.wechat-inbox-loading').waitFor({ state: 'detached' });
  await page.locator('.wechat-candidate').first().waitFor();
};
const card = title => page.getByRole('article', { name: `微信候选：${title}`, exact: true });
const screenshot = async name => page.screenshot({ path: path.join(output, name + '.png'), fullPage: !(await page.locator('.desk-sheet').isVisible()) });
const noOverflow = async () => assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
try {
  const initial = await state();
  assert.equal(initial.events.length, 0);
  assert.equal(initial.candidates.filter(item => item.status === 'needs_review').length, 3);
  await page.goto(base + '/?personal_view=today');
  await page.getByRole('button', { name: '查看全部 3 条待确认通知' }).waitFor();
  assert.equal(await page.locator('.wechat-candidate').count(), 3);
  assert.equal(await page.locator('vite-error-overlay').count(), 0);
  assert.ok(!/AI 置信度|DeepSeek 调用/.test(await page.locator('.personal-content').innerText()));
  await screenshot('desk-fixture-home'); checks.push('home-grouped-three-candidate-preview');
  await page.getByRole('button', { name: '学院资料领取须知（合成测试）' }).waitFor();
  assert.equal(await card('学院资料领取须知（合成测试）').count(), 0);
  await page.getByRole('button', { name: '查看全部 3 条待确认通知' }).click();
  await page.getByRole('heading', { name: '待确认', exact: true }).waitFor();
  await card('提交实验报告（合成测试）').waitFor();
  assert.equal(await page.locator('.wechat-candidate').count(), 3);

  const title = '提交实验报告（合成测试）';
  await card(title).getByRole('button', { name: title, exact: true }).click();
  await page.getByRole('dialog', { name: '通知详情' }).waitFor();
  assert.equal(await page.locator('.desk-sheet .personal-original-text').isVisible(), false);
  await page.getByText('原始微信与 AI 分析', { exact: true }).click();
  await page.locator('.desk-sheet .personal-original-text').waitFor();
  assert.ok((await page.locator('.desk-sheet').innerText()).includes('合成班级老师'));
  await screenshot('desk-source-sheet');
  await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('button', { name: title, exact: true }).evaluate(el => el === document.activeElement), true);
  checks.push('detail-evidence-disclosure-escape-focus-return');

  await card(title).getByRole('button', { name: '编辑', exact: true }).click();
  let sheet = page.getByRole('dialog', { name: '编辑通知' });
  await sheet.getByLabel('标题', { exact: true }).fill('提交实验报告 · 已核对');
  await sheet.getByLabel('类型', { exact: true }).focus();
  await page.keyboard.press('ArrowDown'); await page.keyboard.press('ArrowUp'); await page.keyboard.press('Escape');
  // Escape with edits must open the app-owned discard guard and preserve the draft.
  const discard = page.getByRole('dialog', { name: '放弃未保存的修改？' });
  if (await discard.isVisible()) await discard.getByRole('button', { name: '继续当前操作' }).click();
  let failed = false;
  await page.route('**/api/personal/raw/*/draft', async route => { failed = true; await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: { message: '合成保存失败，请重试。' } }) }); });
  await sheet.getByRole('button', { name: '保存修改', exact: true }).click();
  await sheet.getByRole('alert').filter({ hasText: '合成保存失败' }).waitFor();
  assert.equal(await sheet.getByLabel('标题', { exact: true }).inputValue(), '提交实验报告 · 已核对');
  assert.ok(failed); await screenshot('desk-save-recovery');
  await page.unroute('**/api/personal/raw/*/draft');
  await sheet.getByRole('button', { name: '保存修改', exact: true }).click();
  await sheet.waitFor({ state: 'detached' });
  await card('提交实验报告 · 已核对').waitFor();
  assert.equal((await state()).events.length, 0);
  await page.reload(); await card('提交实验报告 · 已核对').waitFor();
  checks.push('native-select-keyboard-dirty-guard-draft-save-error-recovery-persistence');

  await card('提交实验报告 · 已核对').getByRole('button', { name: '确认', exact: true }).click();
  sheet = page.getByRole('dialog', { name: '确认通知' });
  await sheet.getByRole('button', { name: '确认并保存事项', exact: true }).waitFor();
  await screenshot('desk-confirm-sheet');
  await sheet.getByRole('button', { name: '确认并保存事项', exact: true }).dblclick();
  await page.getByRole('heading', { name: '提交实验报告 · 已核对', exact: true, level: 1 }).waitFor();
  let saved = await state();
  assert.equal(saved.events.length, 1);
  assert.equal(saved.candidates.filter(item => item.status === 'needs_review').length, 2);
  assert.equal(await page.locator('.personal-original-text').isVisible(), false);
  await screenshot('desk-formal-item');
  await page.getByRole('button', { name: '编辑事项', exact: true }).click();
  await page.getByRole('dialog', { name: '编辑事项' }).waitFor();
  await page.getByRole('dialog').getByLabel('事项标题', { exact: true }).fill('实验报告 · 准备提交');
  await page.getByRole('dialog').getByRole('button', { name: '保存事项内容' }).click();
  await page.getByRole('heading', { name: '实验报告 · 准备提交', exact: true }).waitFor();
  checks.push('two-click-explicit-confirm-single-candidate-idempotent-formal-editor');

  await inbox();
  const deadline = '核对资料提交截止（合成测试）';
  await card(deadline).getByRole('button', { name: '忽略', exact: true }).click();
  await card(deadline).waitFor({ state: 'detached' });
  assert.equal((await state()).candidates.filter(item => item.status === 'needs_review').length, 1);
  await page.getByRole('button', { name: '撤销忽略' }).click();
  await card(deadline).waitFor();
  assert.equal((await state()).candidates.filter(item => item.status === 'needs_review').length, 2);
  checks.push('reversible-ignore-no-sibling-loss');

  await card(deadline).getByRole('button', { name: '确认', exact: true }).click();
  sheet = page.getByRole('dialog', { name: '确认通知' });
  await sheet.getByRole('button', { name: '添加日期', exact: true }).click();
  await sheet.getByLabel('截止时间', { exact: true }).fill('11.8');
  await sheet.getByLabel('相关性确认', { exact: true }).selectOption('unclear');
  await sheet.getByRole('button', { name: '确认并保存事项', exact: true }).click();
  await sheet.getByText('请确认与你相关，再选择执行行动。', { exact: true }).waitFor();
  assert.equal(await sheet.getByLabel('相关性确认', { exact: true }).evaluate(el => el === document.activeElement), true);
  await sheet.getByLabel('相关性确认', { exact: true }).selectOption('yes');
  await sheet.getByRole('button', { name: '确认并保存事项', exact: true }).click();
  await sheet.getByText('请填写包含年份的有效日期，或取消保留。', { exact: true }).waitFor();
  assert.equal((await state()).events.length, 1);
  await sheet.getByRole('button', { name: '关闭详情', exact: true }).click();
  await page.getByRole('dialog', { name: '放弃未保存的修改？' }).getByRole('button', { name: '放弃修改', exact: true }).click();
  checks.push('uncertain-relevance-and-missing-year-block-confirm-with-field-focus');

  assert.equal(await card('学院资料领取须知（合成测试）').count(), 0);
  assert.equal((await state()).events.length, 1);
  checks.push('fyi-excluded-from-review-without-fabricated-action-or-confirmation');

  await page.getByRole('button', { name: '深色', exact: true }).click();
  assert.equal(await page.locator('html').getAttribute('data-desk-theme'), 'dark');
  await page.waitForFunction(() => getComputedStyle(document.querySelector('.desk-shell')).backgroundColor === 'rgb(20, 20, 22)');
  assert.equal(await page.locator('.personal-section h1').evaluate(el => getComputedStyle(el).color), 'rgb(245, 245, 247)');
  await screenshot('desk-dark-inbox');
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow(); await screenshot('desk-mobile-inbox');
  await card(deadline).getByRole('button', { name: '编辑', exact: true }).click();
  await page.getByRole('dialog', { name: '编辑通知' }).waitFor();
  await noOverflow(); await screenshot('desk-mobile-edit');
  await page.keyboard.press('Escape');
  await page.getByRole('dialog').waitFor({ state: 'detached' });
  await page.getByRole('button', { name: '浅色', exact: true }).click();
  checks.push('dark-mode-mobile-bottom-navigation-sheet-no-overflow');

  // Frontend Bark states use a mock only. Real provider acceptance is a separate explicit test.
  let settings = { enabled: false, server: 'https://api.day.app', device_key_configured: true, key_source: 'environment', version: 1 };
  let settingsBody;
  await page.route('**/api/personal/notifications/wechat-bark/**', async route => {
    const request = route.request();
    if (request.url().endsWith('/test')) return route.fulfill({ json: { accepted: true, message: 'Bark 已接受合成测试通知。' } });
    if (request.method() === 'PUT') { settingsBody = request.postDataJSON(); settings = { ...settings, enabled: settingsBody.enabled, version: 2 }; }
    await route.fulfill({ json: settings });
  });
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.getByRole('heading', { name: '手机通知', exact: true }).waitFor();
  assert.equal(await page.getByLabel('Device Key', { exact: true }).getAttribute('type'), 'password');
  await page.getByLabel('Device Key', { exact: true }).fill('synthetic-device-secret');
  await page.getByRole('button', { name: '显示 Device Key', exact: true }).click();
  assert.equal(await page.getByLabel('Device Key', { exact: true }).getAttribute('type'), 'text');
  await page.getByRole('button', { name: '隐藏 Device Key', exact: true }).click();
  await page.getByRole('button', { name: '保存通知设置', exact: true }).click();
  await page.getByText('手机通知设置已保存。', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('Device Key', { exact: true }).inputValue(), '');
  assert.equal(settingsBody.device_key, 'synthetic-device-secret');
  assert.ok(!(await page.evaluate(() => JSON.stringify(localStorage))).includes('synthetic-device-secret'));
  await page.getByRole('button', { name: '发送测试通知', exact: true }).first().click();
  await page.getByText('Bark 已接受合成测试通知。', { exact: true }).waitFor();
  await noOverflow(); await screenshot('desk-mobile-bark');
  checks.push('bark-masked-key-show-hide-clear-after-save-no-client-storage-test-feedback');

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.route('**/api/personal/wechat/candidates?**', route => route.fulfill({ status: 503, json: { detail: { message: '合成读取失败，稍后重试。' } } }));
  await page.getByRole('button', { name: '待确认', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: '合成读取失败' }).waitFor();
  assert.ok(await page.getByRole('button', { name: '重新读取候选' }).isVisible());
  await page.unroute('**/api/personal/wechat/candidates?**');
  await page.getByRole('button', { name: '重新读取候选' }).click();
  await card(deadline).waitFor();
  checks.push('read-failure-retry-navigation-usable');
  assert.deepEqual(errors, []);
  const final = await state();
  assert.equal(final.events.length, 1);
  assert.equal(final.provider_calls, initial.provider_calls);
  const report = { passed: true, base, viewports: ['1440×1000','390×844'], checks, errors, modelCallsUnchanged: true, formalEvents: final.events.length };
  await writeFile(path.join(output, 'desk-browser-results.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally { await context.close(); await browser.close(); }
