const assert = require('node:assert/strict');
const { test } = require('node:test');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const catalog = () => ({ currency: 'CNY', telegram: '', wechat: 'my_shop', status: 'ready', updated_at: 1000,
  stats: { views: 0, sold: 0 }, products: [{ id: '2', title: 'ChatGPT Pro 200', category: 'GPT', badge: '学生价',
    description: '', after_sales: '', views: 0, sold: 0, variants: [
      { id: '2:7', label: '新开', price_cents: 128800, promo_price_cents: 118800, available: false, quantity: null, sales: null },
      { id: '2:2', label: '续费', price_cents: 138800, promo_price_cents: null, available: true, quantity: null, sales: null },
    ] }] });

async function preview(run) {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1024, height: 900 } });
    const data = catalog();
    await page.route('**/api/v1/shop/catalog', route => route.fulfill({ json: data }));
    await page.route('**/api/v1/shop/visit', route => route.fulfill({ json: { ok: true } }));
    await run(page, data);
  } finally { await browser.close(); }
}

test('card advertises only available offers; selected unavailable detail remains explicit', async () => preview(async (page, data) => {
  const card = page.locator('.shop-product-row');
  await page.goto(process.env.PREVIEW_BASE_URL);
  const price = card.locator('.shop-product-price');
  await expect(card.locator('.shop-product-availability')).toHaveText('可咨询购买');
  await expect(price.locator('strong')).toHaveText('¥1388.00');
  await expect(price.locator('s')).toHaveCount(0);
  await expect(price.locator('.shop-product-from')).toHaveCount(0);
  // A sold-out cheaper offer must not undercut the available one.
  data.products[0].variants[1].promo_price_cents = 128800;
  await page.reload();
  await expect(price.locator('strong')).toHaveText('¥1288.00');
  await expect(price.locator('s')).toHaveText('原价 ¥1388.00');
  await expect(price.locator('.shop-product-from')).toHaveCount(0);
  // With both available, the minimum and “起” are meaningful again.
  data.products[0].variants[0].available = true;
  await page.reload();
  await expect(price.locator('strong')).toHaveText('¥1188.00');
  await expect(price.locator('.shop-product-from')).toHaveText('起');
  await expect(price.locator('s')).toHaveText('原价 ¥1288.00');
  // With nothing available, no 优惠价 is advertised: the lowest regular price stays, greyed.
  data.products[0].variants.forEach(variant => { variant.available = false; });
  await page.reload();
  await expect(price.locator('strong')).toHaveText('¥1288.00');
  await expect(price.locator('strong')).not.toHaveClass('is-promo');
  await expect(price.locator('s')).toHaveCount(0);
  await expect(card).toHaveClass(/is-unavailable/);
  await expect(card.getByRole('button', { name: /暂不可购买/ })).toBeDisabled();
  await page.goto(`${process.env.PREVIEW_BASE_URL}/?product=2`);
  await expect(page.locator('.shop-detail-price .shop-promo-price')).toHaveText('学生价：¥1188.00');
  await expect(page.getByRole('button', { name: '立即购买', exact: true })).toBeDisabled();
}));

test('long valid offer label never intersects the card cart button', async () => preview(async (page, data) => {
  data.products[0].badge = '开学季学生优惠价';
  data.products[0].variants[0].available = true;
  for (const width of [1024, 1440, 768, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(process.env.PREVIEW_BASE_URL);
    const card = page.locator('.shop-product-row');
    const offer = card.locator('.shop-product-price');
    await expect(offer.locator('.shop-promo-badge')).toHaveText('开学季学生优惠价');
    await expect(offer.locator('s')).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const b = await card.locator('.shop-product-cart').boundingBox();
    const bounds = await card.boundingBox();
    // The label, the price and the struck regular price each stay inside the card and clear of the cart.
    for (const part of ['.shop-promo-badge', 'strong', 's']) {
      const a = await offer.locator(part).boundingBox();
      const overlaps = a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
      assert.equal(overlaps, false, `${part} overlaps the cart at ${width}px: ${JSON.stringify({ a, b })}`);
      assert.ok(a.x + a.width <= bounds.x + bounds.width, `${part} fits the card at ${width}px`);
    }
    if (width === 1024 && process.env.REACT_SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, 'offer-1024.png'), fullPage: true });
    }
  }
}));

