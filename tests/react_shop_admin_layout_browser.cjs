const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

// Fictional data with the crowded combinations seen in daily shop administration.
const row = (key, provider, publicTitle, title, label, cost, available = true) => ({
  key, product_id: key.split(':')[0], provider, category: 'GPT', public_title: publicTitle,
  title, label, cost_cents: cost, available, buy_url: 'https://supplier.example/item',
});
const items = [
  ...[100, 200, 300, 500].map((amount, i) => row(`1:${i + 1}`, 'gpt', 'Codex 额度充值', 'codex额度充值', `${amount}美金额度`, (i + 1) * 5800)),
  row('2:7', 'gpt', 'ChatGPT Plus', '菲律宾ChatGpt plus卡密（官方同步）', 'plus不可以覆盖', 10750),
  row('2:2', 'gpt', 'ChatGPT Plus', '菲律宾ChatGpt plus卡密（官方同步）', '一卡一付', 10900, false),
  row('4000000000006:1', 'fanai', 'ChatGPT Plus', 'ChatGPT Plus 菲区卡密（全程质保）', '标准规格', 13800, false),
  row('3:1', 'gpt', 'ChatGPT Pro 100', 'chatgpt pro100$官方充值', '支持任意地区的个人账号充值，请先核对套餐', 47000),
  row('4000000000008:1', 'fanai', 'ChatGPT Pro 100', 'ChatGPT Pro 5x 官方续订（请先阅读商品说明）', '标准规格', 44800),
];
const source = supplier => ({ supplier, site: 'https://supplier.example/', covers: 'GPT', last_success: 1000, is_stale: false, cooldown_seconds: 0, retry_after_seconds: 0, error: null });
const merchant = {
  revision: 1, telegram: 'sample_shop', wechat: 'sample_shop', items,
  sources: { gpt: source('强云'), fanai: source('Fanai') },
  skus: {
    '2:7': { price_cents: 12800, promo_price_cents: 11800, published: true },
    '2:2': { price_cents: 12900, published: true },
    '3:1': { price_cents: 54000, promo_price_cents: 51800, published: true },
    '4000000000008:1': { price_cents: 52900, published: false },
  },
  display: { 2: { badge: '学生价', sold: 12 }, 3: { badge: '开学季学生专享价', sold: 3 } },
  products: { 2: { description: '测试商品说明', after_sales: '测试售后' }, 3: { description: '测试商品说明', after_sales: '测试售后' } },
  stats: { site: 18, products: { 2: 1 } },
  last_success: 1000, error: null, is_stale: false, cooldown_seconds: 0, retry_after_seconds: 0,
};

