const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const base = process.env.PREVIEW_BASE_URL;
    const context = await browser.newContext();
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const id = '2000000000003', key = `${id}:1`;
    const catalog = async () => (await context.request.get(`${base}/api/v1/shop/catalog`)).json();
    assert.equal((await catalog()).products.some(product => product.id === id), false);
    await page.goto(`${base}/admin/shop`);
    await page.getByRole('group', { name: '品牌', exact: true }).getByRole('button', { name: /^多邻国 / }).click();
    const product = page.locator(`section[aria-labelledby="shop-admin-product-${id}"]`);
    await expect(product).toContainText('Duolingo Super 12M');
    await expect(product.locator('.shop-admin-cost')).toContainText('¥2.09');
    await expect(page.getByLabel(`售价 ${key}`, { exact: true })).toHaveValue('');
    await expect(page.getByLabel(`上架 ${key}`, { exact: true })).not.toBeChecked();
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      if (process.env.REACT_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, `duolingo-draft-${width}.png`), fullPage: true });
      }
    }
    // Fixture-only publication verifies the customer page, never a live merchant change.
    await page.getByLabel(`售价 ${key}`, { exact: true }).fill('15');
    await page.getByLabel(`上架 ${key}`, { exact: true }).check();
    await page.getByRole('button', { name: '保存商品设置', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('已保存');
    const publicProduct = (await catalog()).products.find(product => product.id === id);
    assert.equal(publicProduct.category, '多邻国');
    assert.equal(JSON.stringify(publicProduct).toLowerCase().includes('prodseller'), false);
    await page.goto(`${base}/?product=${id}`);
    await expect(page.getByRole('img', { name: '多邻国', exact: true }).first()).toBeVisible();
    await expect(page.locator('.shop-notice')).toContainText('Claim Offer');
    await expect(page.locator('.shop-notice').getByRole('heading', { name: 'Duolingo Super 12 个月', exact: true })).toBeVisible();
    assert.deepEqual(errors, []);
    console.log('PASS: Duolingo unpriced/unpublished admin, category, cost, mobile layout and fixture-only storefront publication');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
