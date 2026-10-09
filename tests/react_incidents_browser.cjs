const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { expect } = require('@playwright/test');

const baseUrl = process.env.PREVIEW_BASE_URL;

async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu', '--num-raster-threads=1', '--renderer-process-limit=2'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
  const page = await context.newPage();
  const requests = [];
  page.on('request', request => requests.push([request.method(), new URL(request.url()).pathname]));
  // 事故处理 now lives in 流量分析; the old operations tab link follows it.
  await page.goto(`${baseUrl}/__react/admin/health?tab=incidents`);
  await expect(page).toHaveURL(/\/__react\/admin\/incidents$/);
  await expect(page).toHaveTitle('流量分析');
  await expect(page.locator('.sidebar-link[aria-current="page"]')).toHaveText('流量分析');
  const incidents = page.locator('#incidents');
  await expect(incidents.getByRole('heading', { name: '异常与告警' })).toBeVisible();
  // /admin/incidents opens scrolled to the incident section, with focus on it.
  await expect(incidents).toBeFocused();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
  await expect(incidents).toBeInViewport();
  await expect(incidents.locator('.incident-stat')).toHaveCount(3);
  await expect(incidents.locator('.incident-chip')).toContainText(['demo_alex']);
  await expect(incidents.locator('.incident-alerts li')).toHaveCount(1);
  await expect(page.getByRole('link', { name: '下载证据 JSON' })).toHaveAttribute('href', '/api/v1/admin/incidents/evidence');
  await expect(page.getByText('处置候选用户')).toHaveCount(0);
  await expect(page.getByText('线路质量摘要')).toHaveCount(0);
  const ranking = page.locator('.user-ranking');
  await expect(ranking.getByRole('heading', { name: '用户排行 · 近 24 小时' })).toBeVisible();
  const firstRow = ranking.locator('.top-row').first();
  await expect(firstRow.getByRole('link', { name: /demo_alex/ })).toHaveAttribute('href', '/admin/user/demo_alex');
  await expect(firstRow.getByRole('button', { name: '暂停 1 小时' })).toBeVisible();
  await expect(firstRow.getByRole('button', { name: '轮换 Token' })).toBeVisible();
  const postsBefore = requests.filter(([method]) => method === 'POST').length;
  page.once('dialog', dialog => dialog.dismiss());
  await firstRow.getByRole('button', { name: '暂停 1 小时' }).click();
  assert.equal(requests.filter(([method]) => method === 'POST').length, postsBefore, 'a dismissed confirmation sends nothing');
  assert(requests.some(([method, path]) => method === 'GET' && path === '/api/v1/admin/incidents'));
  assert(requests.some(([method, path]) => method === 'GET' && path === '/api/v1/admin/usage'));
  assert(!requests.some(([, path]) => path === '/api/v1/admin/health' || path === '/api/v1/admin/logs'));
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => incidents.locator('.incidents-summary').evaluate(element => getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/).length)).toBe(1);
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'mobile must not scroll horizontally');
  await page.setViewportSize({ width: 1440, height: 900 });

  // 运维 keeps two tabs with history and keyboard navigation.
  await page.goto(`${baseUrl}/__react/admin/health`);
  await expect(page).toHaveTitle('运维');
  await expect(page.getByRole('tab')).toHaveText(['健康状态', '清零日志']);
  await page.getByRole('tab', { name: '清零日志' }).click();
  await expect(page).toHaveURL(/\/__react\/admin\/health\?tab=logs$/);
  await expect(page.getByRole('heading', { name: '最近清零记录' })).toBeVisible();
  await expect.poll(() => requests.some(([method, path]) => method === 'GET' && path === '/api/v1/admin/logs')).toBe(true);
  await page.goBack();
  await expect(page).toHaveURL(/\/__react\/admin\/health$/);
  await expect(page.getByRole('tab', { name: '健康状态' })).toHaveAttribute('aria-selected', 'true');
  await page.goForward();
  await expect(page.getByRole('tab', { name: '清零日志' })).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('tab', { name: '清零日志' }).focus();
  await page.getByRole('tab', { name: '清零日志' }).press('ArrowRight');
  await expect(page.getByRole('tab', { name: '健康状态' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.health-checks')).toBeVisible();
  const maxDelta = page.getByLabel('最大变化 (%)');
  await maxDelta.fill('37');
  await page.getByRole('tab', { name: '清零日志' }).click();
  await page.getByRole('tab', { name: '健康状态' }).click();
  await expect(maxDelta).toHaveValue('37');
  await context.close();
  await browser.close();
  console.log('React incidents browser acceptance passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
