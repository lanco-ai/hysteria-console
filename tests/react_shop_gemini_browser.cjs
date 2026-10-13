const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const base = process.env.PREVIEW_BASE_URL;
    const admin = await browser.newContext();
    await admin.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const initial = await (await admin.request.get(`${base}/api/v1/shop/admin`)).json();
    const id = '2000000000001';
    const skus = { [`${id}:1`]: { price_cents: 12800, promo_price_cents: 11800, published: true } };
    const saved = await admin.request.put(`${base}/api/v1/shop/admin`, { data: {
      client: 3, revision: initial.revision, telegram: 'test_shop', skus, products: initial.products,
    } });
    assert.equal(saved.status(), 200);
    const context = await browser.newContext();
    const page = await context.newPage();
    const errors = [], external = [];
    page.on('pageerror', error => errors.push(error.message));
    context.on('request', request => { if (!request.url().startsWith(base) && !request.url().startsWith('data:')) external.push(request.url()); });
    for (const width of [390, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`${base}/?product=${id}`);
      await expect(page.getByRole('heading', { name: '💎 Gemini AI Pro｜18个月官方订阅', exact: true })).toBeVisible();
      await expect(page.getByRole('heading', { name: '🚀 套餐权益', exact: true })).toBeVisible();
      await expect(page.locator('.shop-notice')).toHaveCount(2);
      await expect(page.locator('.shop-notice ol li')).toHaveCount(4);
      await expect(page.locator('.shop-notice strong').filter({ hasText: '虚拟商品，售出不退。' })).toBeVisible();
      const link = page.getByRole('link', { name: 'https://policies.google.com/terms?hl=zh_CN', exact: true });
      await expect(link).toHaveAttribute('href', 'https://policies.google.com/terms?hl=zh_CN');
      await expect(link).toHaveAttribute('rel', 'noopener noreferrer');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await expect(page.locator('.shop-detail-copy')).not.toContainText('任何地区均可激活');
      await expect(page.locator('.shop-detail-copy')).not.toContainText('交付后 24 小时');
      if (process.env.REACT_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, `gemini-${width}.png`), fullPage: true });
      }
    }
    // The exact official link is allowed without making other Google/unknown URLs clickable.
    const state = await (await admin.request.get(`${base}/api/v1/shop/admin`)).json();
    const malicious = `${initial.products[id].description}\n\n[伪造链接](https://policies.google.com.evil.invalid/terms?hl=zh_CN)\n\n[其它页面](https://policies.google.com/privacy)\n\n<script>window.noticePwned=1</script>`;
    const changed = await admin.request.put(`${base}/api/v1/shop/admin`, { data: { client: 3,
      revision: state.revision, telegram: state.telegram, skus: state.skus,
      products: { ...state.products, [id]: { description: malicious, after_sales: initial.products[id].after_sales } },
    } });
    assert.equal(changed.status(), 200);
    await page.reload();
    await expect(page.locator('.shop-notice a')).toHaveCount(1);
    await expect(page.locator('.shop-notice script')).toHaveCount(0);
    assert.equal(await page.evaluate(() => window.noticePwned), undefined);
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log('PASS: Gemini Markdown/default copy/save, exact official link, responsive layout and link/HTML safety');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
