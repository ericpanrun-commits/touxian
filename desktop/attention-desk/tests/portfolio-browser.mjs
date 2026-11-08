// Browser plugin not available. Real production build + Windows-local API via Playwright.
// All CAD/PDF/image fixtures are synthetic, outside Git. No cloud or model requests.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readdir, readFile, writeFile, rename, unlink, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { deflateSync } from 'node:zlib';
import { chromium } from 'playwright';
import { expect } from 'playwright/test';
import { pdfFixture } from './cloud-pdf-fixture.mjs';

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const directory = await mkdtemp(join(process.env.ERIC_PORTFOLIO_TEST_PARENT || tmpdir(), 'eric-portfolio-'));
const output = process.env.ERIC_PORTFOLIO_EVIDENCE || join(directory, 'evidence');
await mkdir(output, { recursive: true });
await writeFile(join(directory, '.synthetic-portfolio-test'), 'Synthetic fixtures only.');
const root = join(directory, 'Engineering-Portfolio');
await mkdir(root);
const port = process.env.ERIC_PORTFOLIO_TEST_PORT || '18786';
const base = `http://127.0.0.1:${port}`;
const checks = [], errors = [], network = [];
let server;
function start() {
  server = spawn(process.env.ERIC_TEST_PYTHON || 'python', ['tests/portfolio_fixture.py'], {
    cwd: appRoot, windowsHide: true,
    env: { ...process.env, PYTHONPATH: appRoot, ERIC_PORTFOLIO_ROOT: '', APP_ENV: 'development', APP_RUNTIME: 'desktop', ERIC_PORTFOLIO_TEST_DIR: directory, ERIC_PORTFOLIO_TEST_PORT: port },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  server.stderr.on('data', data => { process.stderr.write(data); });
}
async function ready() {
  for (let i = 0; i < 100; i++) {
    try { const response = await fetch(base + '/__test__/state'); if ((await response.json()).synthetic_only) return; } catch { /* starting */ }
    if (server.exitCode !== null) throw new Error('Isolated portfolio server exited.');
    await new Promise(done => setTimeout(done, 100));
  }
  throw new Error('Isolated portfolio server did not start.');
}
async function stop() {
  if (!server || server.exitCode !== null) return;
  const closed = new Promise(done => server.once('exit', done));
  server.kill(); await closed;
}
async function fingerprints(path) {
  const result = {};
  for (const name of await readdir(path)) {
    const file = join(path, name), info = await stat(file);
    if (info.isDirectory()) Object.assign(result, await fingerprints(file));
    else result[file] = { hash: createHash('sha256').update(await readFile(file)).digest('hex'), size: info.size, mtime: info.mtimeMs };
  }
  return result;
}
// A deterministic image with visible geometry, so contain sizing and full preview
// are tested against real pixels. This is test art, never a CAD render.
function imageFixture() {
  const width = 320, height = 240, pixels = Buffer.alloc(height * (width * 3 + 1));
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const radius = Math.hypot(x - 160, y - 120);
    const ink = Math.abs(radius - 68) < 2 || Math.abs(radius - 25) < 2 || (Math.abs(x - 160) < 1 && y > 28 && y < 212) || (Math.abs(y - 120) < 1 && x > 68 && x < 252);
    const offset = y * (width * 3 + 1) + 1 + x * 3;
    pixels.set(ink ? [54, 78, 112] : [234, 240, 247], offset);
  }
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type), data]);
    let crc = 0xffffffff;
    for (const byte of body) { crc ^= byte; for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0); }
    const result = Buffer.alloc(data.length + 12);
    result.writeUInt32BE(data.length); body.copy(result, 4); result.writeUInt32BE((crc ^ 0xffffffff) >>> 0, data.length + 8);
    return result;
  };
  const header = Buffer.alloc(13); header.writeUInt32BE(width); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 2;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(pixels)), chunk('IEND', Buffer.alloc(0))]);
}
const png = imageFixture();
start();
let browser;
try {
  await ready();
  browser = await chromium.launch({ headless: true, channel: process.env.ERIC_TEST_BROWSER || 'msedge' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== base) { network.push(url.origin); return route.abort(); }
    return route.continue();
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base + '/?personal_view=portfolio');
  await page.getByRole('heading', { name: '把第一个作品放进来' }).waitFor();
  assert.equal(await page.title(), '工程作品集 · Eric OS');
  await page.screenshot({ path: join(output, 'empty.png'), fullPage: true });
  checks.push('empty-root');

  const project = join(root, '转向节（合成验收）');
  await mkdir(project);
  await Promise.all([
    writeFile(join(project, 'cover.png'), png), writeFile(join(project, '转向节.SLDPRT'), 'synthetic part'),
    writeFile(join(project, '零件图.dwg'), 'synthetic drawing'), writeFile(join(project, '手绘.pdf'), pdfFixture()),
  ]);
  const second = join(root, '装配体（无封面）');
  await mkdir(second); await writeFile(join(second, '总成.SLDASM'), 'synthetic assembly');
  const original = await fingerprints(root);
  await page.getByRole('button', { name: '刷新作品', exact: true }).click();
  await page.getByRole('button', { name: '查看作品：转向节（合成验收）', exact: true }).waitFor();
  assert.equal(await page.locator('.portfolio-card').count(), 2);
  assert.equal(await page.locator('.portfolio-cover img').evaluate(img => img.complete && img.naturalWidth > 0), true);
  await page.screenshot({ path: join(output, 'desktop.png'), fullPage: true });
  checks.push('folder-drop-refresh-cover-and-icon');

  const search = page.getByRole('searchbox', { name: '搜索作品名称' });
  await search.fill('转向'); assert.equal(await page.locator('.portfolio-card').count(), 1);
  await page.reload(); await page.getByRole('button', { name: '查看作品：转向节（合成验收）' }).waitFor();
  assert.equal(await search.inputValue(), '转向');
  await page.getByRole('button', { name: '清除作品搜索' }).click();
  assert.equal(await search.evaluate(el => el === document.activeElement), true);
  await page.getByLabel('文件类型', { exact: true }).selectOption('DWG');
  assert.equal(await page.locator('.portfolio-card').count(), 1);
  await page.getByLabel('文件类型', { exact: true }).focus();
  await page.keyboard.press('Alt+ArrowDown'); await page.keyboard.press('Escape');
  await page.getByLabel('文件类型', { exact: true }).selectOption('all');
  await search.fill('没有这个作品'); await page.getByRole('heading', { name: '没有匹配的作品' }).waitFor();
  await page.getByRole('button', { name: '清除筛选' }).click();
  checks.push('search-filter-no-results-keyboard-url-restoration');

  const card = page.getByRole('button', { name: '查看作品：转向节（合成验收）' });
  await card.focus(); await page.keyboard.press('Enter');
  const sheet = page.getByRole('dialog', { name: '转向节（合成验收）' });
  await sheet.waitFor();
  assert.equal(await sheet.locator('.portfolio-files li').count(), 4);
  await sheet.getByRole('button', { name: '查看图片' }).click();
  await sheet.locator('.portfolio-image-preview img').waitFor();
  const pdf = sheet.getByRole('link', { name: '预览 PDF' });
  const pdfResponse = await context.request.get(new URL(await pdf.getAttribute('href'), base).href);
  assert.match(pdfResponse.headers()['content-type'], /application\/pdf/);
  const popupWait = context.waitForEvent('page'); await pdf.click();
  const popup = await popupWait; await popup.waitForLoadState(); await popup.close();
  await sheet.getByRole('button', { name: '打开作品文件夹' }).click();
  await sheet.getByText('已请求资源管理器打开作品文件夹。').waitFor();
  const state = await (await fetch(base + '/__test__/state')).json();
  assert.deepEqual(state.opened, [{ path: project, operation: 'explore' }]);
  await page.screenshot({ path: join(output, 'detail.png'), fullPage: true });
  await page.keyboard.press('Escape');
  assert.equal(await card.evaluate(el => el === document.activeElement), true);
  checks.push('file-list-original-image-local-pdf-explorer-dispatch-escape-focus');
  assert.deepEqual(await fingerprints(root), original);

  const previousCover = await page.locator('.portfolio-cover img').getAttribute('src');
  await writeFile(join(project, 'cover.png'), png);
  await page.getByRole('button', { name: '刷新作品', exact: true }).click();
  await expect(page.locator('.portfolio-cover img')).not.toHaveAttribute('src', previousCover);
  checks.push('modified-cover-reloads-without-browser-cache');

  await writeFile(join(project, '新增.step'), 'synthetic step');
  await page.getByRole('button', { name: '刷新作品', exact: true }).click();
  await page.getByText('5 个支持文件', { exact: false }).waitFor();
  await unlink(join(project, '新增.step'));
  await rename(second, join(root, '装配体已改名'));
  await page.getByRole('button', { name: '刷新作品', exact: true }).click();
  await page.getByRole('button', { name: '查看作品：装配体已改名' }).waitFor();
  await rename(join(root, '装配体已改名'), join(directory, '移出作品集'));
  await page.getByRole('button', { name: '刷新作品', exact: true }).click();
  await page.getByText('1 个作品', { exact: true }).waitFor();
  checks.push('add-modify-rename-remove-refresh');

  await page.getByRole('button', { name: '修改目录' }).click();
  const rootInput = page.getByLabel('作品总目录', { exact: true });
  await rootInput.fill('relative'); await page.getByRole('button', { name: '保存目录' }).click();
  await page.getByText('请选择本机的独立绝对目录', { exact: false }).waitFor();
  assert.equal(await rootInput.inputValue(), 'relative');
  assert.equal(await rootInput.evaluate(el => el === document.activeElement), true);
  await rootInput.fill(root + '-other');
  await page.getByRole('button', { name: '取消修改' }).click();
  const other = join(directory, 'other'); await mkdir(other);
  await page.getByRole('button', { name: '修改目录' }).click(); await rootInput.fill(other);
  await page.getByRole('button', { name: '保存目录' }).click();
  await page.getByRole('heading', { name: '把第一个作品放进来' }).waitFor();
  await stop(); start(); await ready(); await page.reload();
  await page.getByText(other, { exact: true }).waitFor();
  await page.getByRole('button', { name: '修改目录' }).click(); await rootInput.fill(root);
  await page.getByRole('button', { name: '保存目录' }).click();
  await page.getByRole('button', { name: '查看作品：转向节（合成验收）' }).waitFor();
  await stop(); start(); await ready(); await page.reload();
  await page.getByRole('button', { name: '查看作品：转向节（合成验收）' }).waitFor();
  checks.push('path-validation-preserves-input-and-process-restart-preserves-settings-and-files');

  await page.route(base + '/api/personal/portfolio', async route => {
    await new Promise(done => setTimeout(done, 500));
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: { code: 'root_unavailable', message: '合成目录暂时不可用，请重试。' } }) });
  });
  await page.getByRole('button', { name: '刷新作品', exact: true }).click();
  await page.getByText('正在读取作品文件夹…', { exact: true }).waitFor();
  await page.getByRole('alert').filter({ hasText: '合成目录暂时不可用' }).waitFor();
  await page.unroute(base + '/api/personal/portfolio'); await page.getByRole('button', { name: '重新读取作品' }).click();
  await page.getByRole('button', { name: '查看作品：转向节（合成验收）' }).waitFor();
  checks.push('loading-failure-and-retry');

  await page.getByRole('button', { name: '深色', exact: true }).click();
  await page.screenshot({ path: join(output, 'dark.png'), fullPage: true });
  await page.getByRole('button', { name: '浅色', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: join(output, 'mobile.png'), fullPage: true });
  await page.getByRole('button', { name: '查看作品：转向节（合成验收）' }).click();
  await sheet.waitFor();
  assert.equal(await sheet.evaluate(el => el.scrollWidth > el.clientWidth), false);
  await page.screenshot({ path: join(output, 'mobile-detail.png'), fullPage: true });
  assert.equal(await page.locator('vite-error-overlay').count(), 0);
  assert.deepEqual(errors, []);
  assert.deepEqual(network, []);
  checks.push('390px-dark-light-reduced-motion-no-overflow-no-runtime-errors-no-external-requests');
  await writeFile(join(output, 'report.json'), JSON.stringify({ passed: true, base, directory, checks, errors, externalRequests: network, viewports: ['1440×1000', '390×844'], explorer: process.env.ERIC_PORTFOLIO_TEST_EXPLORER === '1' ? 'native' : 'recorded exact path' }, null, 2));
  console.log(JSON.stringify({ passed: true, checks, output, directory }, null, 2));
} finally {
  await browser?.close(); await stop();
}
