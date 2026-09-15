const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { expect } = require('@playwright/test');

const baseUrl = process.env.PREVIEW_BASE_URL;

async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu', '--num-raster-threads=1', '--renderer-process-limit=2'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addCookies([{ name: 'usid', value: process.env.REACT_PREVIEW_PASSWORD_USER_COOKIE, url: baseUrl }]);
  const page = await context.newPage();
  const requests = [];
  page.on('request', request => requests.push(new URL(request.url()).pathname));
  await page.goto(`${baseUrl}/__react/user/panel`);
  await expect(page).toHaveTitle('用户面板 · Hysteria');
  await expect(page.getByText('个人控制台', { exact: true })).toBeVisible();
  await expect(page.getByText('本周期用量', { exact: true })).toBeVisible();
  await expect(page.getByText('订阅链接', { exact: true })).toBeVisible();
  await expect(page.locator('.usage-kpi')).toHaveCount(3);
  await expect(page.getByRole('button', { name: '复制链接' })).toBeVisible();
  assert(requests.includes('/api/v1/user/panel'));
  assert(!requests.includes('/user/panel.json'));
  await context.close();

  const lifecycleContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await lifecycleContext.addCookies([{ name: 'usid', value: process.env.REACT_PREVIEW_MUST_CHANGE_COOKIE, url: baseUrl }]);
  const lifecyclePage = await lifecycleContext.newPage();
  await lifecyclePage.goto(`${baseUrl}/__react/user/panel`);
  await expect(lifecyclePage).toHaveURL(/\/user\/change-password$/);
  await lifecycleContext.close();
  await browser.close();
  console.log('React user panel browser acceptance passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
