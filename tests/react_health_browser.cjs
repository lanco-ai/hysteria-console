const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { expect } = require('@playwright/test');

const baseUrl = process.env.PREVIEW_BASE_URL;

async function main() {
  const browser = await chromium.launch({
    headless: true,
    args: ['--disable-gpu', '--num-raster-threads=1', '--renderer-process-limit=2'],
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
  const page = await context.newPage();
  const requests = [];
  page.on('request', request => requests.push(new URL(request.url()).pathname));
  await page.goto(`${baseUrl}/__react/admin/health`);
  await expect(page).toHaveTitle('运维');
  await expect(page.getByRole('tab', { name: '健康状态' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.health-kpi-card')).toHaveCount(4);
  await expect(page.locator('.health-service-table tbody tr')).toHaveCount(15);
  await expect(page.locator('.health-radar-section tbody tr')).toHaveCount(3);
  await expect(page.locator('.health-calibrator-section tbody tr')).toHaveCount(3);
  await page.getByRole('button', { name: '立即刷新' }).click();
  await expect(page.locator('.health-kpi-card')).toHaveCount(4);
  await expect(page.getByRole('button', { name: '检查更新' })).toHaveCount(1);
  await expect(page.getByRole('button', { name: '立即更新' })).toHaveCount(1);
  assert(requests.includes('/api/v1/admin/health'));
  assert(!requests.includes('/admin/health.fragment'));
  await context.close();
  await browser.close();
  console.log('React health browser acceptance passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
