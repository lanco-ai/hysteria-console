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
  page.on('request', request => requests.push(new URL(request.url()).pathname));
  await page.goto(`${baseUrl}/__react/admin/landing-egresses`);
  await expect(page).toHaveTitle('家宽出口');
  await expect(page.getByText('家宽出口节点', { exact: true })).toBeVisible();
  await expect(page.getByText('尚未配置家宽出口', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '保存节点' })).toBeVisible();
  assert(requests.includes('/api/v1/admin/landing-egresses'));
  assert(!requests.includes('/admin/landing-egresses.json'));
  await context.close();
  await browser.close();
  console.log('React landing browser acceptance passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
