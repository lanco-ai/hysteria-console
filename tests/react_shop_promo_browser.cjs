const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

// 推广价 / 学生价, sold counts, views and the WeChat contact: set in 商品管理, shown on the storefront.
(async () => {
  const browser = await chromium.launch({ headless: true });
  const shots = process.env.REACT_SCREENSHOT_DIR;
  const shot = async (page, name) => {
    if (!shots) return;
    fs.mkdirSync(shots, { recursive: true });
    await page.screenshot({ path: path.join(shots, name), fullPage: true });
  };
  try {
    const base = process.env.PREVIEW_BASE_URL;
    const admin = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await admin.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const manager = await admin.newPage();
    await manager.goto(`${base}/admin/shop`);
    await expect(manager.getByLabel('售价 2:7')).toBeVisible();
    await manager.getByLabel('商家微信号').fill('upc-bh');
    await manager.getByLabel('售价 2:7').fill('133');
    await manager.getByLabel('原价 2:7').fill('153');
    await manager.getByLabel('上架 2:7').check();
    await manager.getByLabel('价格标签 2').selectOption('学生价');
    await expect(manager.locator('.shop-admin-product').filter({ has: manager.getByLabel('价格标签 2') }).locator('.shop-admin-display .shop-promo-badge')).toHaveText('学生价');
    await manager.getByLabel('已售件数 2').fill('36');
    // A 原价 that is not above the price is refused before anything is saved.
    await manager.getByLabel('售价 1:4').fill('50');
    await manager.getByLabel('原价 1:4').fill('40');
    await manager.getByRole('button', { name: '保存商品设置' }).click();
    await expect(manager.getByRole('alert')).toContainText('原价需高于售价');
    await manager.getByLabel('原价 1:4').fill('');
    await manager.getByLabel('上架 1:4').check();
    await manager.getByLabel('价格标签 1').selectOption('__custom');
    await manager.getByLabel('自定义标签 1').fill('开学特惠');
    await expect(manager.locator('.shop-admin-display.is-changed')).toHaveCount(2);
    await manager.getByRole('button', { name: '保存商品设置' }).click();
    await expect(manager.getByRole('status')).toContainText('已保存');
    const saved = await (await manager.request.get(`${base}/api/v1/shop/admin`)).json();
    assert.equal(saved.wechat, 'upc-bh');
    assert.deepEqual(saved.display, { 1: { badge: '开学特惠', sold: 0 }, 2: { badge: '学生价', sold: 36 } });
    assert.equal(saved.skus['2:7'].list_price_cents, 15300);
    assert.equal(saved.skus['1:4'].list_price_cents, undefined);
    // A hand-written label comes back as one after a reload.
    await manager.reload();
    await expect(manager.getByLabel('自定义标签 1')).toHaveValue('开学特惠');
    await expect(manager.getByLabel('价格标签 2')).toHaveValue('学生价');
    await expect(manager.getByLabel('原价 2:7')).toHaveValue('153.00');
    await shot(manager, 'shop-admin-promo.png');

    const shopper = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await shopper.grantPermissions(['clipboard-read', 'clipboard-write']);
    const page = await shopper.newPage();
    await page.goto(`${base}/`);
    const row = page.locator('.shop-product-row').filter({ hasText: 'ChatGPT Pro 200' });
    // The label leads the title; the 原价 is struck through with the saving beside it.
    await expect(row.locator('.shop-product-title > *').first()).toHaveClass('shop-promo-badge');
    await expect(row.locator('.shop-promo-badge')).toHaveText('学生价');
    await expect(row.locator('.shop-list-price s')).toHaveText('原价 ¥153.00');
    await expect(row.locator('.shop-list-price em')).toHaveText('省 ¥20.00');
    await expect(row.locator('.shop-product-stats')).toHaveText('已售 36 件');
    await expect(page.locator('.shop-product-row').filter({ hasText: 'ChatGPT Plus' }).locator('.shop-promo-badge')).toHaveText('开学特惠');
    await expect(page.locator('.shop-contact')).toContainText('upc-bh');
    await page.locator('.shop-contact').getByRole('button', { name: '复制' }).click();
    await expect(page.locator('.shop-contact').getByRole('button', { name: '已复制' })).toBeVisible();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'upc-bh');
    await shot(page, 'shop-storefront-promo.png');

    // Opening the product counts one view of it; the shop counts the visitor once a day.
    await row.getByRole('link', { name: 'ChatGPT Pro 200', exact: true }).click();
    await expect(page.locator('.shop-detail h1 .shop-promo-badge')).toHaveText('学生价');
    await expect(page.locator('.shop-detail-price')).toContainText('原价 ¥153.00');
    await expect(page.locator('.shop-detail-price')).toContainText('省 ¥20.00');
    const views = async () => (await (await page.request.get(`${base}/api/v1/shop/catalog`)).json());
    await expect.poll(async () => (await views()).products.find(product => product.id === '2').views).toBe(1);
    assert.deepEqual((await views()).stats, { views: 1, sold: 36 });
    await page.goto(`${base}/`);
    await expect(page.locator('.shop-store-stats')).toHaveText('累计浏览 1 人次 · 累计售出 36 件');
    await expect(row.locator('.shop-product-stats')).toHaveText('1 人看过 · 已售 36 件');
    assert.deepEqual((await views()).stats, { views: 1, sold: 36 });

    // With no Telegram set, WeChat is how a buyer reaches the merchant.
    await page.goto(`${base}/?product=2`);
    await page.getByRole('button', { name: '立即购买', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '购买信息' });
    await expect(dialog).toContainText('商家微信：upc-bh');
    await expect(dialog.getByRole('link', { name: '联系商家 Telegram' })).toHaveCount(0);
    await dialog.getByRole('button', { name: '复制微信号' }).click();
    await expect(dialog).toContainText('微信号 upc-bh 已复制');
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'upc-bh');
    await page.keyboard.press('Escape');

    // The merchant sees the same counts in 商品管理.
    await manager.reload();
    await expect(manager.locator('.shop-admin-stats')).toContainText('累计浏览 1 人次 · 累计售出 36 件');
    await expect(manager.locator('.shop-admin-product').filter({ has: manager.getByLabel('价格标签 2') }).locator('.shop-admin-product-meta')).toContainText('浏览 1');

    for (const width of [390, 768]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`${base}/`);
      await expect(row.locator('.shop-promo-badge')).toBeVisible();
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `storefront fits ${width}px`);
      await manager.setViewportSize({ width, height: 900 });
      assert.ok(await manager.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `商品管理 fits ${width}px`);
    }
    await shot(page, 'shop-storefront-promo-390.png');
    console.log('PASS: price labels and 原价 before the title, sold counts, daily views and the WeChat contact');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
