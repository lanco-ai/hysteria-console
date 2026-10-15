const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

function fixture() {
  return { currency: 'CNY', telegram: '', wechat: 'demo_shop', status: 'ready', updated_at: 1000,
    products: [
      { id: '1', title: 'ChatGPT Plus', category: 'GPT', badge: '限时价', variants: [
        { id: '1:4', label: '其他规格', price_cents: 12900, available: true },
        { id: '1:1', label: '一个月', price_cents: 12800, promo_price_cents: 11800, available: true },
      ] },
      { id: '2', title: 'ChatGPT Pro 200', category: 'GPT', variants: [
        { id: '2:1', label: '一个月', price_cents: 120000, available: true },
      ] },
    ] };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const base = process.env.PREVIEW_BASE_URL;
    let data = fixture();
    await page.route('**/api/v1/shop/catalog', route => route.fulfill({ json: data }));
    await page.route('**/api/v1/shop/visit', route => route.fulfill({ json: { ok: true } }));
    const shots = process.env.REACT_SCREENSHOT_DIR;
    async function screenshot(name) {
      if (!shots) return;
      fs.mkdirSync(shots, { recursive: true });
      await page.screenshot({ path: path.join(shots, name), fullPage: true });
    }

    for (const width of [390, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.goto(base);
      const banner = page.locator('.shop-referral-banner');
      const teaser = page.locator('.shop-referral-teaser');
      await expect(banner).toContainText('每推荐 1 人购买，续费减 ¥1，最低 ¥113/月');
      await expect(teaser).toHaveCount(1);
      await expect(teaser).toContainText('每推荐 1 人购买，后续每月续费减 ¥1');
      const card = page.locator('.shop-product-row').filter({ has: teaser });
      await page.evaluate(() => document.fonts.ready);
      const promo = await teaser.boundingBox();
      const actions = await card.locator('.shop-product-action').boundingBox();
      assert.ok(promo.y >= actions.y + actions.height, `reward remains below cart actions at ${width}px`);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `home fits ${width}px`);
      if (width === 390 || width === 1440) await screenshot(`referral-home-${width}.png`);

      // Both entries reach the rules and select the eligible SKU, even when it
      // is not the first available variant. No referral discount is assumed.
      await (width === 390 ? teaser : banner).click();
      await expect(page).toHaveURL(/\?product=1#plus-referral$/);
      const rules = page.locator('#plus-referral');
      await expect(rules).toBeVisible();
      await expect(rules).toBeFocused();
      await expect(page.getByRole('button', { name: '一个月', exact: true })).toHaveAttribute('aria-pressed', 'true');
      await expect(rules).toContainText('后续每月续费价格累计降低 ¥1，最低 ¥113/月');
      await expect(page.locator('body')).not.toContainText('返利');
      await expect(rules).toContainText('续费优惠由商家确认');
      await expect(page.getByTestId('purchase-total')).toHaveText('¥128.00');
      await expect(page.locator('.shop-detail-price .shop-promo-price')).toHaveText('限时价：¥118.00');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `detail fits ${width}px`);
      if (width === 390 || width === 1440) await screenshot(`referral-detail-${width}.png`);
      await page.getByRole('button', { name: '其他规格', exact: true }).click();
      await expect(rules).toHaveCount(0);
      await page.getByRole('button', { name: '一个月', exact: true }).click();
      await expect(rules).toBeVisible();
    }

    // A stale, withdrawn, sold-out or repriced offer must not advertise ¥118
    // or imply that all Plus products participate in this specific campaign.
    const cases = [
      value => { value.status = 'stale'; },
      value => { value.products[0].variants[1].available = false; },
      value => { value.products[0].variants[1].promo_price_cents = 11900; },
      value => { value.products[0].variants[1].promo_price_cents = null; },
      value => { value.products[0].title = 'ChatGPT Pro 100'; },
      value => { value.products[0].id = '9'; },
      value => { value.products.shift(); },
    ];
    for (const change of cases) {
      data = fixture(); change(data);
      await page.goto(base);
      await expect(page.locator('.shop-product-row').first()).toBeVisible();
      await expect(page.locator('.shop-referral-link')).toHaveCount(0);
      await page.goto(`${base}/?product=1#plus-referral`);
      await expect(page.locator('.shop-detail, .shop-empty')).toBeVisible();
      await expect(page.locator('#plus-referral')).toHaveCount(0);
    }
    // The same confirmed ¥118 offer may later become the regular selling price.
    data = fixture();
    Object.assign(data.products[0].variants[1], { price_cents: 11800, promo_price_cents: null });
    await page.goto(base);
    await expect(page.locator('.shop-referral-banner')).toBeVisible();
    console.log('PASS: Plus referral banner/card/detail, correct SKU and anchor, unchanged purchase pricing, unavailable/repriced/other-plan exclusions, four responsive layouts');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