test('an expired last new product returns the new filter to the complete catalog', async () => preview(async (page, data) => {
  const now = Date.now();
  await page.clock.install({ time: now });
  data.products[0].new_until = Math.floor(now / 1000) + 30;
  await page.goto(process.env.PREVIEW_BASE_URL);
  await page.getByRole('button', { name: /^新品/ }).click();
  await expect(page.locator('.shop-product-row')).toHaveCount(1);
  await page.clock.runFor(61_000);
  await expect(page.getByRole('button', { name: /^新品/ })).toHaveCount(0);
  await expect(page.locator('.shop-product-row')).toHaveCount(1);
  await expect(page.getByRole('button', { name: /^全部/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.shop-new-badge')).toHaveCount(0);
}));

test('maximum-length announcement copy fits tablet and phone layouts', async () => preview(async (page, data) => {
  data.announcement = { text: 'A'.repeat(200), items: ['会员'.repeat(12)] };
  for (const width of [1440, 1024, 800, 768, 640, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(process.env.PREVIEW_BASE_URL);
    await expect(page.getByRole('region', { name: '店铺公告' })).toBeVisible();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `announcement fits ${width}px`);
    assert.ok(await page.locator('.shop-announcement p').evaluate(el => el.scrollWidth <= el.clientWidth + 1), `announcement text wraps at ${width}px`);
  }
}));

test('a brand\'s plans show short names and all stay in view', async () => preview(async (page, data) => {
  const plan = (id, title) => ({ ...data.products[0], id, title, badge: '', variants: data.products[0].variants.map(variant => ({ ...variant, id: `${id}:1`, available: true })).slice(0, 1) });
  // codex额度充值 does not start with ChatGPT; the others still lose the brand word and their region.
  data.products = [plan('1', 'ChatGPT Plus｜菲律宾区'), plan('2', 'ChatGPT Go｜埃及区'), plan('3', 'ChatGPT Pro 100｜智利区'),
    plan('4', 'ChatGPT Pro 200｜菲律宾区'), plan('5', 'ChatGPT Pro 500｜卡付充值'), plan('6', 'codex额度充值'), plan('7', 'ChatGPT 学生认证')];
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`${process.env.PREVIEW_BASE_URL}/?product=4`);
    const plans = page.getByRole('navigation', { name: 'GPT 其他商品' });
    await expect(plans.getByRole('link')).toHaveText(['Plus', 'Go', 'Pro 100', 'Pro 200', 'Pro 500', 'codex额度充值', '学生认证']);
    const box = await plans.boundingBox();
    for (const link of await plans.getByRole('link').all()) {
      const chip = await link.boundingBox();
      assert.ok(chip.x >= box.x - 1 && chip.x + chip.width <= box.x + box.width + 1, `plan chip out of view at ${width}px`);
    }
  }
}));

test('text tags remain readable with or without optional highlight metadata', async () => preview(async (page, data) => {
  data.products[0].tags = ['支持二验三验', '支持Codex'];
  await page.goto(process.env.PREVIEW_BASE_URL);
  await expect(page.locator('.shop-product-row .shop-tag-badge')).toHaveText(data.products[0].tags);
  await expect(page.locator('.shop-product-row .shop-tag-badge.is-hot')).toHaveCount(0);
  data.products[0].hot_tags = ['支持Codex'];
  await page.reload();
  await expect(page.locator('.shop-product-row .shop-tag-badge.is-hot')).toHaveText('支持Codex');
  await page.locator('[data-shop-card="2"] h3 a').click();
  await expect(page.locator('.shop-detail-heading .shop-tag-badge')).toHaveText(data.products[0].tags);
  await expect(page.locator('.shop-detail-heading .shop-tag-badge.is-hot')).toHaveText('支持Codex');
}));
