// Browser plugin not available. Real HTTP + SQLite + mock model fixture; never production.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { chromium } from 'playwright';

const base = process.env.ATTENTION_QA_URL || 'http://127.0.0.1:18765';
const output = process.env.ATTENTION_QA_OUTPUT || path.join(os.tmpdir(), `attention-wechat-e2e-${Date.now()}`);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
let page = await context.newPage();
const errors = [];
const checks = [];
const watch = current => {
  current.on('pageerror', error => errors.push(error.message));
  current.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
};
watch(page);
const state = async () => {
  const response = await context.request.get(base + '/__test__/state');
  const value = await response.json();
  assert.equal(value.synthetic_only, true, 'E2E writes require the isolated synthetic fixture');
  return value;
};
const scan = async () => (await context.request.post(base + '/__test__/scan')).json();
const inbox = async () => {
  await page.goto(base + '/?personal_view=wechat-inbox');
  await page.getByRole('heading', { name: '微信待复核', exact: true }).waitFor();
  await page.locator('.wechat-inbox-loading').waitFor({ state: 'detached' });
};
const card = title => page.getByRole('article', { name: `微信候选：${title}`, exact: true });

try {
  const initial = await state();
  assert.equal(initial.events.length, 0);
  assert.equal(initial.provider_calls, 1);
  const afterRepeat = await scan();
  assert.equal(afterRepeat.provider_calls, initial.provider_calls);
  assert.equal(afterRepeat.candidates.length, initial.candidates.length);
  checks.push('no-change-zero-provider-calls', 'repeat-scan-no-duplicate-candidates');
  await page.goto(base + '/?personal_view=today');
  const preview = page.getByRole('region', { name: '微信待复核', exact: true });
  await preview.getByRole('button', { name: '查看全部 4 条待复核通知', exact: true }).waitFor();
  assert.equal(await preview.getByRole('article').count(), 3);
  await preview.getByRole('button', { name: '查看全部 4 条待复核通知', exact: true }).click();
  await page.getByRole('heading', { name: '微信待复核', exact: true }).waitFor();
  await page.locator('.wechat-inbox-loading').waitFor({ state: 'detached' });
  checks.push('homepage-preview-opens-full-inbox');
  assert.equal(await page.getByRole('article').count(), 4);
  assert.equal(await page.locator('vite-error-overlay').count(), 0);
  checks.push('four-candidate-types-visible');

  const task = card('提交实验报告（合成测试）');
  await task.getByRole('button', { name: '查看原始信息', exact: true }).click();
  const evidence = task.getByRole('region', { name: '原始通知与引用依据' });
  await evidence.locator('pre').filter({ hasText: '【合成测试·任务】请在2026年10月24日17:00前' }).waitFor();
  await task.getByRole('heading', { name: 'AI 为什么提出这条候选' }).waitFor();
  assert.ok((await evidence.innerText()).includes('合成班级老师'));
  assert.ok((await evidence.innerText()).includes('合成测试群'));
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: path.join(output, 'wechat-evidence.png'), fullPage: true });
  const download = evidence.getByRole('link', { name: '下载已保存原文件', exact: true }).first();
  assert.equal((await context.request.get(base + await download.getAttribute('href'))).status(), 200);
  const picker = evidence.getByLabel('查看来源位置', { exact: true });
  const originalBlock = await picker.inputValue();
  await picker.selectOption(await picker.locator('option').filter({ hasText: 'OCR' }).first().getAttribute('value'));
  const originalImage = evidence.getByAltText('已保存的原始截图', { exact: true });
  await originalImage.waitFor();
  await originalImage.evaluate(image => image.complete ? Promise.resolve() : new Promise(resolve => image.addEventListener('load', resolve, { once: true })));
  assert.ok(await originalImage.evaluate(image => image.naturalWidth > 0));
  await picker.selectOption(originalBlock);
  await task.getByRole('button', { name: '收起原文', exact: true }).click();
  checks.push('raw-text-sender-time-chat-evidence', 'ai-reason-separate-from-original', 'saved-media-original-download-and-image-preview');

  await task.getByRole('button', { name: '修改', exact: true }).click();
  await page.getByRole('heading', { name: '核对微信通知', exact: true }).waitFor();
  assert.equal(await page.locator('.personal-review-event').count(), 1);
  await page.getByLabel('事项标题', { exact: true }).fill('实验报告：已核对并修改');
  await page.getByLabel('事项类型', { exact: true }).selectOption('deadline');
  await page.getByLabel('摘要', { exact: true }).fill('我已核对原文，提交修改后的实验报告。');
  await page.getByLabel('截止日期 / 时间', { exact: true }).fill('2026-10-24 18:00');
  await page.getByLabel('地点表述', { exact: true }).fill('工程楼202（已核对）');
  await page.getByLabel('行动名称', { exact: true }).fill('提交已核对的实验报告');
  await page.getByLabel('是否与我相关', { exact: true }).selectOption('yes');
  await page.getByLabel('是否参加 / 执行', { exact: true }).selectOption('yes');
  await page.getByLabel('我已核对并确认此日期', { exact: true }).check();
  await page.getByLabel('关联截止时间', { exact: true }).selectOption({ label: '主要截止' });
  await page.getByRole('button', { name: '确认并创建所选事项', exact: true }).click();
  await page.getByRole('heading', { name: '实验报告：已核对并修改', exact: true }).waitFor();
  await page.getByText('提交已核对的实验报告', { exact: true }).first().waitFor();
  const savedState = await state();
  assert.equal(savedState.events.length, 1);
  assert.equal(savedState.tasks, 1);
  assert.equal(savedState.deadlines, 1);
  assert.equal(savedState.events[0].category, 'deadline');
  assert.equal(savedState.events[0].status, 'needs_action');
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: path.join(output, 'wechat-confirmed-formal-item.png'), fullPage: true });
  checks.push('edit-title-type-time-location-action', 'confirm-creates-existing-event-task-deadline');

  await inbox();
  assert.equal(await card('提交实验报告（合成测试）').count(), 0);
  assert.equal(await page.getByRole('article').count(), 3);
  const noticeTitle = '学院资料领取须知（合成测试）';
  await card(noticeTitle).getByRole('button', { name: '忽略', exact: true }).click();
  await card(noticeTitle).waitFor({ state: 'detached' });
  await scan();
  await inbox();
  assert.equal(await card(noticeTitle).count(), 0);
  await page.getByRole('button', { name: '已忽略', exact: true }).click();
  await card(noticeTitle).getByRole('button', { name: '恢复待复核', exact: true }).click();
  await page.getByRole('button', { name: /^待复核/ }).click();
  await card(noticeTitle).waitFor();
  checks.push('confirmed-candidate-disappears', 'ignored-stays-hidden-after-repeat-and-reload', 'ignored-can-be-restored');

  for (const title of ['参加班会（合成测试）', '核对资料提交截止（合成测试）', noticeTitle]) {
    await card(title).getByRole('button', { name: '确认', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '确认并创建正式事项？' });
    await dialog.waitFor();
    await dialog.getByRole('button', { name: '确认并创建事项', exact: true }).click();
    await page.getByRole('heading', { name: title, exact: true }).waitFor();
    if (title === noticeTitle) {
      await page.getByText('已保存', { exact: true }).first().waitFor();
      assert.equal(await page.getByRole('button', { name: '参加并创建任务', exact: true }).count(), 0);
    }
    await inbox();
  }
  assert.equal((await state()).events.length, 4);
  assert.equal(await page.getByRole('article').count(), 0);
  checks.push('direct-confirm-event-deadline-notice', 'partial-batch-remains-until-each-item-is-handled');

  await context.request.post(base + '/__test__/append');
  await card('自动出现的新候选（合成测试）').waitFor({ timeout: 25000 });
  checks.push('new-candidate-auto-appears-without-page-reload');
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: path.join(output, 'wechat-inbox-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: path.join(output, 'wechat-inbox-mobile.png'), fullPage: true });
  checks.push('narrow-no-horizontal-overflow');
  await page.close();
  const beforeClosed = await state();
  await context.request.post(base + '/__test__/append');
  const afterClosed = await state();
  assert.equal(afterClosed.provider_calls, beforeClosed.provider_calls + 1);
  assert.equal(afterClosed.messages, beforeClosed.messages + 1);
  page = await context.newPage(); watch(page);
  await inbox();
  assert.equal(await page.getByRole('article').count(), 2);
  checks.push('collector-continues-with-browser-closed');

  await context.request.post(base + '/__test__/append-failure');
  await page.getByRole('button', { name: '刷新候选', exact: true }).click();
  const failed = page.getByRole('article').filter({ hasText: 'AI 分析未完成' });
  await failed.waitFor();
  await failed.getByRole('button', { name: '查看原始信息', exact: true }).click();
  await failed.locator('pre').filter({ hasText: '即使 AI 输出格式错误，这条原始消息仍然保留' }).waitFor();
  const failureState = await state();
  await scan();
  assert.equal((await state()).provider_calls, failureState.provider_calls);
  checks.push('invalid-json-retains-message-and-evidence', 'failed-analysis-does-not-loop-retry');

  await page.goto(base + '/?personal_view=wechat-settings');
  await page.getByRole('heading', { name: '授权微信群', exact: true }).waitFor();
  const nameInput = page.getByLabel('授权群名称', { exact: true });
  await nameInput.fill('第二个授权测试群');
  await page.getByRole('button', { name: '添加目标群', exact: true }).click();
  await page.getByRole('button', { name: '保存微信设置', exact: true }).click();
  const authorize = page.getByRole('dialog', { name: '授权读取这些微信群？' });
  await authorize.getByRole('button', { name: '保存并授权', exact: true }).click();
  await page.getByText('微信设置已保存，授权范围和采集计划已更新。', { exact: true }).waitFor();
  let settings = await (await context.request.get(base + '/api/personal/wechat/settings')).json();
  assert.ok(settings.wechat.allowed_chats.includes('第二个授权测试群'));
  await page.getByLabel('启用采集：第二个授权测试群', { exact: true }).uncheck();
  await page.getByRole('button', { name: '保存微信设置', exact: true }).click();
  await page.getByText('微信设置已保存，授权范围和采集计划已更新。', { exact: true }).waitFor();
  settings = await (await context.request.get(base + '/api/personal/wechat/settings')).json();
  assert.equal(settings.wechat.priority_chats.find(chat => chat.name === '第二个授权测试群').enabled, false);
  await page.getByRole('button', { name: '删除授权群：第二个授权测试群', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: '删除授权', exact: true }).click();
  await page.getByRole('button', { name: '保存微信设置', exact: true }).click();
  await page.getByText('微信设置已保存，授权范围和采集计划已更新。', { exact: true }).waitFor();
  settings = await (await context.request.get(base + '/api/personal/wechat/settings')).json();
  assert.equal(settings.wechat.allowed_chats.includes('第二个授权测试群'), false);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: path.join(output, 'wechat-groups-mobile.png'), fullPage: true });
  checks.push('group-add-updates-real-allowlist', 'group-disable-persists', 'group-delete-revokes-without-deleting-history');
  assert.deepEqual(errors, []);
  const final = await state();
  assert.equal(final.messages_sent, 0);
  assert.equal(final.unauthorized_message_bodies_read, 0);
  await writeFile(path.join(output, 'wechat-workbench-e2e.json'), JSON.stringify({ checks, errors, final, base, viewports: ['1440x1000', '390x844'], browser: 'Playwright; Browser plugin not available' }, null, 2));
  console.log(JSON.stringify({ passed: checks.length, checks, errors, output }));
} catch (error) {
  if (!page.isClosed()) await page.screenshot({ path: path.join(output, 'wechat-e2e-failure.png'), fullPage: true }).catch(() => {});
  throw error;
} finally { await browser.close(); }
