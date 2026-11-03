import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';

const base = process.env.ATTENTION_COURSE_QA_URL || 'http://127.0.0.1:18766';
if (new URL(base).hostname !== '127.0.0.1' || new URL(base).port === '8765') throw new Error('Use the explicit isolated fixture server.');
const output = process.env.ATTENTION_COURSE_QA_OUTPUT || await fs.mkdtemp(path.join(os.tmpdir(), 'attention-course-'));
await fs.mkdir(output, { recursive: true });
const report = { target: base, synthetic: true, browserPath: 'Playwright (Browser plugin not available)', viewport: { width: 1440, height: 1000 }, errors: [], blocked: [], checks: {}, screenshots: [] };
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: report.viewport });
  await context.route('**/*', route => {
    if (new URL(route.request().url()).origin !== base) { report.blocked.push(new URL(route.request().url()).origin); return route.abort(); }
    return route.continue();
  });
  const api = context.request;
  const page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') report.errors.push(message.text()); });
  const seeded = await (await api.post(base + '/__course_fixture/seed', { data: { kind: 'math' } })).json();
  assert.equal(seeded.suggestion.name, '高等数学 II');
  const before = await (await api.get(base + '/api/personal/events?status=all&limit=100')).json();
  await page.goto(base + '/?personal_raw=' + seeded.snapshot_id, { waitUntil: 'networkidle' });
  await page.getByLabel('课程（可选）', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('课程（可选）', { exact: true }).inputValue(), '');
  await page.getByText('建议课程：高等数学 II', { exact: false }).waitFor();
  report.checks.suggestionIsUnconfirmed = true;
  await page.getByRole('button', { name: '采用建议', exact: true }).click();
  assert.equal(await page.getByLabel('课程（可选）', { exact: true }).inputValue(), 'course-math-ii');
  await page.getByLabel('是否与我相关', { exact: true }).selectOption('yes');
  await page.getByLabel('是否参加 / 执行', { exact: true }).selectOption('yes');
  for (const box of await page.locator('form input[type=checkbox]').all()) await box.check();
  await page.getByLabel('课程（可选）', { exact: true }).scrollIntoViewIfNeeded();
  const candidateShot = path.join(output, 'course-candidate.png');
  await page.screenshot({ path: candidateShot, fullPage: false });
  report.screenshots.push(candidateShot);
  const confirmedResponse = page.waitForResponse(r => r.url().endsWith('/raw/' + seeded.snapshot_id + '/confirm') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '确认并创建所选事项', exact: true }).click();
  const response = await confirmedResponse;
  assert.equal(response.status(), 201);
  const confirmed = await response.json();
  const matter = confirmed.events[0];
  assert.equal(matter.course.name, '高等数学 II');
  assert.equal(matter.tasks.length, 1);
  assert.equal(matter.tasks[0].course_id, matter.course_id);
  await page.getByText('课程：高等数学 II', { exact: true }).waitFor();
  report.checks.confirmedMatterAndTask = true;

  const repeated = await api.post(base + '/api/personal/raw/' + seeded.snapshot_id + '/confirm', { data: response.request().postDataJSON() });
  assert.equal(repeated.status(), 201);
  const repeat = await repeated.json();
  assert.equal(repeat.events[0].id, matter.id);
  assert.equal(repeat.events[0].tasks[0].id, matter.tasks[0].id);
  const after = await (await api.get(base + '/api/personal/events?status=all&limit=100')).json();
  assert.equal(after.total, before.total + 1);
  report.checks.repeatConfirmIdempotent = true;

  await page.getByRole('navigation', { name: '事项导航' }).getByRole('button', { name: '今天', exact: true }).click();
  await page.getByRole('heading', { name: '今天', exact: true }).waitFor();
  await page.getByText('高等数学 II', { exact: true }).first().waitFor();
  const today = await (await api.get(base + '/api/personal/planning/today')).json();
  const due = today.items.find(i => i.event_id === matter.id);
  assert.equal(due.course.name, '高等数学 II');
  assert.equal(due.tasks[0].course_id, matter.course_id);
  report.checks.todayCourse = true;
  const todayShot = path.join(output, 'course-today-desktop.png');
  await page.screenshot({ path: todayShot, fullPage: false }); report.screenshots.push(todayShot);

  await page.goto(base + '/?personal_event=' + matter.id, { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: '修改课程', exact: true }).click();
  let dialog = page.getByRole('dialog', { name: '关联课程', exact: true });
  await dialog.getByLabel('课程（可选）', { exact: true }).selectOption('__new');
  await dialog.getByRole('button', { name: '添加并选择', exact: true }).click();
  await dialog.getByText('课程名称请填写 1–120 字。', { exact: true }).waitFor();
  await dialog.getByLabel('新课程名称', { exact: true }).fill('线性代数（验证样例）');
  await dialog.getByRole('button', { name: '添加并选择', exact: true }).click();
  await dialog.getByLabel('新课程名称', { exact: true }).waitFor({ state: 'detached' });
  const physics = (await (await api.get(base + '/api/personal/courses')).json()).items.find(c => c.name === '大学物理');
  await dialog.getByLabel('课程（可选）', { exact: true }).selectOption(physics.id);
  await dialog.getByRole('button', { name: '保存课程关联', exact: true }).click();
  await page.getByText('课程：大学物理', { exact: true }).waitFor();
  let value = await (await api.get(base + '/api/personal/events/' + matter.id)).json();
  assert.equal(value.tasks[0].id, matter.tasks[0].id);
  assert.equal(value.tasks[0].course_id, physics.id);
  report.checks.otherCourseAndCreation = true;

  await page.getByRole('button', { name: '修改课程', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '关联课程', exact: true });
  await dialog.getByLabel('课程（可选）', { exact: true }).selectOption('');
  await dialog.getByRole('button', { name: '保存课程关联', exact: true }).click();
  await page.getByRole('button', { name: '关联课程', exact: true }).waitFor();
  value = await (await api.get(base + '/api/personal/events/' + matter.id)).json();
  assert.equal(value.course, null); assert.equal(value.tasks[0].course, null);
  report.checks.emptyAssociationAndTaskPreserved = true;
  await page.getByRole('button', { name: '关联课程', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '关联课程', exact: true });
  await dialog.getByLabel('课程（可选）', { exact: true }).selectOption('course-math-ii');
  await dialog.getByRole('button', { name: '保存课程关联', exact: true }).click();
  await page.getByText('课程：高等数学 II', { exact: true }).waitFor();

  const plain = await (await api.post(base + '/__course_fixture/seed', { data: { kind: 'plain' } })).json();
  assert.equal(plain.suggestion, null);
  await page.goto(base + '/?personal_raw=' + plain.snapshot_id, { waitUntil: 'networkidle' });
  assert.equal(await page.getByLabel('课程（可选）', { exact: true }).inputValue(), '');
  await page.getByLabel('是否与我相关', { exact: true }).selectOption('yes');
  await page.getByLabel('是否参加 / 执行', { exact: true }).selectOption('yes');
  for (const box of await page.locator('form input[type=checkbox]').all()) await box.check();
  const plainResponse = page.waitForResponse(r => r.url().endsWith('/raw/' + plain.snapshot_id + '/confirm') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '确认并创建所选事项', exact: true }).click();
  const plainValue = (await (await plainResponse).json()).events[0];
  assert.equal(plainValue.course, null); assert.equal(plainValue.tasks.length, 1);
  report.checks.nonCourseConfirm = true;

  await page.getByRole('navigation', { name: '事项导航' }).getByRole('button', { name: '今天', exact: true }).click();
  await page.getByRole('button', { name: '深色', exact: true }).click();
  const darkShot = path.join(output, 'course-today-dark.png');
  await page.screenshot({ path: darkShot, fullPage: false }); report.screenshots.push(darkShot);
  await page.getByRole('button', { name: '浅色', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByText('高等数学 II', { exact: true }).first().waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
  const mobileShot = path.join(output, 'course-today-mobile.png');
  await page.screenshot({ path: mobileShot, fullPage: false }); report.screenshots.push(mobileShot);
  report.checks.themeAndNarrowScreen = true;
  report.matterId = matter.id; report.taskId = matter.tasks[0].id;
  assert.deepEqual(report.errors, []); assert.deepEqual(report.blocked, []);
  report.passed = true;
  await context.close();
} finally {
  await browser.close();
  await fs.writeFile(path.join(output, 'course-browser.json'), JSON.stringify(report, null, 2) + '\n');
}
process.stdout.write(JSON.stringify({ passed: report.passed === true, checks: report.checks, errors: report.errors.length, blocked: report.blocked.length }) + '\n');

