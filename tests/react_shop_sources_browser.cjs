const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

const status = { last_success: 1000, error: null, is_stale: false, cooldown_seconds: 0, retry_after_seconds: 0 };
const row = (key, publicTitle, title, label, cost, available = true) => ({ key, product_id: key.split(':')[0], title, public_title: publicTitle, label, cost_cents: cost, available });
const items = [
  row('1:1', 'ChatGPT Plus', 'ChatGPT Plus 官方', '月卡', 9800),
  row('4000000000001:11', 'ChatGPT Plus', 'ChatGPT Plus 成品号', '月卡', 8800),
  row('4000000000001:12', 'ChatGPT Plus', 'ChatGPT Plus 成品号', '周卡', 3000, false),
  row('1000000000005:1', 'Claude Pro', 'Claude Pro 代充', '标准规格', 12000),
  row('4000000000003:31', 'Claude Pro', 'Claude Pro', '标准规格', 13550),
  row('3000000000017:18', 'X Premium 3 个月', 'X Premium 3个月（官方礼物赠送·自动到账） · 商品 17', '标准规格', 9900),
  row('3000000000018:19', 'X Premium 6 个月', 'X Premium 6个月（官方礼物赠送·自动到账） · 商品 18', '标准规格', 16850),
  row('3000000000030:40', 'Muse AI', 'Muse AI 会员 · 商品 30', '月卡', 3550),
];
const merchant = {
  revision: 3, telegram: 'my_shop', products: {}, items,
  skus: { '1:1': { price_cents: 12900, published: true }, '1000000000005:1': { price_cents: 15900, published: true }, '4000000000003:31': { price_cents: 16900, published: true } },
  sources: { gpt: status, anli: status, lorry: status, fanai: status },
  last_success: 1000, error: null, is_stale: false, cooldown_seconds: 0, retry_after_seconds: 0,
};

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const base = process.env.PREVIEW_BASE_URL;
    const screenshotDir = process.env.REACT_SCREENSHOT_DIR;
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const page = await context.newPage();
    await page.addInitScript(() => { try { localStorage.removeItem('hysteria.shop.admin.brand.v1'); } catch { /* no storage */ } });
    await page.route('**/api/v1/shop/admin', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify(merchant) }));
    await page.goto(`${base}/admin/shop`);

    const brands = page.getByRole('group', { name: '品牌' });
    for (const name of ['GPT', 'Claude', 'X', 'Muse AI']) await expect(brands.getByRole('button', { name: new RegExp(`^${name} `) })).toBeVisible();
    await expect(page.locator('.shop-admin-source-name strong')).toHaveText(['GPT · 强云', 'Claude / Grok · Anli', 'X / Muse AI · Lorry', 'GPT / Claude / Grok · Fanai']);

    const gptCompare = page.getByRole('region', { name: 'GPT 同款比价' });
    const offers = gptCompare.locator('li');
    await expect(offers).toHaveCount(2);
    await expect(offers.first()).toContainText('Fanai');
    await expect(offers.first()).toContainText('¥88.00');
    await expect(offers.first()).toContainText('最低');
    await expect(offers.nth(1)).toContainText('强云');
    await expect(offers.nth(1)).toContainText('已上架');
    await expect(page.locator('.shop-admin-supplier').first()).toBeVisible();
    if (screenshotDir) { fs.mkdirSync(screenshotDir, { recursive: true }); await page.screenshot({ path: path.join(screenshotDir, 'shop-admin-compare.png'), fullPage: true }); }

    await brands.getByRole('button', { name: /^Claude / }).click();
    const claudeCompare = page.getByRole('region', { name: 'Claude 同款比价' });
    await expect(claudeCompare.locator('li').first()).toContainText('Anli');
    await expect(claudeCompare).toContainText('多个来源同时上架了 Claude Pro');

    await brands.getByRole('button', { name: /^X / }).click();
    await expect(page.getByRole('heading', { name: /X Premium 3个月/ })).toBeVisible();
    await expect(page.getByRole('region', { name: 'X 同款比价' })).toHaveCount(0);
    await brands.getByRole('button', { name: /^Muse AI / }).click();
    await expect(page.getByRole('heading', { name: /Muse AI 会员/ })).toBeVisible();

    await page.setViewportSize({ width: 390, height: 840 });
    await brands.getByRole('button', { name: /^GPT / }).click();
    const width = await page.evaluate(() => document.documentElement.scrollWidth);
    assert(width <= 390, `shop admin should fit 390px (scrollWidth ${width}px)`);
    console.log('PASS: shop admin X / Muse AI brands, supplier labels and same-plan price comparison');
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
