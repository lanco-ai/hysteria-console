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
  await expect(page).toHaveTitle('模板与路由');
  await expect(page.getByRole('heading', { name: '模板与路由' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '当前规则列表' })).toBeVisible();
  const userSelect = page.locator('#rule-pack-user');
  const scopeSelect = page.locator('#rule-pack-scope');
  const userHelp = page.locator('#rule-pack-user-help');
  await expect(userSelect).toBeDisabled();
  await expect(userHelp).toContainText('选择“单个用户”后可选择用户');
  await scopeSelect.selectOption('user');
  await expect(userSelect).toBeEnabled();
  await expect(userHelp).toContainText('位用户可选');
  const userOptions = await userSelect.locator('option').evaluateAll(options => options.map(option => option.value).filter(Boolean));
  assert(userOptions.length > 0);
  await userSelect.selectOption(userOptions[0]);
  await expect(page.getByRole('button', { name: '应用规则包' })).toBeEnabled();
  await scopeSelect.selectOption('global');
  await expect(userSelect).toBeDisabled();
  await expect(userSelect).toHaveValue('');
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
