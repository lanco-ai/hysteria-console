const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { expect } = require('@playwright/test');

const baseUrl = process.env.PREVIEW_BASE_URL;

const mockedRules = [
  'AND,((NETWORK,UDP),(DST-PORT,3478)),🔒 WebRTC 隐私',
  'DOMAIN-SUFFIX,openai.com,🤖 GPT 优化',
  'IP-CIDR,91.108.4.0/22,✈️ Telegram 优化,no-resolve',
  'DOMAIN-SUFFIX,baidu.com,DIRECT',
  'RULE-SET,private,DIRECT',
  'GEOIP,CN,DIRECT',
  'MATCH,🚀 节点选择',
];

async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu', '--num-raster-threads=1', '--renderer-process-limit=2'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
  const page = await context.newPage();
  const requests = [];
  page.on('request', request => requests.push(new URL(request.url()).pathname));
  await page.goto(`${baseUrl}/__react/admin/rules`);
  await expect(page).toHaveTitle('路由与出口');
  await expect(page.getByRole('heading', { name: '路由与出口' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '当前规则列表' })).toBeVisible();
  // The list and the add / pack forms sit side by side on a wide screen.
  const list = await page.locator('.rules-list-section').boundingBox();
  const side = await page.locator('.rules-side').boundingBox();
  assert(side.x > list.x + list.width - 1, 'the forms column sits to the right of the list');
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
  // The raw editor is the text view of the same list.
  await page.getByRole('button', { name: '文本编辑' }).click();
  await expect(page.locator('#rules-raw')).toBeVisible();
  await expect(page.locator('.rule-list')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '覆盖全部规则' })).toBeDisabled();
  await page.locator('#rules-raw').fill('MATCH,DIRECT');
  await expect(page.getByRole('button', { name: '覆盖全部规则' })).toBeEnabled();
  await expect(page.locator('.segmented-dot')).toHaveCount(1);
  await page.getByRole('button', { name: '放弃草稿' }).click();
  await page.getByRole('button', { name: '列表', exact: true }).click();
  await expect(page.locator('.rule-row')).toHaveCount(1);
  assert(requests.includes('/api/v1/admin/rules'));
  assert(!requests.includes('/admin/rules.fragment'));
  await context.close();

  // A production-shaped list: logical and MATCH rules keep their policy, built-in rules cannot be deleted.
  const mocked = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await mocked.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
  const rulesPage = await mocked.newPage();
  await rulesPage.route('**/api/v1/admin/rules', route => route.fulfill({ json: { rules: mockedRules, revision: 'e'.repeat(64), packs: [], users: [] } }));
  await rulesPage.goto(`${baseUrl}/__react/admin/rules`);
  const rows = rulesPage.locator('.rule-row');
  await expect(rows).toHaveCount(mockedRules.length);
  await expect(rows.nth(0).locator('.rule-type')).toHaveText('AND');
  await expect(rows.nth(0).locator('.rule-pattern')).toHaveText('((NETWORK,UDP),(DST-PORT,3478))');
  await expect(rows.nth(0).locator('.rule-policy')).toHaveText('🔒 WebRTC 隐私');
  await expect(rows.nth(2).locator('.rule-pattern')).toHaveText('91.108.4.0/22no-resolve');
  await expect(rows.nth(2).locator('.rule-policy')).toHaveText('✈️ Telegram 优化');
  await expect(rows.nth(6).locator('.rule-pattern')).toHaveText('其余全部流量');
  await expect(rows.nth(6).locator('.rule-policy')).toHaveText('🚀 节点选择');
  await expect(rulesPage.getByRole('button', { name: /^删除第/ })).toHaveCount(4);
  await expect(rows.nth(4).locator('.rule-op')).toHaveText('内置');
  await rulesPage.locator('#rules-search').fill('openai');
  await expect(rows).toHaveCount(1);
  await expect(rows.first().locator('.rule-index')).toHaveText('2');
  await expect(rulesPage.locator('.rules-match-count')).toHaveText('显示 1 / 7');
  await rulesPage.locator('#rules-search').fill('');
  await rulesPage.locator('#rules-policy').selectOption('DIRECT');
  await expect(rows).toHaveCount(3);
  await rulesPage.locator('#rules-policy').selectOption('');
  // On a phone the add-rule form comes first and rows stay inside the screen.
  await rulesPage.setViewportSize({ width: 390, height: 844 });
  const form = await rulesPage.locator('.rules-side').boundingBox();
  const listBox = await rulesPage.locator('.rules-list-section').boundingBox();
  assert(form.y < listBox.y, 'forms sit above the list on a phone');
  assert(await rulesPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no horizontal overflow at 390px');
  await mocked.close();
  await browser.close();
  console.log('React rules browser acceptance passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
