// Browser plugin not available. Headless Playwright uses synthetic API fixtures.
// This script never takes screenshots of WeChat or contacts a model provider.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { chromium } from 'playwright';

const base = process.env.ATTENTION_QA_URL || 'http://127.0.0.1:18765';
const output = process.env.ATTENTION_QA_OUTPUT || path.join(os.tmpdir(), 'attention-wechat-states-' + Date.now());
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, reducedMotion: 'reduce' });
const page = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => { if (message.type() === 'error' && !/503|409/.test(message.text())) errors.push(message.text()); });
let saveFailure = true;
let conflict = false;
let saveCount = 0;
let statusFailure = false;
let releaseStatus;
const statusGate = new Promise(resolve => { releaseStatus = resolve; });
const checks = [];
let document;
const status = { status: 'quiet', capability: 'not_tested', go_level: 'NO_GO', runtime_verified: false, capability_reason: '本机微信读取尚未通过实测，自动读取保持阻断。', wechat_available: null, last_scan: [], next_scan: null, quiet: true, chats: [], stats: { messages_new: 0, messages_filtered: 0, llm_calls: 0, important_items_found: 0, pending_candidates: 0 } };
try {
  // Read only from an isolated temporary backend. Then fixture every WeChat response.
  assert.equal((await (await context.request.get(base + '/__test__/state')).json()).synthetic_only, true);
  const initial = await context.request.get(base + '/api/personal/wechat/settings');
  assert.equal(initial.status(), 200);
  document = await initial.json();
  document.wechat.enabled = false;
  await page.route('**/api/personal/wechat/**', async route => {
    const request = route.request(); const url = new URL(request.url());
    const finish = (body, code = 200) => route.fulfill({ status: code, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname.endsWith('/status')) { await statusGate; return statusFailure ? finish({ detail: { code: 'unavailable', message: '模拟离线。' } }, 503) : finish(status); }
    if (url.pathname.endsWith('/scans')) return finish({ items: [], total: 0 });
    if (url.pathname.endsWith('/candidates')) return finish({ items: [], total: 0 });
    if (url.pathname.endsWith('/user-context')) {
      if (request.method() === 'PUT') return finish({ ...request.postDataJSON(), profile_version: 2 });
      return finish({ profile_version: 1, school: '', college: '', major: '', entry_year: null, class_name: '', role_assertions: {}, courses: [], interests: [], projects: [], wechat_self_aliases: [], notes: '', confirmed_fields: [] });
    }
    if (url.pathname.endsWith('/settings')) {
      if (request.method() === 'PUT') {
        saveCount++;
        if (saveFailure) { saveFailure = false; return finish({ detail: { code: 'storage_error', message: '模拟保存失败，输入已保留。' } }, 503); }
        if (conflict) { conflict = false; return finish({ detail: { code: 'conflict', message: '设置已在另一页面更新，请重新读取。' } }, 409); }
        document = { ...request.postDataJSON(), revision: document.revision + 1 }; return finish(document);
      }
      return finish(document);
    }
    throw new Error('Unexpected WeChat request: ' + url.pathname);
  });
  await page.goto(base + '/?personal_view=wechat-settings');
  await page.getByRole('heading', { name: '微信采集', exact: true }).waitFor();
  await page.getByText('正在读取微信采集状态…', { exact: true }).waitFor();
  releaseStatus();
  await page.getByText('夜间暂停', { exact: true }).waitFor();
  checks.push('loading-and-quiet-states');
  assert.ok((await page.title()).includes('微信采集'));
  assert.equal(await page.locator('vite-error-overlay').count(), 0);
  assert.equal(await page.getByRole('button', { name: '立即扫描', exact: true }).isDisabled(), true);
  assert.equal(await page.getByLabel('开启微信自动采集').isDisabled(), true);
  await page.getByRole('button', { name: '添加目标群', exact: true }).click();
  const groupInput = page.getByLabel('授权群名称', { exact: true });
  assert.equal(await groupInput.getAttribute('aria-invalid'), 'true');
  assert.equal(await groupInput.evaluate(node => node === document.activeElement), true);
  await groupInput.fill('合成通知测试群');
  await groupInput.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true, keyCode: 229 });
  assert.equal(saveCount, 0);
  await page.getByRole('button', { name: '添加目标群', exact: true }).click();
  await page.getByRole('button', { name: '保存微信设置', exact: true }).click();
  let authorization = page.getByRole('dialog', { name: '授权读取这些微信群？' });
  await authorization.waitFor();
  assert.equal(await authorization.getByRole('button', { name: '继续当前操作' }).evaluate(node => node === document.activeElement), true);
  await page.keyboard.press('Escape');
  await authorization.waitFor({ state: 'detached' });
  assert.equal(saveCount, 0);
  checks.push('first-invalid-focus', 'ime-enter-no-submit', 'authorization-dialog-safe-focus-and-escape');
  await page.getByRole('button', { name: '保存微信设置', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: '保存并授权', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: '模拟保存失败' }).waitFor();
  assert.equal(await page.getByLabel('启用采集：合成通知测试群', { exact: true }).isChecked(), true);
  await page.getByText('AI 调用上限与采集计划', { exact: true }).click();
  await page.getByLabel('每日 AI 请求上限', { exact: true }).fill('0');
  await page.getByText('AI 调用上限与采集计划', { exact: true }).click();
  await page.getByRole('button', { name: '保存微信设置', exact: true }).click();
  assert.equal(await page.getByLabel('每日 AI 请求上限', { exact: true }).evaluate(node => node === document.activeElement), true);
  await page.getByLabel('每日 AI 请求上限', { exact: true }).fill('50');
  await page.getByRole('button', { name: '保存微信设置', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: '保存并授权', exact: true }).click();
  await page.getByText('微信设置已保存，授权范围和采集计划已更新。', { exact: true }).waitFor();
  checks.push('failed-save-retains-input', 'closed-details-validation-focus', 'explicit-save-retry');
  await page.getByLabel('启用采集：合成通知测试群', { exact: true }).uncheck();
  conflict = true;
  await page.getByRole('button', { name: '保存微信设置', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: '设置已在另一页面更新' }).waitFor();
  assert.equal(await page.getByLabel('启用采集：合成通知测试群', { exact: true }).isChecked(), false);
  await page.getByRole('button', { name: '重新读取已保存设置', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: '重新读取已保存设置', exact: true }).click();
  await page.getByRole('dialog').waitFor({ state: 'detached' });
  assert.equal(await page.getByLabel('启用采集：合成通知测试群', { exact: true }).isChecked(), true);
  checks.push('stale-save-preserves-input', 'explicit-discard-and-reload');
  statusFailure = true;
  await page.getByRole('button', { name: '刷新状态', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: '刷新失败' }).waitFor();
  await page.getByText('夜间暂停', { exact: true }).waitFor();
  statusFailure = false;
  await page.getByRole('button', { name: '重试', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: '刷新失败' }).waitFor({ state: 'detached' });
  checks.push('offline-retains-last-status-and-recovers');
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: path.join(output, 'wechat-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: path.join(output, 'wechat-mobile.png'), fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
  await page.getByRole('button', { name: '用户画像', exact: true }).click();
  await page.getByRole('heading', { name: '用户画像', exact: true }).waitFor();
  checks.push('sibling-settings-navigation');
  await page.getByRole('button', { name: '添加角色', exact: true }).click();
  await page.getByLabel('角色名称', { exact: true }).fill('班长');
  await page.getByLabel('是否具有这个角色', { exact: true }).selectOption('false');
  await page.getByLabel('是否具有这个角色', { exact: true }).focus();
  await page.keyboard.press('Alt+ArrowDown');
  await page.keyboard.press('Escape');
  checks.push('native-select-keyboard-popup');
  await page.getByRole('button', { name: '保存用户画像', exact: true }).click();
  await page.getByText('画像已保存。历史消息不会自动重新付费分析。', { exact: true }).waitFor();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: path.join(output, 'roles-mobile.png'), fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
  assert.deepEqual(errors, []);
  await page.goto(base + '/?personal_view=wechat-inbox');
  await page.getByRole('heading', { name: '待复核已清空', exact: true }).waitFor();
  await page.getByRole('button', { name: '已忽略', exact: true }).click();
  await page.getByRole('heading', { name: '没有已忽略的通知', exact: true }).waitFor();
  checks.push('quiet-unverified-disabled-scan', 'role-confirmed-false', 'desktop-and-mobile-no-overflow', 'empty-and-no-results', 'no-console-errors');
  assert.deepEqual(errors, []);
  await writeFile(path.join(output, 'wechat-states-result.json'), JSON.stringify({ passed: checks.length, checks, errors }, null, 2));
  console.log(JSON.stringify({ passed: checks.length, checks, errors, output }));
} finally { await browser.close(); }
