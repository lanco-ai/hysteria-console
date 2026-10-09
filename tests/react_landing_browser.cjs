const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { expect } = require('@playwright/test');

const baseUrl = process.env.PREVIEW_BASE_URL;

const landing = {
  ts: '2026-07-18T12:00:00+08:00',
  revision: 'a'.repeat(64),
  nodes: [
    { id: 'hk-home-01', name: '香港家宽 01', exit_ip: '203.0.113.10', isp: 'HKBN', region: '香港', enabled: true, health: { status: 'healthy', observed_ip: '203.0.113.10', checked_at: '2026-07-18T11:50:00+08:00', error_code: '' } },
    { id: 'jp-home-02', name: '日本家宽 02', exit_ip: '198.51.100.22', isp: 'NTT', region: '东京', enabled: true, health: { status: 'unhealthy', observed_ip: '', checked_at: '2026-07-18T11:40:00+08:00', error_code: 'exit_ip_mismatch' } },
    { id: 'us-home-03', name: '美国家宽 03', exit_ip: '192.0.2.33', isp: 'Comcast', region: '洛杉矶', enabled: false },
  ],
  users: [
    { user: 'demo_alex', revision: 'b'.repeat(64), allowed_ids: ['hk-home-01'] },
    { user: 'must_change', revision: 'c'.repeat(64), allowed_ids: [] },
  ],
};

async function newPage(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, timezoneId: 'Asia/Shanghai' });
  await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
  return { context, page: await context.newPage() };
}

async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu', '--num-raster-threads=1', '--renderer-process-limit=2'] });
  const { context, page } = await newPage(browser);
  const requests = [];
  page.on('request', request => requests.push(new URL(request.url()).pathname));
  await page.goto(`${baseUrl}/__react/admin/landing-egresses`);
  await expect(page).toHaveTitle('路由与出口');
  await expect(page.getByRole('tab', { name: '家宽出口' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.sidebar-link[aria-current="page"]')).toHaveText('路由与出口');
  await expect(page.locator('.landing-node-list h2')).toContainText('家宽出口节点');
  await expect(page.locator('.landing-node-list .badge')).toHaveText('0 个节点');
  // Without nodes the page explains itself and offers one way to add the first.
  await expect(page.getByText('尚未配置家宽出口', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '+ 新增节点' })).toHaveCount(1);
  await expect(page.locator('.landing-access-section')).toContainText('添加家宽出口节点后，可在这里为用户授权');
  await expect(page.locator('.landing-access-row')).toHaveCount(0);
  const add = page.getByRole('button', { name: '+ 新增节点' });
  await add.click();
  const dialog = page.getByRole('dialog', { name: '新增家宽出口' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('button', { name: '保存节点' })).toBeVisible();
  await expect(dialog.locator('#landing-node-id')).not.toHaveAttribute('readonly', '');
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(add).toBeFocused();
  assert(requests.includes('/api/v1/admin/landing-egresses'));
  assert(!requests.includes('/admin/landing-egresses.json'));
  await context.close();

  const mocked = await newPage(browser);
  const posts = [];
  await mocked.page.route('**/api/v1/admin/landing-egresses', route => route.fulfill({ json: landing }));
  await mocked.page.route(/\/api\/v1\/admin\/landing-egresses\/(check|access|delete|save)$/, route => {
    const action = new URL(route.request().url()).pathname.split('/').pop();
    posts.push([action, new URLSearchParams(route.request().postData() || '')]);
    return route.fulfill({ json: { ok: true, action, revision: 'f'.repeat(64) } });
  });
  const cardsPage = mocked.page;
  await cardsPage.goto(`${baseUrl}/__react/admin/landing-egresses`);
  const cards = cardsPage.locator('.landing-card');
  await expect(cards).toHaveCount(3);
  await expect(cardsPage.locator('.landing-node-list .admin-section-header')).toContainText('2 个启用 · 1 个健康');
  // Node IDs keep their case; health reads as words, not status keys.
  await expect(cards.nth(0).locator('.landing-card-id')).toHaveText('hk-home-01');
  assert.equal(await cards.nth(0).locator('.landing-card-id').innerText(), 'hk-home-01');
  await expect(cards.nth(0).locator('.landing-health')).toContainText('健康 · 实测出口 203.0.113.10');
  await expect(cards.nth(0).locator('.landing-health')).toContainText('07-18 11:50 检查');
  await expect(cards.nth(1).locator('.landing-health')).toContainText('不可用 · 出口 IP 与预期不符');
  await expect(cards.nth(2).locator('.landing-health')).toHaveText('未检查');
  await expect(cards.nth(0)).toContainText('1 位用户');
  // Edit opens the dialog with the stored fields; the SOCKS5 address must be retyped.
  await cards.nth(0).getByRole('button', { name: '编辑' }).click();
  const edit = cardsPage.getByRole('dialog', { name: '编辑 香港家宽 01' });
  await expect(edit.locator('#landing-node-id')).toHaveValue('hk-home-01');
  await expect(edit.locator('#landing-node-id')).toHaveAttribute('readonly', '');
  await expect(edit.locator('#landing-node-name')).toHaveValue('香港家宽 01');
  await expect(edit.locator('#landing-socks-ip')).toHaveValue('');
  await expect(edit).toContainText('SOCKS5 IP 与端口不会回传到页面，需重新填写');
  await edit.getByRole('button', { name: '取消' }).click();
  await expect(edit).toHaveCount(0);
  // A dismissed delete confirmation sends nothing; a health check posts the node id.
  await cards.nth(1).getByRole('button', { name: '删除' }).click();
  await cards.nth(0).getByRole('button', { name: '健康检查' }).click();
  await expect(cardsPage.locator('.flash')).toHaveText('健康检查已完成');
  assert.deepEqual(posts.map(([action, body]) => [action, body.get('id')]), [['check', 'hk-home-01']]);
  // Access rows save only after a change; disabled nodes cannot be granted.
  const row = cardsPage.locator('.landing-access-row', { hasText: 'must_change' });
  const save = row.getByRole('button', { name: '保存授权' });
  await expect(save).toBeDisabled();
  await expect(row.getByRole('checkbox', { name: /美国家宽 03/ })).toBeDisabled();
  await row.getByText('香港家宽 01', { exact: true }).click();
  await expect(save).toBeEnabled();
  await save.click();
  await expect(cardsPage.locator('.flash')).toHaveText('用户授权已更新');
  const [, accessBody] = posts.find(([action]) => action === 'access');
  assert.equal(accessBody.get('user'), 'must_change');
  assert.deepEqual(accessBody.getAll('egress_id'), ['hk-home-01']);
  await cardsPage.setViewportSize({ width: 390, height: 844 });
  assert(await cardsPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no horizontal overflow at 390px');
  await mocked.context.close();
  await browser.close();
  console.log('React landing browser acceptance passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