async function box(locator) {
  const value = await locator.boundingBox();
  assert(value, 'expected a visible element');
  return value;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const base = process.env.PREVIEW_BASE_URL;
    const screenshotDir = process.env.REACT_SCREENSHOT_DIR;
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const page = await context.newPage();
    await page.route('**/api/v1/shop/admin', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify(merchant) }));
    await page.goto(`${base}/admin/shop`);
    const plus = page.getByRole('article', { name: 'ChatGPT Plus', exact: true });
    const sourceProduct = plus.locator('.shop-admin-product').filter({ has: page.getByLabel('售价 2:7', { exact: true }) });
    await expect(sourceProduct).toBeVisible();
    if (screenshotDir) {
      fs.mkdirSync(screenshotDir, { recursive: true });
      await page.screenshot({ path: path.join(screenshotDir, 'admin-desktop.png'), fullPage: true });
      await plus.screenshot({ path: path.join(screenshotDir, 'admin-plus-desktop.png') });
      await page.setViewportSize({ width: 390, height: 844 });
      await plus.evaluate(element => window.scrollTo(0, window.scrollY + element.getBoundingClientRect().top - 128));
      await page.screenshot({ path: path.join(screenshotDir, 'admin-plus-mobile.png') });
    }

    // Separate plans must read as separate cards, even when each has multiple feeds.
    await page.setViewportSize({ width: 1920, height: 1080 });
    const plans = page.locator('.shop-plan:visible');
    const first = await box(plans.nth(0));
    const second = await box(plans.nth(1));
    assert(second.y - first.y - first.height >= 12, 'storefront plans need a visible gap');
    const identity = await box(sourceProduct.locator('.shop-admin-product-header'));
    const spec = await box(sourceProduct.locator('.shop-admin-spec').first());
    assert(identity.y + identity.height <= spec.y, 'source identity stays above the full-width pricing grid');

    // Every price has a nearby heading; rows stay aligned, with no crowded or clipped controls.
    for (const width of [1920, 1440, 1280, 1024, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      await expect(page.getByLabel('售价 2:7', { exact: true })).toHaveValue('128.00');
      const problems = await page.evaluate(() => {
        const failures = [];
        const visible = element => element.getClientRects().length > 0;
        if (document.documentElement.scrollWidth > innerWidth) failures.push('horizontal page overflow');
        for (const row of document.querySelectorAll('.shop-admin-row')) {
          if (!visible(row)) continue;
          const cells = [...row.children].filter(visible).map(element => element.getBoundingClientRect());
          for (let i = 0; i < cells.length; i++) for (let j = i + 1; j < cells.length; j++) {
            const a = cells[i]; const b = cells[j];
            if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1) failures.push('overlapping variant fields');
          }
          for (const input of row.querySelectorAll('input[inputmode="decimal"]')) {
            if (input.clientWidth < 68) failures.push('price input too narrow');
          }
        }
        for (const element of document.querySelectorAll('.shop-admin-product-header h4, .shop-admin-product-flags, .shop-admin-spec strong')) {
          if (visible(element) && element.scrollWidth > element.clientWidth + 1) failures.push('clipped product information');
        }
        return failures;
      });
      assert.deepEqual(problems, [], `layout at ${width}px`);
      await expect(sourceProduct.locator('.shop-admin-margin strong').first()).toHaveText('¥10.50');
      const effective = await box(sourceProduct.locator('.shop-admin-margin strong').first());
      const regular = await box(sourceProduct.locator('.shop-admin-margin small').filter({ hasText: '原价毛利 ¥20.50' }));
      assert(regular.y >= effective.y + effective.height - 1, 'regular margin stays below effective margin');
      if (width <= 768) {
        const heading = await box(sourceProduct.locator('.shop-admin-product-header'));
        const firstRow = await box(sourceProduct.locator('.shop-admin-row').first());
        assert(firstRow.y >= heading.y + heading.height, 'narrow screens stack source and variants');
      }
      if (width <= 390) {
        await expect(sourceProduct.locator('.shop-admin-price .shop-admin-mobile-label').first()).toBeVisible();
        await expect(sourceProduct.locator('.shop-admin-promo .shop-admin-mobile-label').first()).toHaveText('学生价');
      } else {
        await expect(sourceProduct.locator('.shop-admin-columns')).toBeVisible();
      }
      if (screenshotDir && [1024, 320].includes(width)) {
        await plus.evaluate(element => window.scrollTo(0, window.scrollY + element.getBoundingClientRect().top - 128));
        await page.screenshot({ path: path.join(screenshotDir, `admin-${width}.png`) });
      }
    }

    // Reflow and settings disclosure must preserve in-progress edits and comparison navigation.
    await page.getByLabel('售价 2:7', { exact: true }).fill('130');
    await page.getByRole('button', { name: '商品设置 2', exact: true }).click();
    await page.getByLabel('商品说明 2', { exact: true }).fill('尚未保存的说明草稿');
    await page.getByRole('button', { name: '商品设置 2', exact: true }).click();
    await page.getByRole('group', { name: '规格筛选' }).getByRole('button', { name: /^来源售罄/ }).click();
    await expect(page.getByLabel('售价 2:7', { exact: true })).toBeHidden();
    await plus.locator('.shop-plan-best').click();
    await expect(page.locator('#shop-admin-product-2')).toBeFocused();
    await expect(page.getByLabel('售价 2:7', { exact: true })).toHaveValue('130');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('button', { name: '商品设置 2', exact: true }).click();
    await expect(page.getByLabel('商品说明 2', { exact: true })).toHaveValue('尚未保存的说明草稿');
    await expect(page.getByLabel('上架 2:7', { exact: true })).toBeChecked();
    await expect(page.getByLabel('优惠价 2:7', { exact: true })).toHaveValue('118.00');
    await page.getByLabel('新品标记 2', { exact: true }).selectOption('14');
    for (const width of [1024, 900, 800, 769, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      const overflow = await sourceProduct.locator('.shop-admin-display').evaluate(el => [...el.querySelectorAll('input,select')].some(control => {
        const box = control.getBoundingClientRect();
        const field = control.closest('.shop-admin-display-field').getBoundingClientRect();
        return box.right > field.right - 6 || box.left < field.left + 6;
      }));
      assert.equal(overflow, false, `storefront settings controls fit ${width}px`);
    }
    console.log('PASS: shop admin hierarchy, seven responsive widths, readable price controls, draft preservation and comparison navigation');
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
