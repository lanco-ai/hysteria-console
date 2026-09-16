const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { expect } = require('@playwright/test');

const baseUrl = process.env.PREVIEW_BASE_URL;
const screenshots = path.resolve('.superpowers/sdd/2026-09-14-react-overview/screenshots');
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
async function realOperations(browser) {
  const context = await contextFor(browser), page = await context.newPage();
  const failures = [], requests = [];
  page.on('pageerror', error => failures.push(error.message));
  page.on('response', response => { if (response.status() >= 400) failures.push(`${response.status()} ${new URL(response.url()).pathname}`); });
  page.on('requestfailed', request => { if (!request.failure()?.errorText.includes('ERR_ABORTED')) failures.push(request.failure()?.errorText); });
  page.on('request', request => requests.push([request.method(), new URL(request.url()).pathname]));
  await goto(page);
  assert.equal(await page.title(), '总览');
  assert.equal(await page.locator('.users-table th').count(), 5);
  assert.equal(await page.locator('script[src*="admin-poll"]').count(), 0);
  await expect(page.locator('.sidebar-link[aria-current="page"]')).toHaveText('总览');
  await expect(page.getByRole('link', { name: '导出 CSV' })).toHaveAttribute('href', '/admin/usage.csv?window=cycle');
  assert((await snapshot(context)).cycle.total_used > 0, 'accounting fixture must be nonzero');
  fs.mkdirSync(screenshots, { recursive: true });
  const geometry = [];
  for (const width of [1920, 1024, 390]) {
    await page.setViewportSize({ width, height: 1080 });
    await page.evaluate(() => { document.activeElement.blur(); window.scrollTo(0, 0); });
    await page.screenshot({ path: path.join(screenshots, `react-${width}.png`), fullPage: true });
    for (const selector of ['.topbar', '.overview-stats', '.users-header', '.users-table-wrap', '.create-section', '#user-filter', '#filter-count']) {
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
  await page.getByRole('button', { name: '在线', exact: true }).click();
  await expect(page.locator('#filter-count')).toHaveText('1 / 3 个');
  await page.getByRole('button', { name: '超限', exact: true }).click();
  await expect(page.locator('#filter-count')).toHaveText('1 / 3 个');
  await page.getByRole('button', { name: '全部', exact: true }).click();
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
  await expect(page.locator('#total-used')).toHaveText('0.00 B'); await ready(page);
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
