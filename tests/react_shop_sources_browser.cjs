const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

// The administrator's view names each feed and files each item under its storefront category.
const status = (supplier, covers) => ({ last_success: 1000, error: null, is_stale: false, cooldown_seconds: 0, retry_after_seconds: 0, site: 'https://supplier.example/', supplier, covers });
const row = (provider, category, key, publicTitle, title, label, cost, available = true) => ({ provider, category, key, product_id: key.split(':')[0], title, public_title: publicTitle, label, cost_cents: cost, available, buy_url: 'https://supplier.example/item' });
const items = [
  row('gpt', 'GPT', '1:1', 'ChatGPT Plus', 'ChatGPT Plus 官方', '月卡', 9800),
  row('fanai', 'GPT', '4000000000006:1', 'ChatGPT Plus', 'ChatGPT Plus 菲区卡密', '标准规格', 8800),
  row('fanai', 'GPT', '4000000000007:1', 'ChatGPT Plus', 'ChatGPT Plus 周卡', '标准规格', 3000, false),
  row('anli', 'Claude', '1000000000005:1', 'Claude Pro', 'Claude Pro 代充', '标准规格', 12000),
  row('fanai', 'Claude', '4000000000022:1', 'Claude Pro', 'Claude Pro （ios）', '标准规格', 13550),
  row('lorry', 'X', '3000000000017:18', 'X Premium 3 个月', 'X Premium 3个月（官方礼物赠送·自动到账） · 商品 17', '标准规格', 2000),
  row('lorry', 'X', '3000000000018:19', 'X Premium 6 个月', 'X Premium 6个月（官方礼物赠送·自动到账） · 商品 18', '标准规格', 3900),
  row('lorry', 'Muse AI', '3000000000022:23', 'Muse AI 成品号', 'Muse 成品号（微软邮箱·验证码登录） · 商品 22', '标准规格', 600),
];
const merchant = {
  revision: 3, telegram: 'my_shop', products: {}, items,
  skus: { '1:1': { price_cents: 12900, published: true }, '1000000000005:1': { price_cents: 15900, published: true }, '4000000000022:1': { price_cents: 16900, published: true } },
  sources: { gpt: status('强云', 'GPT'), anli: status('Anli', 'Claude / Grok'), lorry: status('Lorry', 'X / Muse AI'), fanai: status('Fanai', 'GPT / Claude / Grok') },
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
    // Each feed is a chip that opens its site for the administrator.
    const chips = page.getByRole('group', { name: '来源状态' }).locator('.shop-source-chip');
    await expect(chips).toHaveText([/^GPT · 强云1$/, /^Claude \/ Grok · Anli1$/, /^X \/ Muse AI · Lorry3$/, /^GPT \/ Claude \/ Grok · Fanai3$/]);
    await expect(chips.first()).toHaveAttribute('href', 'https://supplier.example/');

    // One storefront plan holds every supplier product that sells it, cheapest purchasable first.
    const plus = page.getByRole('article', { name: 'ChatGPT Plus', exact: true });
    await expect(plus.locator('.shop-admin-product .shop-admin-supplier')).toHaveText(['Fanai', '强云', 'Fanai']);
    await expect(plus.locator('.shop-admin-product').first().locator('.badge.ok')).toHaveText('最低');
    await expect(plus.locator('.shop-admin-product').last()).toContainText('来源售罄');
    await expect(plus.locator('.shop-plan-best')).toHaveText('最低成本 ¥88.00 · Fanai');
    await expect(plus.locator('.shop-plan-head .badge.ok')).toHaveText('前台 ¥129.00');
    await expect(plus.locator('.shop-plan-warn')).toHaveCount(0);
    if (screenshotDir) { fs.mkdirSync(screenshotDir, { recursive: true }); await page.screenshot({ path: path.join(screenshotDir, 'shop-admin-compare.png'), fullPage: true }); }

    // Choosing the cheapest one when the filter hides it lifts the filter and goes to that product.
    const filters = page.getByRole('group', { name: '规格筛选' });
    await filters.getByRole('button', { name: /^已上架/ }).click();
    const target = page.locator('#shop-admin-product-4000000000006');
    await expect(target).toBeHidden();
    await plus.locator('.shop-plan-best').click();
    await expect(target).toBeVisible();
    await expect(target).toBeFocused();
    await expect(target).toBeInViewport();
    await expect(filters.getByRole('button', { name: /^全部/ })).toHaveAttribute('aria-pressed', 'true');

    await brands.getByRole('button', { name: /^Claude / }).click();
    const claude = page.getByRole('article', { name: 'Claude Pro', exact: true });
    await expect(claude.locator('.shop-admin-product .shop-admin-supplier')).toHaveText(['Anli', 'Fanai']);
    await expect(claude.locator('.shop-plan-warn')).toHaveText('2 个来源同时上架，前台会出现重复商品');

    // A plan that only one supplier sells has nothing to compare.
    await brands.getByRole('button', { name: /^X / }).click();
    await expect(page.getByRole('heading', { name: /X Premium 3个月/ })).toBeVisible();
    await expect(page.locator('.shop-plan-best:visible')).toHaveCount(0);
    await brands.getByRole('button', { name: /^Muse AI / }).click();
    await expect(page.getByRole('heading', { name: /Muse 成品号/ })).toBeVisible();

    await page.setViewportSize({ width: 390, height: 840 });
    await brands.getByRole('button', { name: /^GPT / }).click();
    const width = await page.evaluate(() => document.documentElement.scrollWidth);
    assert(width <= 390, `shop admin should fit 390px (scrollWidth ${width}px)`);
    // A feed that is out of date is not ranked: its last prices read 价格待更新, never 最低.
    await page.setViewportSize({ width: 1440, height: 1000 });
    const stale = { ...merchant, sources: { ...merchant.sources, fanai: { ...merchant.sources.fanai, is_stale: true, error: 'source_unavailable' } } };
    await page.unroute('**/api/v1/shop/admin');
    await page.route('**/api/v1/shop/admin', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify(stale) }));
    await page.reload();
    await brands.getByRole('button', { name: /^GPT / }).click();
    await expect(plus.locator('.shop-admin-product .shop-admin-supplier')).toHaveText(['强云', 'Fanai', 'Fanai']);
    await expect(plus.locator('.shop-plan-best')).toHaveText('最低成本 ¥98.00 · 强云');
    await expect(plus.locator('.shop-admin-supplier.is-stale')).toHaveCount(2);
    await expect(plus.locator('.shop-admin-product').filter({ has: page.locator('.shop-admin-supplier.is-stale') }).locator('.badge.warn').first()).toHaveText('价格待更新');
    await expect(plus.locator('.shop-admin-product').filter({ has: page.locator('.shop-admin-supplier.is-stale') }).locator('.badge.ok')).toHaveCount(0);
    await expect(chips.last()).toHaveClass(/is-warn/);
    await expect(chips.last()).toContainText('暂不可用');
    console.log('PASS: shop admin brands, source chips, plans grouping suppliers cheapest first, stale feeds never lowest');
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
