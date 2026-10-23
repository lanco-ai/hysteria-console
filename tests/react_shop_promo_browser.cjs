const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

// 优惠价 (学生价, 限时价…) leading a card's price, under the regular price on the product page, sold counts, views and the WeChat contact:
// set in 商品管理, shown on the storefront, the product page, the cart and the purchase message.
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
    const saves = [];
    manager.on('request', request => { if (request.method() === 'PUT' && request.url().endsWith('/api/v1/shop/admin')) saves.push(request.postDataJSON()); });
    await manager.goto(`${base}/admin/shop`);
    await expect(manager.getByLabel('售价 2:7')).toBeVisible();
    await manager.getByLabel('商家微信号').fill('upc-bh');
    // 售价 stays the regular price; the 优惠价 is the lower one for buyers it applies to.
    await manager.getByLabel('售价 2:7').fill('1288');
    await manager.getByLabel('优惠价 2:7').fill('1188');
    await manager.getByLabel('上架 2:7').check();
    await manager.getByLabel('售价 2:2').fill('1388');
    await manager.getByLabel('上架 2:2').check();
    await expect(manager.locator('.shop-admin-row').filter({ has: manager.getByLabel('售价 2:7') }).locator('.shop-admin-margin')).toContainText('优惠 ¥188.00');
    const settings = manager.getByRole('button', { name: '商品设置 2', exact: true });
    await expect(settings).toHaveAttribute('aria-expanded', 'false');
    // Units sold sit with the label and the 新品 mark in the product's settings, not on every product.
    await expect(manager.getByLabel('已售件数 2', { exact: true })).toHaveCount(0);
    await settings.click();
    await expect(settings).toHaveAttribute('aria-expanded', 'true');
    await manager.getByLabel('已售件数 2', { exact: true }).fill('36');
    await expect(manager.getByRole('group', { name: '商品销量 2', exact: true })).toContainText('自动记录 0 件 · 合计 36 件');
    await manager.getByLabel('优惠标签 2').selectOption('学生价');
    // 新品 for two weeks: the product's header says so before saving.
    await manager.getByLabel('新品标记 2').selectOption('14');
    await expect(manager.getByLabel('新品标记 2')).toHaveValue('keep');
    await expect(manager.getByLabel('新品标记 2').locator('option:checked')).toHaveText(/^新品至 \d+\/\d+$/);
    const markedAt = Date.now() / 1000;
    const product2 = manager.locator('.shop-admin-product').filter({ has: manager.getByLabel('优惠标签 2') });
    await expect(product2.locator('.shop-admin-product-flags .shop-promo-badge')).toHaveText('学生价');
    // The label also names the 优惠价 column of the product's rows on a phone.
    await expect(product2.locator('.shop-admin-promo .shop-admin-mobile-label').first()).toHaveText('学生价');
    // An 优惠价 that is not below the price is refused before anything is saved.
    await manager.getByLabel('售价 1:4').fill('150');
    await manager.getByLabel('优惠价 1:4').fill('160');
    await manager.getByRole('button', { name: '保存商品设置' }).click();
    await expect(manager.getByRole('alert')).toContainText('优惠价需低于售价');
    assert.equal(saves.length, 0);
    await manager.getByLabel('优惠价 1:4').fill('');
    await manager.getByLabel('上架 1:4').check();
    await manager.getByRole('button', { name: '商品设置 1', exact: true }).click();
    await manager.getByLabel('优惠标签 1').selectOption('__custom');
    await manager.getByLabel('自定义标签 1').fill('开学特惠');
    await expect(manager.locator('.shop-admin-product-toggle.is-changed')).toHaveCount(2);
    // The announcement starts from the built-in note; the merchant writes their own.
    await manager.getByRole('button', { name: '编辑公告', exact: true }).click();
    await expect(manager.getByLabel('公告内容', { exact: true })).toHaveValue(/^主营 AI 会员与额度/);
    await manager.getByLabel('公告内容', { exact: true }).fill('主营 AI 会员，其他商品没上架的直接问我。');
    await manager.getByLabel('还可代购的商品', { exact: true }).fill('YouTube Premium、Gmail 账号\nCapCut 剪映，Gmail 账号');
    await expect(manager.locator('.shop-admin-announcement-summary')).toContainText('还可代购 3 项');
    await expect(manager.locator('.shop-admin-announcement .shop-admin-source-dirty')).toHaveCount(1);
    await manager.getByRole('button', { name: '保存商品设置' }).click();
    await expect(manager.getByRole('status')).toContainText('已保存');
    assert.equal(saves.length, 1);
    assert.equal(saves[0].client, 3);
    assert.deepEqual(saves[0].skus['2:7'], { price_cents: 128800, published: true, promo_price_cents: 118800 });
    const saved = await (await manager.request.get(`${base}/api/v1/shop/admin`)).json();
    assert.equal(saved.wechat, 'upc-bh');
    const newUntil = saved.display[2].new_until;
    assert.ok(Math.abs(newUntil - (markedAt + 14 * 86400)) < 120, '新品 lasts 14 days from marking');
    assert.deepEqual(saved.display, { 1: { badge: '开学特惠', sold: 0 }, 2: { badge: '学生价', sold: 36, new_until: newUntil } });
    assert.deepEqual(saved.announcement, { text: '主营 AI 会员，其他商品没上架的直接问我。', items: ['YouTube Premium', 'Gmail 账号', 'CapCut 剪映'] });
    assert.deepEqual(saved.skus['2:7'], { price_cents: 128800, published: true, promo_price_cents: 118800 });
    assert.equal(saved.skus['1:4'].promo_price_cents, undefined);
    // After a reload the settings come back, a hand-written label as one.
    await manager.reload();
    await expect(manager.getByLabel('售价 2:7')).toHaveValue('1288.00');
    await expect(manager.getByLabel('优惠价 2:7')).toHaveValue('1188.00');
    await expect(manager.locator('#shop-admin-settings-2')).toHaveCount(0);
    await expect(manager.locator('.shop-admin-product').filter({ has: manager.getByRole('button', { name: '商品设置 2', exact: true }) }).locator('.shop-admin-product-flags .shop-new-badge')).toHaveText('新品');
    await manager.getByRole('button', { name: '商品设置 1', exact: true }).click();
    await expect(manager.getByLabel('自定义标签 1')).toHaveValue('开学特惠');
    await manager.getByRole('button', { name: '商品设置 2', exact: true }).click();
    await expect(manager.getByLabel('优惠标签 2')).toHaveValue('学生价');
    await expect(manager.getByLabel('已售件数 2')).toHaveValue('36');
    await expect(manager.getByLabel('新品标记 2')).toHaveValue('keep');
    await shot(manager, 'shop-admin-promo.png');

    const shopper = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await shopper.grantPermissions(['clipboard-read', 'clipboard-write']);
    const page = await shopper.newPage();
    await page.goto(`${base}/`);
    const row = page.locator('.shop-product-row').filter({ hasText: 'ChatGPT Pro 200' });
    // The label leads the price; the 学生价 is the price shown, in red, with the regular price struck after it.
    await expect(row.locator('.shop-product-title .shop-promo-badge')).toHaveCount(0);
    await expect(row.locator('.shop-product-price > *').first()).toHaveClass('shop-promo-badge');
    await expect(row.locator('.shop-promo-badge')).toHaveText('学生价');
    await expect(row.locator('.shop-product-price strong')).toHaveText('¥1188.00');
    await expect(row.locator('.shop-product-price strong')).toHaveClass('is-promo');
    await expect(row.locator('.shop-product-from')).toHaveText('起');
    await expect(row.locator('.shop-product-price s')).toHaveText('原价 ¥1288.00');
    await expect(row.locator('.shop-product-stats')).toHaveText('已售 36 件');
    // The 新品 gets its tab, leads its category and has a filter of its own.
    await expect(row).toHaveClass(/is-new/);
    await expect(row.locator('.shop-new-badge')).toHaveText('新品');
    await expect(page.locator('.shop-category-group[data-category="GPT"] .shop-product-row').first()).toContainText('ChatGPT Pro 200');
    await expect(page.getByRole('button', { name: /^新品/ })).toContainText('1');
    // The merchant's announcement sits above the products.
    const announcement = page.getByRole('region', { name: '店铺公告' });
    await expect(announcement).toContainText('主营 AI 会员，其他商品没上架的直接问我。');
    await expect(announcement.getByRole('listitem')).toHaveText(['YouTube Premium', 'Gmail 账号', 'CapCut 剪映']);
    const plus = page.locator('.shop-product-row').filter({ hasText: 'ChatGPT Plus' });
    await expect(plus.locator('.shop-promo-badge')).toHaveText('开学特惠');
    await expect(plus.locator('.shop-product-price s')).toHaveCount(0);
    await expect(page.locator('.shop-hero .shop-contact')).toContainText('upc-bh');
    await page.locator('.shop-contact').getByRole('button', { name: '复制' }).click();
    await expect(page.locator('.shop-contact').getByRole('button', { name: '已复制' })).toBeVisible();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'upc-bh');
    await shot(page, 'shop-storefront-promo.png');

    // Opening the product counts one view of it; the shop counts the visitor once a day.
    await row.getByRole('link', { name: 'ChatGPT Pro 200｜菲律宾区', exact: true }).click();
    await expect(page.locator('.shop-detail h1 .shop-promo-badge')).toHaveText('学生价');
    await expect(page.locator('.shop-detail-price')).toContainText('售价¥1288.00');
    await expect(page.locator('.shop-detail-price .shop-promo-price')).toHaveText('学生价：¥1188.00');
    await page.getByLabel('购买数量', { exact: true }).fill('2');
    await expect(page.getByTestId('purchase-total')).toHaveText('¥2576.00');
    await expect(page.locator('.shop-total .shop-promo-total')).toHaveText('学生价合计 ¥2376.00');
    // The other variant has no 优惠价: keep an empty total slot to avoid a layout jump.
    await page.getByRole('button', { name: '续费卡密不可新开', exact: true }).click();
    await expect(page.locator('.shop-detail-price .shop-promo-price')).toHaveCount(0);
    await expect(page.locator('.shop-total .shop-promo-total')).toHaveText('');
    await expect(page.locator('.shop-total .shop-promo-total')).toBeVisible();
    const views = async () => (await (await page.request.get(`${base}/api/v1/shop/catalog`)).json());
    await expect.poll(async () => (await views()).products.find(product => product.id === '2').views).toBe(1);
    assert.deepEqual((await views()).stats, { views: 1, sold: 36 });
    await page.goto(`${base}/`);
    await expect(page.getByRole('group', { name: '店铺数据' }).locator('.shop-store-stat')).toHaveText(['累计浏览 1 人次', '累计售出 36 件']);
    await expect(row.locator('.shop-product-stats')).toHaveText('1 人看过 · 已售 36 件');
    assert.deepEqual((await views()).stats, { views: 1, sold: 36 });

    // With no Telegram set, WeChat is how a buyer reaches the merchant; the message carries the 学生价.
    await page.goto(`${base}/?product=2`);
    await page.getByLabel('购买数量', { exact: true }).fill('2');
    await page.getByRole('button', { name: '立即购买', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '购买信息' });
    await expect(dialog).toContainText('商家微信：upc-bh');
    const message = dialog.getByLabel('可复制的购买信息');
    await expect(message).toHaveValue(/单价：¥1288\.00（学生价 ¥1188\.00） · 小计：¥2576\.00/);
    await expect(message).toHaveValue(/合计：¥2576\.00 CNY\n学生价合计：¥2376\.00 CNY（需符合条件，以商家确认为准）/);
    await expect(dialog.getByRole('link', { name: '联系商家 Telegram' })).toHaveCount(0);
    await dialog.getByRole('button', { name: '复制微信号' }).click();
    await expect(dialog).toContainText('微信号 upc-bh 已复制');
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'upc-bh');
    await page.keyboard.press('Escape');

    // The cart shows the 学生价 of its line and the total at it.
    await page.getByRole('button', { name: '加入购物车', exact: true }).click();
    await page.goto(`${base}/?view=cart`);
    await expect(page.locator('.shop-cart-row .shop-promo-price')).toHaveText('学生价：¥1188.00');
    await expect(page.getByTestId('cart-total')).toHaveText('¥2576.00');
    await expect(page.locator('.shop-cart-footer .shop-promo-total')).toHaveText('学生价合计 ¥2376.00');
    await expect(page.locator('.shop-cart')).toContainText('学生价需符合条件，以商家确认为准。');

    // The merchant sees the same counts in 商品管理.
    await manager.reload();
    await expect(manager.locator('.shop-admin-kpis > div').filter({ hasText: '累计浏览' }).locator('dd')).toHaveText('1人次');
    await expect(manager.locator('.shop-admin-kpis > div').filter({ hasText: '累计售出' }).locator('dd')).toHaveText('36件');
    await expect(manager.locator('.shop-admin-product').filter({ has: manager.getByRole('button', { name: '商品设置 2', exact: true }) }).locator('.shop-admin-product-flags')).toContainText('浏览 1');

    for (const width of [390, 768]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`${base}/`);
      await expect(row.locator('.shop-promo-badge')).toBeVisible();
      await expect(row.locator('.shop-product-price s')).toBeVisible();
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `storefront fits ${width}px`);
      await manager.setViewportSize({ width, height: 900 });
      assert.ok(await manager.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `商品管理 fits ${width}px`);
    }
    await shot(page, 'shop-storefront-promo-390.png');
    // Explicitly clearing the mark and both announcement fields survives save/reload.
    await manager.getByRole('button', { name: '商品设置 2', exact: true }).click();
    await manager.getByLabel('新品标记 2').selectOption('');
    await manager.getByRole('button', { name: '编辑公告', exact: true }).click();
    await manager.getByLabel('公告内容', { exact: true }).fill('');
    await manager.getByLabel('还可代购的商品', { exact: true }).fill('');
    await manager.getByRole('button', { name: '保存商品设置' }).click();
    await expect(manager.getByRole('status')).toContainText('已保存');
    const cleared = await (await manager.request.get(`${base}/api/v1/shop/admin`)).json();
    assert.equal(cleared.display[2].new_until, undefined);
    assert.deepEqual(cleared.announcement, { text: '', items: [] });
    await page.reload();
    await expect(page.locator('.shop-new-badge')).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^新品/ })).toHaveCount(0);
    await expect(page.getByRole('region', { name: '店铺公告' })).toHaveCount(0);
    console.log('PASS: 优惠价 leading card prices and under the regular price on the product page, cart and message; labels, 新品 marks, the announcement, sold counts, views and WeChat');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
