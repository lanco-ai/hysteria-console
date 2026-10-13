const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { expect } = require('@playwright/test');

const baseUrl = process.env.PREVIEW_BASE_URL;
const screenshots = path.resolve(process.env.REACT_SCREENSHOT_DIR || '.superpowers/sdd/2026-09-14-react-overview/screenshots');
const rowFor = (page, user = 'demo_alex') => page.locator(`tr[data-user="${user}"]`);
const bootPath = '/api/v1/admin/overview-page';
async function snapshot(context) {
  const response = await context.request.get(baseUrl + bootPath);
  assert.equal(response.status(), 200);
  return response.json();
}
async function ready(page) {
  await expect(rowFor(page).getByRole('button', { name: '编辑套餐' })).toBeEnabled();
}
async function goto(page, query = '') {
  const response = await page.goto(`${baseUrl}/__react/admin${query}`);
  assert.equal(response.status(), 200, 'controlled React overview entry must be available');
  await ready(page);
}
async function contextFor(browser) {
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
  return context;
}
async function trafficSummary(browser) {
  const context = await contextFor(browser), page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const now = new Date('2026-10-13T12:00:00Z');
  await page.clock.install({ time: now }); await page.clock.pauseAt(now);
  const usage = { ts: '2026-10-13T12:00:00', stats: {
    current_hour_bytes: 63.31 * 1024 ** 2 | 0, today_bytes: 10.81 * 1024 ** 3,
    yesterday_bytes: 17.74 * 1024 ** 3, last_7d_bytes: 87.17 * 1024 ** 3,
    cycle_bytes: 284.45 * 1024 ** 3, cycle_day: 29, cycle_total_days: 30, online: 2,
  } };
  for (const key of ['today_bytes', 'yesterday_bytes', 'last_7d_bytes', 'cycle_bytes']) usage.stats[key] = Math.floor(usage.stats[key]);
  let summaryReads = 0, fail = false, forbidden = false;
  await page.route('**/api/v1/admin/usage?summary=yes', route => {
    summaryReads++;
    return route.fulfill({ status: forbidden ? 401 : fail ? 503 : 200, contentType: 'application/json', body: JSON.stringify(forbidden || fail ? { error: 'unavailable' } : usage) });
  });
  await goto(page);
  const cards = page.locator('.overview-traffic-card');
  const values = page.locator('.overview-traffic-value');
  await expect(values).toHaveText(['63.31 MB', '10.81 GB', '87.17 GB', '284.45 GB']);
  await expect(page.locator('.overview-traffic-detail')).toHaveText(['2 在线', '昨日 17.74 GB', '日均 12.45 GB', '第 29 / 30 天']);
  await expect(page.getByRole('link', { name: '查看流量分析' })).toHaveAttribute('href', '/admin/usage');
  const cycle = await page.locator('.overview-cycle').boundingBox();
  const summaryBox = await page.locator('.overview-traffic').boundingBox();
  assert(summaryBox.y + summaryBox.height <= cycle.y, 'global summary must precede existing billing controls');
  fs.mkdirSync(screenshots, { recursive: true });
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 1080 });
    // Resizing from the desktop rail starts the existing mobile drawer transition.
    // Finish finite animations before checking visibility or taking review evidence.
    await page.screenshot({ path: path.join(screenshots, `users-traffic-${width}.png`), fullPage: true, animations: 'disabled' });
    const boxes = await cards.evaluateAll(nodes => nodes.map(node => {
      const box = node.getBoundingClientRect();
      return { x: box.x, y: box.y, right: box.right, height: box.height };
    }));
    assert.equal(boxes.length, 4);
    assert(Math.abs(boxes[0].y - boxes[1].y) < 1, `${width}: first pair must align`);
    if (width > 720) assert(boxes.every(box => Math.abs(box.y - boxes[0].y) < 1), `${width}: four metrics must share one row`);
    else {
      assert(boxes[2].y > boxes[0].y && Math.abs(boxes[2].y - boxes[3].y) < 1, `${width}: mobile metrics must form two rows`);
      assert(boxes.every(box => box.x >= 0 && box.right <= width), `${width}: cards must fit the viewport`);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${width}: mobile page must not overflow horizontally`);
      const visible = await cards.evaluateAll(nodes => nodes.map(node => {
        const box = node.getBoundingClientRect();
        return document.elementFromPoint(box.x + 4, box.y + 4)?.closest('.overview-traffic-card') === node;
      }));
      assert(visible.every(Boolean), `${width}: navigation must not cover any metric card`);
    }
    const numbers = await page.locator('.overview-traffic-number').evaluateAll(nodes => nodes.map(node => ({ size: getComputedStyle(node).fontSize, y: node.getBoundingClientRect().y })));
    assert.equal(new Set(numbers.map(number => number.size)).size, 1, 'all metrics need the same type scale');
    assert(Math.abs(numbers[0].y - numbers[1].y) < 1, 'paired values must align');
    await expect(page.getByRole('link', { name: '查看流量分析' })).toBeVisible();
  }
  await page.setViewportSize({ width: 1440, height: 1080 });
  const before = await values.allTextContents(), readsBefore = summaryReads;
  await page.getByRole('searchbox').fill('no-match');
  await expect(page.locator('#filter-empty')).toBeVisible();
  assert.deepEqual(await values.allTextContents(), before, 'user search cannot change global metrics');
  await page.getByRole('searchbox').fill('');
  await page.locator('[data-filter="online"]').click();
  assert.deepEqual(await values.allTextContents(), before, 'user status filter cannot change global metrics');
  assert.equal(summaryReads, readsBefore, 'local filters do not trigger global requests');
  await page.locator('[data-filter="all"]').click();
  await page.locator('.create-toggle').click();
  await page.locator('#create-note').fill('summary failures preserve this draft');
  fail = true;
  await page.locator('[data-role="admin-poll-status"]').click();
  await expect(page.getByRole('button', { name: '重试流量概览' })).toBeEnabled();
  await expect(values).toHaveText(['—', '—', '—', '—']);
  await ready(page);
  await expect(page.locator('#create-note')).toHaveValue('summary failures preserve this draft');
  fail = false;
  await page.getByRole('button', { name: '重试流量概览' }).click();
  await expect(values).toHaveText(['63.31 MB', '10.81 GB', '87.17 GB', '284.45 GB']);
  await expect(page.locator('#create-note')).toHaveValue('summary failures preserve this draft');
  const refreshedReads = summaryReads;
  await page.clock.runFor(30_000);
  await expect.poll(() => summaryReads).toBe(refreshedReads + 1);
  forbidden = true;
  await page.locator('[data-role="admin-poll-status"]').click();
  await expect(page.locator('.users-table')).toHaveCount(0);
  await expect(page.locator('.overview-traffic-card')).toHaveCount(0);
  await expect(page.getByRole('link', { name: '管理员登录' })).toHaveAttribute('href', '/login');
  assert.deepEqual(errors, []);
  await context.close();
}
async function realOperations(browser) {
  const context = await contextFor(browser), page = await context.newPage();
  const failures = [], requests = [];
  page.on('pageerror', error => failures.push(error.message));
  page.on('response', response => { if (response.status() >= 400) failures.push(`${response.status()} ${new URL(response.url()).pathname}`); });
  page.on('requestfailed', request => { if (!request.failure()?.errorText.includes('ERR_ABORTED')) failures.push(request.failure()?.errorText); });
  page.on('request', request => requests.push([request.method(), new URL(request.url()).pathname]));
  await goto(page);
  assert.equal(await page.title(), '用户');
  await expect(page.locator('.page-title small')).toHaveText('preview.invalid');
  await expect(page.locator('.page-title small')).not.toContainText('计费周期');
  assert.equal(await page.locator('.users-table th').count(), 5);
  assert.equal(await page.locator('script[src*="admin-poll"]').count(), 0);
  await expect(page.locator('.sidebar-link[aria-current="page"]')).toHaveText('用户');
  await expect(page.getByRole('link', { name: '导出 CSV' })).toHaveAttribute('href', '/admin/usage.csv?window=cycle');
  // Cycle settings and cycle-wide actions share one bar instead of the top bar and KPI cards.
  await expect(page.locator('.overview-cycle .cycle-config-form')).toHaveCount(1);
  await expect(page.locator('.topbar .cycle-config-form')).toHaveCount(0);
  await expect(page.locator('#total-used')).toHaveCount(0);
  // The bar's inputs, 保存, 导出 CSV and 清空本周期用量 share one height and one centre line.
  const cycleControls = await page.locator('.overview-cycle').locator('input, .btn').evaluateAll(nodes => nodes.map(node => {
    const box = node.getBoundingClientRect();
    return { height: Math.round(box.height), middle: box.top + box.height / 2 };
  }));
  assert.equal(cycleControls.length, 5);
  assert.deepEqual([...new Set(cycleControls.map(item => item.height))], [36]);
  const middles = cycleControls.map(item => item.middle);
  assert(Math.max(...middles) - Math.min(...middles) <= 1, `cycle controls must share a centre line: ${middles}`);
  // The create form's landing select is as tall as its text inputs.
  await page.locator('.create-toggle').click();
  const createHeights = await page.evaluate(() => ['#create-user', '#create-quota-gb', '#create-landing-initial-egress']
    .map(id => Math.round(document.querySelector(id).getBoundingClientRect().height)));
  assert.deepEqual(createHeights, [36, 36, 36]);
  await page.locator('.create-toggle').click();
  assert((await snapshot(context)).cycle.total_used > 0, 'accounting fixture must be nonzero');
  fs.mkdirSync(screenshots, { recursive: true });
  const geometry = [];
  for (const width of [1920, 1024, 390]) {
    await page.setViewportSize({ width, height: 1080 });
    await page.evaluate(() => { document.activeElement.blur(); window.scrollTo(0, 0); });
    await page.screenshot({ path: path.join(screenshots, `react-${width}.png`), fullPage: true });
    for (const selector of ['.topbar', '.overview-cycle', '.users-header', '.users-table-wrap', '.create-section', '#user-filter', '#filter-count']) {
      const box = await page.locator(selector).boundingBox();
      assert(box && box.width > 0 && box.height > 0, `${width} ${selector} must be visible`);
      geometry.push({ width, selector, react: box });
    }
    const spark = await rowFor(page).locator('svg.spark').innerHTML();
    assert(spark.length > 0, 'sparkline should render for a populated user');
    await page.locator('.create-toggle').click();
    await page.screenshot({ path: path.join(screenshots, `react-create-${width}.png`), fullPage: true });
    await page.locator('.create-toggle').click();
    await rowFor(page).getByRole('button', { name: '编辑套餐' }).click();
    await page.screenshot({ path: path.join(screenshots, `react-edit-${width}.png`), fullPage: true });
    await page.keyboard.press('Escape');
  }
  fs.writeFileSync(path.join(screenshots, 'geometry.json'), JSON.stringify(geometry, null, 2));
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.getByRole('searchbox').fill('DEMO_A');
  await expect(page.locator('#filter-count')).toHaveText('1 / 3 个');
  await page.getByRole('searchbox').fill('no-match');
  await expect(page.locator('#filter-empty')).toBeVisible();
  await page.getByRole('searchbox').fill('');
  // The filter chips carry the counts, so the page needs no separate summary.
  await expect(page.locator('.filter-chips .chip-count')).toHaveText(['3', '1', '1']);
  await page.locator('[data-filter="online"]').click();
  await expect(page.locator('#filter-count')).toHaveText('1 / 3 个');
  await page.locator('[data-filter="over"]').click();
  await expect(page.locator('#filter-count')).toHaveText('1 / 3 个');
  await page.locator('[data-filter="all"]').click();
  const beforeCancel = requests.filter(([method]) => method === 'POST').length;
  page.on('dialog', dialog => dialog.dismiss());
  for (const name of ['清流量', '刷新流量', '重置订阅', '暂停', '删除']) await rowFor(page).getByRole('button', { name, exact: true }).click();
  await page.getByRole('button', { name: '清空本周期用量' }).click();
  await page.locator('.cycle-config-form button').click();
  assert.equal(requests.filter(([method]) => method === 'POST').length, beforeCancel, 'every destructive cancellation sends zero POSTs');
  page.removeAllListeners('dialog'); page.on('dialog', dialog => dialog.accept());
  await page.evaluate(() => { navigator.clipboard.writeText = async text => { window.__copied = text; }; });
  await rowFor(page).getByRole('button', { name: '复制 demo_alex 的专属面板链接', exact: true }).click();
  assert.equal(await page.evaluate(() => window.__copied), (await snapshot(context)).users.find(row => row.user === 'demo_alex').panel_url);
  await expect(rowFor(page).getByRole('status')).toHaveText('已复制');
  const initial = await snapshot(context);
  await rowFor(page).getByRole('button', { name: '刷新流量', exact: true }).click();
  await expect(rowFor(page).locator('[data-role="used"]')).toHaveText('0.00 B'); await ready(page);
  const refreshed = await snapshot(context);
  assert.equal(refreshed.cycle.total_used, initial.cycle.total_used, 'refresh preserves server total');
  const otherBefore = refreshed.users.find(row => row.user === 'must_change').used;
  await rowFor(page, 'must_change').getByRole('button', { name: '清流量', exact: true }).click(); await ready(page);
  await expect(rowFor(page, 'must_change').locator('[data-role="used"]')).toHaveText('0.00 B');
  assert.equal((await snapshot(context)).cycle.total_used, refreshed.cycle.total_used - otherBefore, 'reset subtracts real accounted bytes');
  await rowFor(page).getByRole('button', { name: '暂停', exact: true }).click();
  await expect(rowFor(page).getByRole('button', { name: '启用', exact: true })).toBeEnabled();
  await rowFor(page).getByRole('button', { name: '启用', exact: true }).click();
  await expect(rowFor(page).getByRole('button', { name: '暂停', exact: true })).toBeEnabled();
  await page.locator('.create-toggle').click();
  await page.locator('#create-user').fill('browser_new');
  await page.locator('#create-note').fill('non-secret create draft');
  await page.locator('#create-panel-password').fill('fictional-password');
  await page.locator('#cycle-day').fill('4');
  await page.getByRole('button', { name: '创建', exact: true }).click();
  await expect(rowFor(page, 'browser_new')).toBeVisible(); await ready(page);
  assert.equal(await page.locator('.create-section').getAttribute('open'), null);
  assert.equal(await page.locator('#cycle-day').inputValue(), '4', 'create preserves independent cycle draft');
  await page.locator('.create-toggle').click();
  await page.locator('#create-note').fill('another unsaved draft');
  const revision = await rowFor(page).getAttribute('data-revision');
  await rowFor(page).getByRole('button', { name: '编辑套餐' }).click();
  await expect(page.locator('#edit-panel-password')).toHaveValue('');
  await page.locator('#edit-note').fill('browser edited note');
  await page.locator('#edit-quota-extra-gb').fill('5');
  await page.locator('#edit-guest').uncheck(); await page.locator('#edit-tuic-enabled').uncheck();
  await page.getByRole('button', { name: '保存更改' }).click();
  await expect(page.locator('#user-edit-dialog')).toHaveCount(0); await ready(page);
  assert.notEqual(await rowFor(page).getAttribute('data-revision'), revision);
  assert.equal(await page.locator('#create-note').inputValue(), 'another unsaved draft');
  await expect(rowFor(page).getByRole('button', { name: '编辑套餐' })).toBeFocused();
  const edited = (await snapshot(context)).users.find(row => row.user === 'demo_alex');
  assert.equal(edited.metered, false); assert.equal(edited.tuic_enabled, false); assert.equal(edited.note, 'browser edited note');
  const oldLink = edited.panel_url;
  await rowFor(page).getByRole('button', { name: '重置订阅' }).click(); await ready(page);
  const rotated = (await snapshot(context)).users.find(row => row.user === 'demo_alex');
  assert.notEqual(rotated.panel_url, oldLink);
  await expect(rowFor(page).getByRole('link', { name: '面板', exact: true })).toHaveAttribute('href', rotated.panel_url);
  await page.locator('.cycle-config-form button').click(); await ready(page);
  assert.equal((await snapshot(context)).cycle.settlement_day, 4);
  await rowFor(page, 'browser_new').getByRole('button', { name: '删除', exact: true }).click();
  await expect(rowFor(page, 'browser_new')).toHaveCount(0); await ready(page);
  await page.getByRole('button', { name: '清空本周期用量' }).click();
  await expect(page.locator('.flash', { hasText: '已清除全部用户本周期已用流量' })).toBeVisible(); await ready(page);
  assert.equal((await snapshot(context)).cycle.total_used, 0);
  assert(!requests.some(([, url]) => url === '/admin/overview.json'), 'React never starts legacy polling');
  assert.deepEqual(failures, []);
  await context.close();
}
async function main() {
  const browser = await chromium.launch({
    headless: true,
    args: ['--disable-gpu', '--num-raster-threads=1', '--renderer-process-limit=2'],
  });
  try {
    if (process.env.REACT_OVERVIEW_TRAFFIC_ONLY) {
      await trafficSummary(browser);
      console.log('React overview traffic summary acceptance passed');
      return;
    }
    if (!process.env.REACT_OVERVIEW_LIFECYCLE_ONLY) await trafficSummary(browser);
    if (!process.env.REACT_OVERVIEW_LIFECYCLE_ONLY) await realOperations(browser);
    const context = await contextFor(browser);
    const baseline = await snapshot(context);
    await context.close();
    await require('./react_overview_lifecycle_browser.cjs')(browser, baseline);
  } finally {
    await browser.close();
  }
  console.log('React overview browser acceptance passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
