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
  await page.goto(`${baseUrl}/__react/admin/health?tab=incidents`);
  await expect(page).toHaveTitle('运维');
  await expect(page.getByRole('tab', { name: '事故处理' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('heading', { name: '处置候选用户' })).toBeVisible();
  await expect(page.locator('.health-kpi-card')).toHaveCount(3);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.locator('.incidents-page .health-top-kpis').evaluate(element => getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/).length)).toBe(2);
  await page.setViewportSize({ width: 1440, height: 900 });
  const candidateUsers = page.getByRole('heading', { name: '处置候选用户' }).locator('xpath=ancestor::section[1]');
  await expect(candidateUsers.locator('thead tr')).toContainText('24h 流量');
  await expect(candidateUsers.locator('thead tr')).toContainText('操作');
  await expect(page.getByRole('link', { name: '下载证据 JSON' })).toHaveAttribute('href', '/api/v1/admin/incidents/evidence');
  assert(requests.some(([method, path]) => method === 'GET' && path === '/api/v1/admin/incidents'));
  assert(!requests.some(([, path]) => path === '/api/v1/admin/health' || path === '/api/v1/admin/logs'));
  assert(!requests.some(([, path]) => path === '/admin/incidents'));
  await page.getByRole('tab', { name: '清零日志' }).click();
  await expect(page).toHaveURL(/\/__react\/admin\/health\?tab=logs$/);
  await expect(page.getByRole('heading', { name: '最近清零记录' })).toBeVisible();
  assert(requests.some(([method, path]) => method === 'GET' && path === '/api/v1/admin/logs'));
  await page.goBack();
  await expect(page).toHaveURL(/\/__react\/admin\/health\?tab=incidents$/);
  await expect(page.getByRole('tab', { name: '事故处理' })).toHaveAttribute('aria-selected', 'true');
  await page.goForward();
  await expect(page).toHaveURL(/\/__react\/admin\/health\?tab=logs$/);
  await expect(page.getByRole('tab', { name: '清零日志' })).toHaveAttribute('aria-selected', 'true');
  await page.goBack();
  await expect(page).toHaveURL(/\/__react\/admin\/health\?tab=incidents$/);
  await page.getByRole('tab', { name: '事故处理' }).focus();
  await page.getByRole('tab', { name: '事故处理' }).press('ArrowRight');
  await expect(page.getByRole('tab', { name: '清零日志' })).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('tab', { name: '健康状态' }).click();
  await expect(page).toHaveURL(/\/__react\/admin\/health\?tab=health$/);
  await expect(page.locator('.health-service-table')).toBeVisible();
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
