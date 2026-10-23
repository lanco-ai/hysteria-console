const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const base = process.env.PREVIEW_BASE_URL;
    const id = process.env.PRICING_TEST_PRODUCT || '2000000000001', sku = `${id}:1`;
    const duo = id === '2000000000003';
    const minimum = duo ? 10 : 5;
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const initial = await (await context.request.get(`${base}/api/v1/shop/admin`)).json();
    const manual = { price_cents: 1000, promo_price_cents: duo ? 800 : 700, published: true };
    const response = await context.request.put(`${base}/api/v1/shop/admin`, { data: {
      client: 3, revision: initial.revision, telegram: 'test_shop', skus: { [sku]: manual },
      display: { [id]: { badge: '限时价', sold: 4 } }, products: initial.products,
    } });
    assert.equal(response.status(), 200);
    const manager = await context.newPage();
    const errors = [];
    manager.on('pageerror', error => errors.push(error.message));
    await manager.goto(`${base}/admin/shop`);
    await manager.getByRole('button', { name: duo ? /^多邻国/ : /^Gemini/ }).click();
    const row = manager.locator('.shop-admin-row').filter({ has: manager.getByLabel(`售价 ${sku}`, { exact: true }) });
    await expect(row.locator('.shop-admin-margin strong')).toHaveText(duo ? '¥5.91' : '-¥0.56');
    await expect(row.locator('.shop-admin-margin')).toHaveClass(duo ? /is-gain/ : /is-loss/);
    await expect(row.locator('.shop-admin-margin small')).toHaveText(duo ? '原价毛利 ¥7.91' : '原价毛利 ¥2.44');
    await manager.getByLabel(`动态定价 ${sku}`).check();
    await expect(manager.getByLabel(`售价 ${sku}`, { exact: true })).toHaveValue('12.88');
    await expect(manager.getByLabel(`售价 ${sku}`, { exact: true })).toBeDisabled();
    await expect(manager.getByLabel(`优惠价 ${sku}`)).toHaveValue('');
    await expect(manager.getByLabel(`优惠价 ${sku}`)).toBeDisabled();
    await expect(row.locator('.shop-admin-margin strong')).toHaveText(duo ? '¥10.79' : '¥5.32');
    await manager.getByLabel(`最低毛利 ${sku}`).fill((minimum - 0.01).toFixed(2));
    await manager.getByRole('button', { name: '保存商品设置' }).click();
    await expect(manager.getByRole('alert')).toContainText(`最低毛利不能低于 ¥${minimum}`);
    await manager.getByLabel(`最低毛利 ${sku}`).fill(String(minimum));
    await manager.getByRole('button', { name: '保存商品设置' }).click();
    await expect(manager.getByRole('status')).toContainText('已保存');
    await manager.reload();
    await expect(manager.getByLabel(`动态定价 ${sku}`)).toBeChecked();
    await expect(manager.getByLabel(`售价 ${sku}`, { exact: true })).toHaveValue('12.88');
    await expect(manager.locator('.shop-plan:visible .shop-plan-head')).toContainText('前台 ¥12.88');
    const saved = await (await context.request.get(`${base}/api/v1/shop/admin`)).json();
    assert.deepEqual(saved.skus[sku], { ...manual, pricing: { mode: 'cost_plus', profit_cents: minimum * 100 } });

    for (const width of [1440, 390, 320]) {
      await manager.setViewportSize({ width, height: 1000 });
      await manager.evaluate(() => document.fonts.ready);
      if (width <= 390) await expect.poll(() => manager.locator('#sidebar').evaluate(el => el.getBoundingClientRect().right)).toBeLessThanOrEqual(0);
      await expect(manager.getByLabel(`最低毛利 ${sku}`)).toBeVisible();
      assert.ok(await manager.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      if (process.env.REACT_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
        await manager.locator('.shop-plan:visible').screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, `${duo ? 'duolingo' : 'gemini'}-pricing-admin-${width}.png`) });
      }
    }
    const buyer = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await buyer.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base);
    const card = page.locator('.shop-product-row');
    await expect(card.locator('.shop-product-price strong')).toHaveText('¥12.88');
    await expect(card.locator('.shop-product-price s')).toHaveCount(0);
    await expect(card.locator('.shop-promo-badge')).toHaveCount(0);
    await page.goto(`${base}/?product=${id}`);
    await expect(page.locator('.shop-detail-price')).toContainText('¥12.88');
    await page.getByLabel('购买数量', { exact: true }).fill('2');
    await page.getByRole('button', { name: '立即购买', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '购买信息' });
    await expect(dialog.getByLabel('可复制的购买信息')).toHaveValue(/单价：¥12\.88 · 小计：¥25\.76/);
    // A new source quote changes the price without an administrator save. A stale
    // purchase dialog must show the new amount before copying or contacting anyone.
    fs.writeFileSync(process.env.GEMINI_TEST_QUOTE_FILE, JSON.stringify({ cost: 1500, generation: 1 }));
    assert.equal((await context.request.post(`${base}/api/v1/shop/refresh`, { data: {} })).status(), 200);
    await dialog.getByRole('button', { name: '复制购买信息' }).click();
    await expect(dialog).toContainText('商品价格或联系方式已更新');
    await expect(dialog.getByLabel('可复制的购买信息')).toHaveValue(duo ? /单价：¥25\.00 · 小计：¥50\.00/ : /单价：¥20\.00 · 小计：¥40\.00/);
    await page.keyboard.press('Escape');
    await page.reload();
    await page.getByRole('button', { name: '加入购物车', exact: true }).click();
    await page.goto(`${base}/?view=cart`);
    await expect(page.getByTestId('cart-total')).toHaveText(duo ? '¥25.00' : '¥20.00');
    // A missing fresh cost cannot keep selling at the old cheap manual promotion.
    fs.writeFileSync(process.env.GEMINI_TEST_QUOTE_FILE, JSON.stringify({ cost: 1500, generation: 2, fail: true }));
    assert.equal((await context.request.post(`${base}/api/v1/shop/refresh`, { data: {} })).status(), 200);
    await page.goto(`${base}/?product=${id}`);
    await expect(page.getByRole('button', { name: '立即购买', exact: true })).toBeDisabled();
    await manager.reload();
    await expect(row.locator('.shop-admin-margin strong')).toHaveText('待同步成本');
    // Explicitly returning to manual restores the saved price and promo, never a
    // generated quote accidentally frozen into merchant settings.
    await manager.getByLabel(`动态定价 ${sku}`).uncheck();
    await expect(manager.getByLabel(`售价 ${sku}`, { exact: true })).toHaveValue('10.00');
    await expect(manager.getByLabel(`优惠价 ${sku}`)).toHaveValue(duo ? '8.00' : '7.00');
    await manager.getByRole('button', { name: '保存商品设置' }).click();
    await expect(manager.getByRole('status')).toContainText('已保存');
    const reverted = await (await context.request.get(`${base}/api/v1/shop/admin`)).json();
    assert.deepEqual(reverted.skus[sku], manual);
    assert.deepEqual(errors, []);
    console.log(`PASS: ${duo ? 'Duolingo' : 'Gemini'} dynamic pricing, actual margin, persistence, mobile layout, live quote changes, cart/purchase revalidation and manual restore`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
