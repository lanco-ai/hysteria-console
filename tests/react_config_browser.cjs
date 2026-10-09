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
  await expect(page).toHaveTitle('路由与出口');
  await expect(page.getByRole('heading', { name: '路由与出口' })).toBeVisible();
  await expect(page.getByRole('tab')).toHaveText(['订阅模板', '路由规则', '家宽出口']);
  await expect(page.locator('#config-editor')).toBeVisible();
  await expect(page.getByText(/下次拉取订阅生效 · 保存校验结构与版本 · 用户凭证由服务端注入/)).toBeVisible();
  await expect(page.getByText('模板说明与影响范围', { exact: true })).toHaveCount(0);
  // The overview beside the editor follows the draft; the status line validates it.
  const editor = await page.locator('.template-editor').boundingBox();
  const overview = await page.locator('.template-overview').boundingBox();
  assert(overview.x > editor.x + editor.width - 1, 'the overview sits beside the editor on a wide screen');
  await expect(page.locator('.template-overview')).toContainText('条规则');
  await expect(page.locator('#template-status')).toContainText('JSON 有效');
  const lines = await page.locator('#config-editor').inputValue().then(value => value.split('\n').length);
  assert.equal(await page.locator('.code-gutter').evaluate(node => node.textContent), Array.from({ length: lines }, (_, index) => index + 1).join('\n'));
  await page.locator('.template-jump', { hasText: 'rules' }).click();
  await expect(page.locator('#config-editor')).toBeFocused();
  const caretLine = await page.locator('#config-editor').evaluate(area => area.value.slice(0, area.selectionStart).split('\n').length);
  const rulesLine = await page.locator('#config-editor').evaluate(area => area.value.split('\n').findIndex(line => line.startsWith('  "rules"')) + 1);
  assert.equal(caretLine, rulesLine, 'the jump chip moves the caret to the rules key');
  await page.getByRole('button', { name: '格式化 JSON' }).click();
  await expect(page.getByRole('status')).toContainText('已格式化 JSON');
  await page.locator('#config-editor').fill('{"draft":');
  await expect(page.locator('#template-status')).toContainText('语法错误');
  await expect(page.getByRole('button', { name: '保存订阅模板' })).toBeDisabled();
  await expect(page.locator('.template-overview')).toContainText('草稿无效，显示已保存版本');
  await page.getByRole('button', { name: '放弃草稿' }).click();
  await expect(page.locator('#template-status')).toContainText('JSON 有效');
  assert(requests.includes('/api/v1/admin/config'));
  assert(!requests.includes('/admin/config.fragment'));
  assert.equal(await page.locator('.sidebar-nav a[href="/admin/rules"]').count(), 0);
  // The overview links straight to the rules tab.
  await page.getByRole('button', { name: '在「路由规则」中管理 →' }).click();
  await expect(page).toHaveURL(/\/__react\/admin\/config\?tab=rules$/);
  await expect(page.getByRole('heading', { name: '当前规则列表' })).toBeVisible();
  assert(requests.includes('/api/v1/admin/rules'));
  await page.getByRole('button', { name: '文本编辑' }).click();
  await page.locator('#rules-raw').fill('MATCH,DIRECT');
  await page.getByRole('tab', { name: '订阅模板', exact: true }).click();
  await expect(page.locator('#config-editor')).toBeVisible();
  await page.getByRole('tab', { name: '路由规则', exact: true }).click();
  await expect(page.locator('#rules-raw')).toHaveValue('MATCH,DIRECT');
  await page.getByRole('tab', { name: '订阅模板', exact: true }).click();
  await page.locator('#config-editor').fill('{"draft":true}');
  await expect(page.locator('#template-status')).toContainText('缺少 proxies、proxy-groups、rules');
  await expect(page.locator('.template-editor .badge')).toHaveText('未保存');
  await page.getByRole('tab', { name: '路由规则', exact: true }).click();
  await page.getByRole('tab', { name: '订阅模板', exact: true }).click();
  await expect(page.locator('#config-editor')).toHaveValue('{"draft":true}');
  // 家宽出口 joined as the third tab and keeps its own address.
  await page.getByRole('tab', { name: '家宽出口', exact: true }).click();
  await expect(page).toHaveURL(/\/__react\/admin\/landing-egresses$/);
  await expect(page.getByRole('button', { name: '+ 新增节点' })).toBeVisible();
  assert(requests.includes('/api/v1/admin/landing-egresses'));
  await page.getByRole('tab', { name: '订阅模板', exact: true }).click();
  await expect(page.locator('#config-editor')).toHaveValue('{"draft":true}');
  await page.goBack();
  await expect(page.getByRole('tab', { name: '家宽出口', exact: true })).toHaveAttribute('aria-selected', 'true');
  // On a phone the editor and overview stack without horizontal overflow.
  await page.getByRole('tab', { name: '订阅模板', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  const stackedEditor = await page.locator('.template-editor').boundingBox();
  const stackedOverview = await page.locator('.template-overview').boundingBox();
  assert(stackedOverview.y > stackedEditor.y + stackedEditor.height - 1, 'the overview follows the editor on a phone');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no horizontal overflow at 390px');
  await context.close();
  await browser.close();
  console.log('React config browser acceptance passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
