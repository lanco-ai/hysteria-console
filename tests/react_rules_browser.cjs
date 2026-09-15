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
  await page.goto(`${baseUrl}/__react/admin/rules`);
  await expect(page).toHaveTitle('路由规则');
  await expect(page.getByRole('heading', { name: '当前规则列表' })).toBeVisible();
  await page.getByText('直接编辑全部规则', { exact: true }).click();
  await expect(page.locator('#rules-raw')).toBeVisible();
  await expect(page.getByRole('button', { name: '覆盖全部规则' })).toBeDisabled();
  assert(requests.includes('/api/v1/admin/rules'));
  assert(!requests.includes('/admin/rules.fragment'));
  await context.close();
  await browser.close();
  console.log('React rules browser acceptance passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
