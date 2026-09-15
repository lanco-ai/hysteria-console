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
  await page.goto(`${baseUrl}/__react/admin/config`);
  await expect(page).toHaveTitle('模板配置');
  await expect(page.locator('#config-editor')).toBeVisible();
  await page.getByRole('button', { name: '格式化 JSON' }).click();
  await expect(page.getByRole('status')).toContainText('已格式化 JSON');
  assert(requests.includes('/api/v1/admin/config'));
  assert(!requests.includes('/admin/config.fragment'));
  await context.close();
  await browser.close();
  console.log('React config browser acceptance passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
