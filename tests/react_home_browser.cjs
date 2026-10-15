const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { expect } = require('@playwright/test');

const baseUrl = process.env.PREVIEW_BASE_URL;

// Shop navigation must stay inside the shop, for visitors and administrators.
async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const anonymousContext = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const anonymous = await anonymousContext.newPage();
    let privateRequests = 0;
    anonymous.on('request', request => {
      const pathname = new URL(request.url()).pathname;
      if (pathname.startsWith('/api/chat/') || pathname.startsWith('/api/video/') || pathname.startsWith('/api/plans')) privateRequests += 1;
    });
    const sessionResponse = anonymous.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/session');
    await anonymous.goto(`${baseUrl}/__react/`);
    assert.equal((await sessionResponse).status(), 401);
    await expect(anonymous).toHaveTitle('购物 · LancoAI');
    await expect(anonymous.locator('.sidebar')).toHaveCount(0);
    // AI tools stay in the console; the portal also lets customers redeem their SMS codes.
    await expect(anonymous.locator('nav[aria-label="主导航"] a')).toHaveText(['购物', '短信接码']);
    await expect(anonymous.getByRole('link', { name: '开源发现', exact: true })).toHaveCount(0);
    await expect(anonymous.getByRole('dialog')).toHaveCount(0);
    await expect(anonymous.getByRole('heading', { name: '暂无商品' })).toBeVisible();
    await anonymous.evaluate(() => { window.__portalMarker = 'same-document'; });
    await anonymous.getByRole('link', { name: '短信接码', exact: true }).click();
    await expect(anonymous).toHaveURL(`${baseUrl}/__react/?view=sms`);
    await expect(anonymous).toHaveTitle('短信接码 · LancoAI');
    await expect(anonymous.getByLabel('激活码', { exact: true })).toBeVisible();
    await expect(anonymous.getByRole('dialog')).toHaveCount(0);
    assert.equal(await anonymous.evaluate(() => window.__portalMarker), 'same-document');
    await anonymous.goBack();
    await expect(anonymous.getByRole('heading', { name: '暂无商品' })).toBeVisible();
    assert.equal(privateRequests, 0, 'the public portal must not call private chat or video APIs');

    for (const width of [390, 768, 1440]) {
      await anonymous.setViewportSize({ width, height: 900 });
      await anonymous.goto(`${baseUrl}/__react/`);
      await expect(anonymous.locator('.portal-nav')).toBeVisible();
      await expect(anonymous.getByText('创建账号', { exact: true })).toHaveCount(0);
      await expect(anonymous.getByText('订单查询', { exact: true })).toHaveCount(0);
      await expect(anonymous.getByText('公告', { exact: true })).toHaveCount(0);
      assert.equal(await anonymous.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, `${width}px must not overflow`);
      await anonymous.getByRole('searchbox', { name: '搜索商品' }).fill('test');
      await expect(anonymous.getByRole('heading', { name: '没有匹配的商品' })).toBeVisible();
      await anonymous.getByRole('button', { name: '清除搜索' }).click();
      await expect(anonymous.getByRole('heading', { name: '暂无商品' })).toBeVisible();
      if (process.env.REACT_HOME_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.REACT_HOME_SCREENSHOT_DIR, { recursive: true });
        await anonymous.screenshot({ path: path.join(process.env.REACT_HOME_SCREENSHOT_DIR, `portal-shop-${width}.png`), fullPage: true });
      }
    }
    assert.equal(privateRequests, 0, 'the public portal must not call private chat or video APIs');
    await anonymousContext.close();
    const adminContext = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await adminContext.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
    const admin = await adminContext.newPage();
    const foreignRequests = [];
    admin.on('request', request => {
      if (/^\/api\/(plans|chat|video)(\/|$)/.test(new URL(request.url()).pathname)) foreignRequests.push(request.url());
    });
    await admin.goto(`${baseUrl}/__react/`);
    await expect(admin.locator('.portal-account')).toHaveAttribute('href', '/admin/shop');
    await admin.locator('.portal-account').click();
    await expect(admin).toHaveURL(`${baseUrl}/__react/admin/shop`);
    await expect(admin.locator('.sidebar-nav .sidebar-link')).toHaveText(['商品管理', '短信接码']);
    await expect(admin.locator('.sidebar-footer a')).toHaveText(['商城首页']);
    await expect(admin.locator('.plans-global-reminders')).toHaveCount(0);
    assert.deepEqual(foreignRequests, [], 'the shop must not poll workbench features');
    await adminContext.close();
    console.log('PASS: shop-only portal, merchant navigation and no workbench polling');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
