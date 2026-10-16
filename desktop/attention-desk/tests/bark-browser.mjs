// Real local app + isolated browser-route fixtures; NEVER sends a real phone push.
// Browser plugin not available in the implementation session; use Playwright.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { chromium } from 'playwright';

const base = process.env.ATTENTION_QA_URL || 'http://127.0.0.1:5173';
const output = process.env.ATTENTION_QA_OUTPUT || path.join(os.tmpdir(), 'attention-desk-bark-qa');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.env.ATTENTION_QA_CHANNEL ? { channel: process.env.ATTENTION_QA_CHANNEL } : {}) });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
const page = await context.newPage();
const errors = []; const checks = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
const go = async query => {
  await page.goto(base + '/' + query);
  await page.locator('.personal-content h1').first().waitFor();
  assert.ok((await page.title()).includes('Attention Desk'));
  assert.equal(await page.locator('vite-error-overlay').count(), 0);
};

try {
  // Genuine application routes and real API reads before any route mocking.
  await go(''); checks.push('home');
  await go('?official_feed=latest');
  for (const label of ['与你相关', '学院', '教务', '最新']) {
    await page.getByRole('button', { name: label, exact: true }).click();
    await page.getByText(/共 \d+ 条/).first().waitFor();
    checks.push('official:' + label);
  }
  const article = page.locator('.official-title-link').first();
  if (await article.count()) {
    await article.click(); await page.getByText(/手机通知：/).waitFor(); checks.push('detail-notification-state');
  }
  await go('?official_feed=latest&official_sources=1'); checks.push('sources');
  await go('?personal_view=calendar'); checks.push('calendar');
  await go('?personal_view=reminders'); checks.push('reminders');
  await go('?personal_view=reminder-settings'); checks.push('reminder-settings');
  await page.getByRole('button', { name: 'Bark 手机通知', exact: true }).click();
  await page.getByRole('heading', { name: 'Bark 手机通知', exact: true }).waitFor();
  await page.getByText(/Device Key：/).waitFor();
  const actualConfigured = !(await page.getByText('未配置', { exact: true }).count());
  if (!actualConfigured) assert.ok(await page.getByRole('button', { name: '发送测试通知', exact: true }).isDisabled());
  await page.screenshot({ path: path.join(output, 'bark-actual-desktop.png'), fullPage: true });
  checks.push('real-settings-secret-not-exposed');

  // Frontend states use fixtures that contain no secret and never reach Bark.
  const realSettings = await (await context.request.get(base + '/api/personal/notifications/bark/settings')).json();
  const realWorker = await (await context.request.get(base + '/api/personal/system/status')).json();
  assert.equal(realWorker.automatic_bark, false);
  assert.equal(realWorker.automatic_sync, false);
  checks.push('local-production-authority-disabled');
  let settings = { ...realSettings, configured: true, device_key_configured: true, environment_enabled: true,
    preference_enabled: true, enabled: true, status: 'configured', version: 1 };
  let failTest = false; let conflict = false; let slow = false; let testCalls = 0; let writes = 0;
  const history = { items: [{ id: 'browser-fixture', title: '公开学校通知 · 浏览器测试', channel: 'bark', status: 'sent',
    created_at: '2026-10-15T04:00:00+00:00', sent_at: '2026-10-15T04:00:00+00:00', attempt_count: 1 }], total: 1, limit: 20, offset: 0 };
  await page.route('**/api/personal/notifications/bark/**', async route => {
    const request = route.request(); const url = new URL(request.url());
    if (slow) await new Promise(resolve => setTimeout(resolve, 500));
    let data;
    if (url.pathname.endsWith('/history')) data = history;
    else if (url.pathname.endsWith('/test')) {
      testCalls += 1;
      data = failTest ? { accepted: false, result: 'unauthorized', message: 'Bark unauthorized / invalid key，请核对 Device Key。' }
        : { accepted: true, result: 'sent', message: 'Bark 已接受通知，请在 iPhone 查看。' };
      settings = { ...settings, status: failTest ? 'failed' : 'healthy', last_error: failTest ? 'unauthorized' : null };
    } else if (request.method() === 'PUT') {
      writes += 1;
      if (conflict) return route.fulfill({ status: 409, json: { detail: { message: '手机通知设置已更新，请重新读取后核对。' } } });
      const payload = request.postDataJSON();
      settings = { ...settings, enabled: payload.enabled, preference_enabled: payload.enabled, policy: payload.policy,
        status: payload.enabled ? 'healthy' : 'disabled', version: settings.version + 1 }; data = settings;
    } else data = settings;
    await route.fulfill({ json: data });
  });
  await go('?personal_view=bark-settings');
  await page.getByText('已配置 · 等待首次发送', { exact: true }).waitFor();
  await page.getByText('公开学校通知 · 浏览器测试').waitFor();
  await page.getByRole('button', { name: '发送测试通知', exact: true }).click();
  await page.getByText('连接正常', { exact: true }).waitFor(); assert.equal(testCalls, 1); checks.push('test-success-history');
  failTest = true;
  await page.getByRole('button', { name: '发送测试通知', exact: true }).click();
  await page.getByText('连接失败', { exact: true }).waitFor();
  assert.ok((await page.getByRole('alert').innerText()).includes('invalid key')); checks.push('test-failure');
  await page.getByLabel('最低相关度（0—100）').fill('999');
  await page.getByRole('button', { name: '保存手机通知设置', exact: true }).click();
  await page.getByText('请填写 0—100 的相关度。').waitFor(); assert.equal(writes, 0);
  assert.equal(await page.getByLabel('最低相关度（0—100）').evaluate(element => document.activeElement === element), true);
  await page.getByLabel('最低相关度（0—100）').fill('85');
  conflict = true;
  await page.getByRole('button', { name: '保存手机通知设置', exact: true }).click();
  await page.getByText(/手机通知设置已更新/).waitFor();
  assert.equal(await page.getByLabel('最低相关度（0—100）').inputValue(), '85'); checks.push('validation-conflict-retains-input');
  conflict = false;
  await page.getByLabel('启用 Bark 自动推送', { exact: true }).uncheck();
  await page.getByRole('button', { name: '保存手机通知设置', exact: true }).click();
  await page.getByText('已关闭', { exact: true }).waitFor();
  assert.ok(await page.getByRole('button', { name: '发送测试通知', exact: true }).isDisabled()); checks.push('disable-save');
  await page.getByLabel('静默开始', { exact: true }).fill('22:30');
  await page.getByRole('button', { name: '重新读取设置', exact: true }).click();
  await page.getByRole('dialog').waitFor(); await page.keyboard.press('Escape');
  assert.equal(await page.getByLabel('静默开始', { exact: true }).inputValue(), '22:30'); checks.push('dirty-escape-focus');
  await page.getByRole('button', { name: '重新读取设置', exact: true }).click();
  await page.getByRole('button', { name: '读取已保存设置', exact: true }).click();
  await page.getByText('已读取最新手机通知设置。').waitFor();
  await page.getByLabel('常规推送最低重要程度').focus(); await page.keyboard.press('ArrowDown'); await page.keyboard.press('Escape');
  await page.screenshot({ path: path.join(output, 'bark-disabled-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(output, 'bark-mobile.png'), fullPage: true });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)); checks.push('narrow-keyboard-reduced-motion');
  settings = { ...settings, configured: false, device_key_configured: false, environment_enabled: false, status: 'unconfigured' };
  history.items = []; history.total = 0; slow = true;
  await page.goto(base + '/?personal_view=bark-settings');
  await page.getByText('正在读取手机通知设置…').waitFor();
  await page.getByText('未配置', { exact: true }).waitFor();
  await page.getByText(/暂无通知记录/).waitFor(); checks.push('loading-empty-unconfigured');
  let worker = { ...realWorker, runtime: 'cloud', environment: 'production', cloud_deployed: true, database_persistent: true,
    instance: 'browser-fixture', status: 'online', scheduler: 'healthy', automatic_sync: true, automatic_bark: true,
    bootstrap: false, dry_run: false, backup_status: 'available' };
  let workerOffline = false;
  await page.route('**/api/personal/system/status', route => workerOffline ? route.abort('connectionfailed') : route.fulfill({ json: worker }));
  await go('?personal_view=bark-settings');
  await page.getByText('云端后台 · 运行中', { exact: true }).waitFor();
  await page.screenshot({ path: path.join(output, 'worker-online.png'), fullPage: true });
  checks.push('worker-production-persistent');
  worker = { ...worker, status: 'degraded', scheduler: 'stale', backup_status: 'failed' };
  await page.getByRole('button', { name: '刷新运行状态', exact: true }).click();
  await page.getByText('云端后台 · 状态异常', { exact: true }).waitFor();
  await page.getByText(/请检查后台运行日志和备份/).waitFor(); checks.push('worker-stale-backup-failure');
  worker = { ...worker, status: 'bootstrapping', bootstrap: true, dry_run: true, scheduler: 'disabled', automatic_bark: false };
  await page.getByRole('button', { name: '刷新运行状态', exact: true }).click();
  await page.getByText('云端后台 · 准备中', { exact: true }).waitFor();
  await page.getByText(/正在安全验证模式/).waitFor(); checks.push('worker-bootstrap-no-false-online');
  workerOffline = true;
  await page.getByRole('button', { name: '刷新运行状态', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: '下方保留上次读取结果' }).waitFor(); checks.push('worker-offline-retry');
  await page.unroute('**/api/personal/notifications/bark/**');
  await page.route('**/api/personal/notifications/bark/**', route => route.abort('connectionfailed'));
  await page.goto(base + '/?personal_view=bark-settings');
  await page.getByRole('alert').first().waitFor(); checks.push('offline-recovery');
  // Aborted requests deliberately cause network console entries; all earlier app errors must be zero.
  const expectedErrors = errors.filter(value => value.includes('409 (Conflict)') || value.includes('net::ERR_CONNECTION_FAILED'));
  const appErrors = errors.filter(value => !expectedErrors.includes(value));
  assert.deepEqual(appErrors, []);
  await writeFile(path.join(output, 'browser-results.json'), JSON.stringify({ base, checks, relevantErrors: appErrors,
    expectedErrors, viewports: ['1440x1000', '390x844'], browser: 'Playwright Chromium', testCalls, writes }, null, 2));
  console.log(JSON.stringify({ passed: checks.length, checks, screenshots: output, relevantErrors: appErrors }));
} finally { await browser.close(); }
