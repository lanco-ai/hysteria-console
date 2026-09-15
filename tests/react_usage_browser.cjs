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
  await page.goto(`${baseUrl}/__react/admin/usage`);
  await expect(page).toHaveTitle('流量分析');
  await expect(page.locator('.metric-card')).toHaveCount(4);
  await expect(page.locator('.usage-hourly-bar')).toHaveCount(168);
  await expect(page.locator('.usage-heatmap-row')).toHaveCount(7);
  await expect(page.locator('.usage-heatmap-cell')).toHaveCount(7 * 24);
  await page.locator('#usage-history summary').click();
  await expect(page.locator('.daily-table-collapsed')).toBeVisible();
  assert(requests.includes('/api/v1/admin/usage'));
  assert(requests.includes('/api/v1/admin/usage-history'));
  assert(!requests.includes('/admin/analytics.json'));
  assert(!requests.includes('/admin/usage-history'));
  await context.close();
  await browser.close();
  console.log('React usage browser acceptance passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });

