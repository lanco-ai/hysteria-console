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
  await expect(page).toHaveTitle('模板与路由');
  await expect(page.getByRole('heading', { name: '模板与路由' })).toBeVisible();
  await expect(page.locator('#config-editor')).toBeVisible();
  await expect(page.getByText(/下次拉取订阅生效 · 保存校验结构与版本 · 用户凭证由服务端注入/)).toBeVisible();
  await expect(page.getByText('模板说明与影响范围', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '格式化 JSON' }).click();
  await expect(page.getByRole('status')).toContainText('已格式化 JSON');
  assert(requests.includes('/api/v1/admin/config'));
  assert(!requests.includes('/admin/config.fragment'));
  assert.equal(await page.locator('.sidebar-nav a[href="/admin/rules"]').count(), 0);
  await page.getByRole('tab', { name: '路由规则', exact: true }).click();
  await expect(page).toHaveURL(/\/__react\/admin\/config\?tab=rules$/);
  await expect(page.getByRole('heading', { name: '当前规则列表' })).toBeVisible();
  assert(requests.includes('/api/v1/admin/rules'));
  await page.getByText('直接编辑全部规则', { exact: true }).click();
  await page.locator('#rules-raw').fill('MATCH,DIRECT');
  await page.getByRole('tab', { name: '订阅模板', exact: true }).click();
  await expect(page.locator('#config-editor')).toBeVisible();
  await page.getByRole('tab', { name: '路由规则', exact: true }).click();
  await expect(page.locator('#rules-raw')).toHaveValue('MATCH,DIRECT');
  await page.getByRole('tab', { name: '订阅模板', exact: true }).click();
  await page.locator('#config-editor').fill('{"draft":true}');
  await page.getByRole('tab', { name: '路由规则', exact: true }).click();
  await page.getByRole('tab', { name: '订阅模板', exact: true }).click();
  await expect(page.locator('#config-editor')).toHaveValue('{"draft":true}');
  await context.close();
  await browser.close();
  console.log('React config browser acceptance passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
