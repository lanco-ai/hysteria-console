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
  await expect(card.locator('.shop-product-availability')).toHaveText('可咨询购买');
  await expect(card.locator('.shop-promo-price')).toHaveCount(0);
  // A sold-out cheaper offer must not undercut the available one.
  data.products[0].variants[1].promo_price_cents = 128800;
  await page.reload();
  await expect(card.locator('.shop-promo-price')).toHaveText('学生价：¥1288.00');
  // With both available, the minimum and “起” are meaningful again.
  data.products[0].variants[0].available = true;
  await page.reload();
  await expect(card.locator('.shop-promo-price')).toHaveText('学生价：¥1188.00起');
  data.products[0].variants.forEach(variant => { variant.available = false; });
  await page.reload();
  await expect(card.locator('.shop-promo-price')).toHaveCount(0);
  await expect(card.getByRole('button', { name: /暂不可购买/ })).toBeDisabled();
  await page.goto(`${process.env.PREVIEW_BASE_URL}/?product=2`);
  await expect(page.locator('.shop-detail-price .shop-promo-price')).toHaveText('学生价：¥1188.00');
  await expect(page.getByRole('button', { name: '立即购买', exact: true })).toBeDisabled();
}));

test('long valid offer label never intersects the card action buttons', async () => preview(async (page, data) => {
  data.products[0].badge = '开学季学生优惠价';
  data.products[0].variants[0].available = true;
  for (const width of [1024, 1440, 768, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(process.env.PREVIEW_BASE_URL);
    const card = page.locator('.shop-product-row');
    const offer = card.locator('.shop-promo-price');
    await expect(offer).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const a = await offer.boundingBox();
    const b = await card.locator('.shop-product-action').boundingBox();
    const overlaps = a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
    assert.equal(overlaps, false, `offer overlaps actions at ${width}px: ${JSON.stringify({ a, b })}`);
    const bounds = await card.boundingBox();
    assert.ok(a.x + a.width <= bounds.x + bounds.width, `offer fits card at ${width}px`);
    if (width === 1024 && process.env.REACT_SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, 'offer-1024.png'), fullPage: true });
    }
  }
}));
