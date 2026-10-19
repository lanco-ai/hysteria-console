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

function grokFixture() {
  const sectioned = (kind) => `## ${kind}通用开头

${kind}公共内容。

### 规格：1 个月（月卡）

${kind}月卡专属内容。

#### 月卡补充

${kind}月卡下级内容。

### 规格：3 个月（季卡）

${kind}季卡专属内容，质保 90 天。

#### 季卡补充

${kind}季卡下级内容，90 天。

### 规格：已删除的未知规格

${kind}未知规格内容。

#### 未知规格补充

${kind}未知下级内容。

### 通用结尾

${kind}通用结尾内容。`;
  return { ...fixture(), products: [
    { id: '6000000000004', title: 'SuperGrok 充值（旧版 · iOS）', category: 'Grok',
      description: sectioned('说明'), after_sales: sectioned('售后'), variants: [
        { id: '6000000000004:1', label: '1 个月（月卡）', price_cents: 14500, available: true },
        { id: '6000000000004:2', label: '3 个月（季卡）', price_cents: 26800, available: true },
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

    // Large totals at the quantity limit must not make the fixed mobile bar jump.
    for (const otherPromo of [null, 119000]) {
      data = fixture();
      if (otherPromo) Object.assign(data.products[0].variants[0], { price_cents: 129000, promo_price_cents: otherPromo });
      await page.setViewportSize({ width: 320, height: 1000 });
      await page.goto(`${base}/?product=1&promo=${otherPromo ?? 'none'}#plus-referral`);
      await page.getByRole('spinbutton', { name: '购买数量', exact: true }).fill('99');
      await expect(page.getByTestId('purchase-total')).toHaveText('¥12672.00');
      const buybarBoxes = () => page.evaluate(() => {
        scrollTo({ top: 0, behavior: 'instant' });
        return ['.shop-detail-buybar', '.shop-detail-actions', '[data-testid="purchase-total"]'].map(selector => {
          const box = document.querySelector(selector).getBoundingClientRect();
          return [Math.round(box.x), Math.round(box.y), Math.round(box.height)];
        });
      });
      const before = await buybarBoxes();
      await page.getByRole('button', { name: '其他规格', exact: true }).click();
      await expect(page.getByTestId('purchase-total')).toHaveText(otherPromo ? '¥127710.00' : '¥12771.00');
      assert.deepEqual(await buybarBoxes(), before, `mobile purchase bar stays put at quantity 99 with ${otherPromo ? 'different offers' : 'no sibling offer'}`);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'quantity 99 detail fits 320px');
    }

    // A private sibling is absent from the public catalog; renaming either local spec also
    // removes its original label. All explicit sections still belong only to their own spec.
    for (const [caseIndex, change] of [
      value => { value.products[0].variants.pop(); },
      value => { value.products[0].variants[1].label = '季度续订'; },
      value => { value.products[0].variants[0].label = '月度续订'; },
    ].entries()) {
      data = grokFixture(); change(data);
      await page.goto(`${base}/?product=6000000000004&case=${caseIndex}`);
      const copy = page.locator('.shop-detail-copy');
      for (const kind of ['说明', '售后']) {
        await expect(copy).toContainText(`${kind}公共内容。`);
        await expect(copy).toContainText(`${kind}月卡专属内容。`);
        await expect(copy).toContainText(`${kind}月卡下级内容。`);
        await expect(copy).toContainText(`${kind}通用结尾内容。`);
        await expect(copy).not.toContainText(`${kind}季卡`);
        await expect(copy).not.toContainText(`${kind}未知`);
      }
      await expect(copy).not.toContainText('90 天');
      if (data.products[0].variants.length > 1) {
        await page.getByRole('button', { name: data.products[0].variants[1].label, exact: true }).click();
        for (const kind of ['说明', '售后']) {
          await expect(copy).toContainText(`${kind}季卡专属内容，质保 90 天。`);
          await expect(copy).toContainText(`${kind}季卡下级内容，90 天。`);
          await expect(copy).toContainText(`${kind}公共内容。`);
          await expect(copy).toContainText(`${kind}通用结尾内容。`);
          await expect(copy).not.toContainText(`${kind}月卡`);
          await expect(copy).not.toContainText(`${kind}未知`);
        }
      }
    }

    // Merchant-authored headings may follow a renamed spec's current label as well.
    data = grokFixture();
    data.products[0].variants[0].label = '月度续订';
    for (const field of ['description', 'after_sales']) data.products[0][field] = data.products[0][field].replace('规格：1 个月（月卡）', '规格：月度续订');
    await page.goto(`${base}/?product=6000000000004`);
    await expect(page.locator('.shop-detail-copy')).toContainText('说明月卡专属内容。');
    await expect(page.locator('.shop-detail-copy')).toContainText('售后月卡专属内容。');
    await expect(page.locator('.shop-detail-copy')).not.toContainText('90 天');

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
    console.log('PASS: Plus referral banner/card/detail, correct SKU and anchor, ¥123 purchase price and ¥118 referral floor, unavailable/repriced/other-plan exclusions, four responsive layouts, stable 320px quantity-99 purchase bar, unpublished/renamed spec copy and common sections');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
