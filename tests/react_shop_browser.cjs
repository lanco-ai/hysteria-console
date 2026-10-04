const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { expect } = require('@playwright/test');
const base = process.env.PREVIEW_BASE_URL;

async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const context = await browser.newContext();
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    const page = await context.newPage();
    await page.goto(`${base}/`);
    await expect(page.getByRole('heading', { name: '暂无商品' })).toBeVisible();
    const admin = await browser.newContext();
    await admin.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const manager = await admin.newPage();
    await manager.goto(`${base}/admin/shop`);
    await expect(manager.getByRole('heading', { name: /^商品管理/ })).toBeVisible();
    await expect(manager.locator('.shop-admin-product')).toHaveCount(5);
    await expect(manager.locator('.shop-admin-product h2')).toHaveCount(5);
    await expect(manager.locator('.shop-admin-row')).toHaveCount(7);
    await expect(manager.locator('.shop-admin-product').first().locator('.shop-admin-row')).toHaveCount(2);
    await expect(manager.locator('.topbar-right').getByRole('button', { name: '刷新来源' })).toBeVisible();
    await expect(manager.locator('.topbar-right').getByRole('button', { name: '重新读取' })).toBeVisible();
    await expect(manager.locator('.shop-admin-savebar').getByRole('button', { name: '保存商品设置' })).toBeVisible();
    await expect(manager.getByText('独立售价 · 人工确认与交付')).toHaveCount(0);
    await expect(manager.getByText('成本仅管理员可见。新规格默认草稿，填写售价并勾选上架后才会展示给客户。来源刷新不会覆盖售价。')).toHaveCount(0);
    await expect(manager.getByLabel('售价 2:7')).toBeVisible();
    const adminSnapshot = await (await manager.request.get(`${base}/api/v1/shop/admin`)).json();
    const sameTitleItem = { ...adminSnapshot.items[0], product_id: '99', key: '99:1' };
    await manager.route('**/api/v1/shop/admin', route => route.request().method() === 'GET' ? route.fulfill({ json: { ...adminSnapshot, items: [...adminSnapshot.items, sameTitleItem] } }) : route.continue());
    await manager.getByRole('button', { name: '重新读取' }).click();
    await expect(manager.locator('.shop-admin-product')).toHaveCount(6);
    await expect(manager.locator('.shop-admin-row')).toHaveCount(8);
    await expect(manager.getByLabel('售价 99:1')).toBeVisible();
    await manager.unroute('**/api/v1/shop/admin');
    await manager.getByRole('button', { name: '重新读取' }).click();
    await expect(manager.locator('.shop-admin-product')).toHaveCount(5);
    await manager.getByLabel('售价 2:7').focus();
    await manager.keyboard.press('Tab');
    await expect(manager.getByLabel('上架 2:7')).toBeFocused();
    await manager.getByLabel('商家 Telegram 用户名').fill('shop_owner');
    await manager.getByLabel('售价 2:7').fill('1100.01');
    await expect(manager.getByLabel('售价 2:2')).toHaveValue('');
    await manager.getByLabel('上架 2:7').check();
    await manager.getByLabel('售价 2:2').fill('1200.02');
    await manager.getByLabel('上架 2:2').check();
    await manager.getByLabel('售价 1:4').fill('130.00');
    await manager.getByLabel('上架 1:4').check();
    await manager.getByRole('button', { name: '保存商品设置' }).focus();
    await manager.keyboard.press('Enter');
    await expect(manager.getByRole('status')).toContainText('已保存');
    const response = await page.request.get(`${base}/api/v1/shop/catalog`);
    assert.equal(response.status(), 200);
    const publicText = await response.text();
    assert.equal(/cost_cents|qiangyunai|source_url|cnadsiuvhga/.test(publicText), false);
    for (const width of [390, 719, 768, 1440]) {
      await manager.setViewportSize({ width, height: 900 });
      assert.equal(await manager.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await manager.getByLabel('售价 1:4').scrollIntoViewIfNeeded();
      await manager.getByLabel('售价 1:4').focus();
      const focusPlacement = await manager.evaluate(() => {
        const field = document.activeElement;
        const bar = document.querySelector('.shop-admin-savebar');
        return field instanceof HTMLElement && bar instanceof HTMLElement ? { fieldBottom: field.getBoundingClientRect().bottom, barTop: bar.getBoundingClientRect().top } : null;
      });
      assert.ok(focusPlacement && focusPlacement.fieldBottom <= focusPlacement.barTop, JSON.stringify({ width, focusPlacement }));
      await manager.evaluate(() => scrollTo(0, 0));
      const saveClearance = await manager.evaluate(() => {
        const save = document.querySelector('.shop-admin-savebar button');
        const launcher = document.querySelector('.lanco-agent-launcher');
        return save instanceof HTMLElement && launcher instanceof HTMLElement ? {
          saveRight: save.getBoundingClientRect().right,
          launcherLeft: launcher.getBoundingClientRect().left,
        } : null;
      });
      assert.ok(saveClearance && saveClearance.saveRight <= saveClearance.launcherLeft, JSON.stringify({ width, saveClearance }));
      if (process.env.REACT_SHOP_SCREENSHOT_DIR) {
        await manager.screenshot({ path: path.join(process.env.REACT_SHOP_SCREENSHOT_DIR, `admin-overview-${width}.png`) });
        await manager.screenshot({ path: path.join(process.env.REACT_SHOP_SCREENSHOT_DIR, `admin-shop-${width}.png`), fullPage: true });
      }
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`${base}/`);
      await expect(page.getByRole('heading', { name: 'ChatGPT Pro 200' })).toBeVisible();
      await expect(page.getByRole('button', { name: '已售罄' })).toBeDisabled();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      const shots = process.env.REACT_SHOP_SCREENSHOT_DIR;
      if (shots) {
        fs.mkdirSync(shots, { recursive: true });
        await page.screenshot({ path: path.join(shots, `shop-${width}.png`), fullPage: true });
      }
      await page.getByRole('button', { name: '购买', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: '购买信息' });
      await expect(dialog).toBeVisible();
      await dialog.getByLabel('商品规格').selectOption('2:2');
      await dialog.getByLabel('购买数量').fill('3');
      await expect(dialog.getByTestId('purchase-total')).toHaveText('¥3600.06');
      await expect(dialog.getByRole('link', { name: '联系商家 Telegram' })).toHaveAttribute('href', 'https://t.me/shop_owner');
      await expect(dialog).toContainText('待商家人工确认');
      await dialog.getByRole('button', { name: '复制购买信息' }).click();
      assert.match(await page.evaluate(() => navigator.clipboard.readText()), /合计：¥3600.06 CNY/);
      await dialog.getByLabel('购买数量').fill('0');
      await expect(dialog.getByRole('link', { name: '联系商家 Telegram' })).toHaveCount(0);
      await dialog.getByLabel('购买数量').fill('3');
      if (shots) await page.screenshot({ path: path.join(shots, `purchase-${width}.png`), fullPage: true });
      await page.keyboard.press('Escape');
      await expect(dialog).toHaveCount(0);
      await expect(page.getByRole('button', { name: '购买', exact: true })).toBeFocused();
    }
    await page.getByRole('searchbox', { name: '搜索商品' }).fill('nothing');
    await expect(page.getByRole('heading', { name: '没有匹配的商品' })).toBeVisible();
    await manager.getByLabel('商家 Telegram 用户名').fill('');
    await manager.getByRole('button', { name: '保存商品设置' }).click();
    await expect(manager.getByRole('status')).toContainText('已保存');
    await page.goto(`${base}/`);
    await page.getByRole('button', { name: '购买', exact: true }).click();
    await expect(page.getByRole('dialog')).toContainText('商家尚未配置联系方式');
    await expect(page.getByRole('link', { name: '联系商家 Telegram' })).toHaveCount(0);
    await page.keyboard.press('Escape');
    await manager.getByLabel('售价 2:7').fill('999.99');
    const changedCost = { ...adminSnapshot, items: adminSnapshot.items.map(item => item.key === '2:7' ? { ...item, cost_cents: item.cost_cents + 200 } : item) };
    await manager.route('**/api/v1/shop/refresh', route => route.fulfill({ json: changedCost }));
    await manager.getByRole('button', { name: '刷新来源' }).click();
    await expect(manager.getByRole('status')).toContainText('售价和未保存输入已保留');
    await expect(manager.getByLabel('售价 2:7')).toHaveValue('999.99');
    await expect(manager.locator('.shop-admin-product').first()).toContainText('¥1002.00');
    await manager.unroute('**/api/v1/shop/refresh');
    await manager.route('**/api/v1/shop/admin', route => route.request().method() === 'PUT' ? route.fulfill({ status: 409, json: { error: 'revision_conflict' } }) : route.continue());
    await manager.getByRole('button', { name: '保存商品设置' }).click();
    await expect(manager.getByRole('alert')).toContainText('设置已被其他页面修改');
    await expect(manager.getByLabel('售价 2:7')).toHaveValue('999.99');
    const stale = JSON.parse(publicText);
    stale.status = 'stale';
    for (const product of stale.products) for (const variant of product.variants) variant.available = false;
    await page.route('**/api/v1/shop/catalog', route => route.fulfill({ json: stale }));
    await page.reload();
    await expect(page.getByText('商品信息正在等待更新，暂时无法购买，请稍后再试。')).toBeVisible();
    await expect(page.getByRole('button', { name: '购买', exact: true })).toHaveCount(0);
    await page.unroute('**/api/v1/shop/catalog');
    await page.route('**/api/v1/shop/catalog', route => route.fulfill({ status: 503, json: { error: 'storage_unavailable' } }));
    await page.reload();
    await expect(page.getByRole('alert')).toContainText('商品暂时无法加载');
    assert.equal((await manager.goto(`${base}/admin/shop/unknown`)).status(), 404);
    await context.close();
    await admin.close();
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
