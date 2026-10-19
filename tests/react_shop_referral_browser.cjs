const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

function fixture() {
  return { currency: 'CNY', telegram: '', wechat: 'demo_shop', status: 'ready', updated_at: 1000,
    products: [
      { id: '1', title: 'ChatGPT Plus｜菲律宾区', category: 'GPT', badge: '限时价', description: '## Plus\n\n### 规格：一个月\n\n一个月专属说明。\n\n### 规格：其他规格\n\n其他规格专属说明。\n\n### 通用\n\n两个规格都适用。', variants: [
        { id: '1:4', label: '其他规格', price_cents: 12900, available: true },
        { id: '1:1', label: '一个月', price_cents: 12800, promo_price_cents: 12300, available: true },
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
      await expect(banner).toContainText('每推荐 1 人购买，续费减 ¥1，最低 ¥118/月');
      await expect(teaser).toHaveCount(1);
      await expect(page.locator('body')).not.toContainText('¥113');
      await expect(teaser).toContainText('每推荐 1 人购买，后续每月续费减 ¥1');
      const card = page.locator('.shop-product-row').filter({ has: teaser });
      await page.evaluate(() => document.fonts.ready);
      const promo = await teaser.boundingBox();
      // The reward sits with the product's details, above the price and cart, so prices still line up across a row.
      const buy = await card.locator('.shop-product-buy').boundingBox();
      assert.ok(promo.y + promo.height <= buy.y, `reward stays clear of the price and cart at ${width}px`);
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
      await expect(rules).toContainText('后续每月续费价格累计降低 ¥1，最低 ¥118/月');
      await expect(page.locator('body')).not.toContainText('返利');
      await expect(rules.locator('.shop-referral-base')).toHaveText('月订阅 ¥123');
      await expect(rules.locator('li').first()).toHaveText('适用规格：一个月，以月订阅 ¥123 购买。');
      await expect(rules.locator('.shop-referral-examples > div')).toHaveText(['推荐 1 人¥122/月', '推荐 5 人及以上¥118/月']);
      await expect(rules).toContainText('续费优惠由商家确认');
      await expect(page.getByTestId('purchase-total')).toHaveText('¥128.00');
      await expect(page.locator('.shop-detail-price .shop-promo-price')).toHaveText('限时价：¥123.00');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `detail fits ${width}px`);
      if (width === 390 || width === 1440) await screenshot(`referral-detail-${width}.png`);
      await page.getByRole('button', { name: '立即购买', exact: true }).click();
      const summary = page.getByRole('dialog', { name: '购买信息', exact: true }).getByLabel('可复制的购买信息');
      await expect(summary).toHaveValue(/限时价.*¥123\.00/);
      await expect(summary).not.toHaveValue(/¥118\.00/);
      await page.keyboard.press('Escape');
      // Each spec shows its price on its button; the copy shows the picked spec's section.
      await expect(page.locator('.shop-specs button .shop-spec-meta')).toHaveText(['¥129.00', '¥123.00 限时价']);
      const copy = page.locator('.shop-detail-copy');
      await expect(copy).toContainText('一个月专属说明。');
      await expect(copy).not.toContainText('其他规格专属说明。');
      // Switching specs moves nothing: the rules stay (they name their spec), the offer line and the
      // total keep their place, and only the picked spec's copy changes.
      // Measured from the top of the page, where the sticky and fixed parts sit in their own places.
      const boxes = () => page.evaluate(() => {
        scrollTo({ top: 0, behavior: 'instant' });
        return Object.fromEntries(['.shop-detail-price', '.shop-specs', '.shop-detail-buybar', '#plus-referral', '.shop-detail-copy', '[data-testid="purchase-total"]']
          .map(selector => { const box = document.querySelector(selector).getBoundingClientRect(); return [selector, [Math.round(box.x), Math.round(box.y), Math.round(box.height)]]; }));
      });
      const before = await boxes();
      await page.getByRole('button', { name: '其他规格', exact: true }).click();
      await expect(page.getByRole('button', { name: '其他规格', exact: true })).toHaveAttribute('aria-pressed', 'true');
      await expect(rules).toBeVisible();
      await expect(page.locator('.shop-detail-offer')).toHaveText('此规格不参加限时价');
      await expect(page.getByTestId('purchase-total')).toHaveText('¥129.00');
      await expect(copy).toContainText('其他规格专属说明。');
      await expect(copy).not.toContainText('一个月专属说明。');
      await expect(copy).toContainText('两个规格都适用。');
      const after = await boxes();
      for (const selector of Object.keys(before).filter(key => key !== '.shop-detail-copy')) assert.deepEqual(after[selector], before[selector], `${selector} stays put at ${width}px`);
      assert.deepEqual(after['.shop-detail-copy'].slice(0, 2), before['.shop-detail-copy'].slice(0, 2), `copy starts in place at ${width}px`);
      await page.getByRole('button', { name: '一个月', exact: true }).click();
      await expect(rules).toBeVisible();
    }

    // A stale, withdrawn, sold-out or repriced offer must not advertise ¥123
    // or imply that all Plus products participate in this specific campaign.
    const cases = [
      value => { value.status = 'stale'; },
      value => { value.products[0].variants[1].available = false; },
      value => { value.products[0].variants[1].promo_price_cents = 11800; },
      value => { value.products[0].variants[1].promo_price_cents = 12400; },
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
    // The same confirmed ¥123 offer may later become the regular selling price.
    data = fixture();
    Object.assign(data.products[0].variants[1], { price_cents: 12300, promo_price_cents: null });
    await page.goto(base);
    await expect(page.locator('.shop-referral-banner')).toBeVisible();
    console.log('PASS: Plus referral banner/card/detail, correct SKU and anchor, ¥123 purchase price and ¥118 referral floor, unavailable/repriced/other-plan exclusions, four responsive layouts');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
