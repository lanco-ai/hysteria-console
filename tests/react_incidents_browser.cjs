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
  await page.goto(`${baseUrl}/__react/admin/incidents`);
  await expect(page).toHaveTitle('事故处理');
  await expect(page.getByRole('heading', { name: '处置候选用户' })).toBeVisible();
  await expect(page.locator('.health-kpi-card')).toHaveCount(4);
  await expect(page.getByRole('link', { name: '下载证据 JSON' })).toHaveAttribute('href', '/api/v1/admin/incidents/evidence');
  assert(requests.some(([method, path]) => method === 'GET' && path === '/api/v1/admin/incidents'));
  assert(!requests.some(([, path]) => path === '/admin/incidents'));
  await context.close();
  await browser.close();
  console.log('React incidents browser acceptance passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
