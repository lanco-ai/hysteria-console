const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

const base = process.env.PREVIEW_BASE_URL;
function fixture() {
  const plans = ['Plus', 'Go', 'Pro 100', 'Pro 200', 'Pro 500'];
  return { currency: 'CNY', telegram: '', wechat: 'demo_shop', status: 'ready', updated_at: 1000,
    products: plans.map((plan, i) => ({ id: String(i + 1), title: `ChatGPT ${plan}`, category: 'GPT', badge: '限时价',
      description: '使用自己的账号兑换。\n\n' + '商品说明，请在购买前确认适用条件。\n\n'.repeat(12), after_sales: '质保30天，请联系本店。',
      variants: [{ id: `${i + 1}:1`, label: '一个月', price_cents: 12800, promo_price_cents: 11800, available: true },
        { id: `${i + 1}:2`, label: '其他规格', price_cents: 12900, available: true }],
    })).concat([{ id: '9', title: 'Claude Pro', category: 'Claude', badge: '', description: '', after_sales: '',
      variants: [{ id: '9:1', label: '标准规格', price_cents: 15300, available: true }] }]),
  };
}

// Records each view transition: its kind and the elements named to morph.
function recordTransitions() {
  window.__transitions = [];
  const start = Document.prototype.startViewTransition;
  if (!start) return;
  Document.prototype.startViewTransition = function (...args) {
    const names = [...document.querySelectorAll('[data-shop-card], [data-shop-hero], [data-shop-logo], [data-shop-title]')]
      .map(node => node.style.viewTransitionName).filter(Boolean);
    window.__transitions.push({ kind: document.documentElement.dataset.shopTransition, names });
    return start.apply(this, args);
  };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext();
    await context.addInitScript(recordTransitions);
    const page = await context.newPage();
    let reads = 0;
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/v1/shop/catalog', route => { reads += 1; return route.fulfill({ json: fixture() }); });
    await page.route('**/api/v1/shop/visit', route => route.fulfill({ json: { ok: true } }));
    const storefront = page.locator('.shop-storefront');
    const productPage = page.locator('.shop-product-page');
    const heading = page.locator('#shop-detail-title');
    const back = page.getByRole('link', { name: '商品目录', exact: true });
    const transitions = () => page.evaluate(() => window.__transitions);
    const settled = () => expect.poll(() => page.evaluate(() => document.documentElement.dataset.shopTransition ?? null)).toBe(null);
    const shots = process.env.REACT_SCREENSHOT_DIR;
    async function screenshot(name) {
      if (!shots) return;
      fs.mkdirSync(shots, { recursive: true });
      await page.screenshot({ path: path.join(shots, name) });
    }

    for (const width of [390, 768, 1440]) {
      await page.setViewportSize({ width, height: 850 });
      await page.goto(base);
      await page.getByRole('button', { name: /^GPT / }).click();
      await page.getByRole('searchbox', { name: '搜索商品' }).fill('ChatGPT');
      const trigger = page.getByRole('link', { name: 'ChatGPT Pro 200', exact: true });
      const card = page.locator('[data-shop-card="4"]');
      await card.scrollIntoViewIfNeeded();
      const savedY = await page.evaluate(() => { window.__catalogNode = document.querySelector('.shop-catalog'); return scrollY; });
      if (width === 390) assert.ok(savedY > 100, 'exercise a scrolled mobile catalog');
      const beforeReads = reads;

      // Anywhere on the card opens the product: here, beside its price.
      const priceBox = await card.locator('.shop-product-price').boundingBox();
      const cardBox = await card.boundingBox();
      await card.click({ position: { x: priceBox.x - cardBox.x + 4, y: priceBox.y - cardBox.y + 4 } });
      await expect(page).toHaveURL(`${base}/?product=4`);
      await expect(heading).toHaveText(/ChatGPT Pro 200/);
      await expect(heading).toBeFocused();
      await expect(storefront).toBeHidden();
      await settled();
      assert.equal(reads, beforeReads, 'opening a product reuses the loaded catalog');
      assert.ok(await page.evaluate(() => window.__catalogNode === document.querySelector('.shop-catalog')), 'the catalog stays mounted');
      assert.equal(await page.evaluate(() => scrollY), 0, 'a product page starts at its top');
      const opened = (await transitions()).at(-1);
      assert.equal(opened.kind, 'open');
      assert.deepEqual(opened.names.sort(), ['shop-card', 'shop-card-logo', 'shop-card-title'], 'the card morphs into the page header');
      await expect(page.getByRole('button', { name: '立即购买', exact: true })).toBeInViewport();
      await page.evaluate(() => scrollTo({ top: document.documentElement.scrollHeight, behavior: 'instant' }));
      await expect(page.getByRole('button', { name: '立即购买', exact: true })).toBeInViewport();
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `product page fits ${width}px`);
      await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
      if (width === 390 || width === 1440) await screenshot(`product-page-${width}.png`);
      const historyLength = await page.evaluate(() => history.length);

      // The brand's other plans swap in place, without new history entries.
      const plans = page.getByRole('navigation', { name: 'GPT 其他商品' });
      await expect(plans.getByRole('link')).toHaveText(['Plus', 'Go', 'Pro 100', 'Pro 200', 'Pro 500']);
      await expect(plans.getByRole('link', { name: 'Pro 200', exact: true })).toHaveAttribute('aria-current', 'page');
      await page.getByLabel('购买数量', { exact: true }).fill('3');
      await plans.getByRole('link', { name: 'Pro 500', exact: true }).click();
      await expect(page).toHaveURL(`${base}/?product=5`);
      await expect(heading).toHaveText(/ChatGPT Pro 500/);
      await expect(page.getByLabel('购买数量', { exact: true })).toHaveValue('1');
      await expect(plans.getByRole('link', { name: 'Pro 500', exact: true })).toHaveAttribute('aria-current', 'page');
      await settled();
      assert.equal((await transitions()).at(-1).kind, 'next');
      await plans.getByRole('link', { name: 'Pro 200', exact: true }).click();
      await expect(heading).toHaveText(/ChatGPT Pro 200/);
      await settled();
      assert.equal((await transitions()).at(-1).kind, 'previous');
      assert.equal(await page.evaluate(() => history.length), historyLength, 'plans replace the product entry');

      // Back to the catalog: same filter, search, scroll position and focus.
      await back.click();
      await expect(page).toHaveURL(`${base}/`);
      await expect(storefront).toBeVisible();
      await expect(productPage).toHaveCount(0);
      await expect(trigger).toBeFocused();
      await expect(page.getByRole('searchbox', { name: '搜索商品' })).toHaveValue('ChatGPT');
      await expect(page.getByRole('button', { name: /^GPT / })).toHaveAttribute('aria-pressed', 'true');
      await expect.poll(() => page.evaluate(() => scrollY)).toBeGreaterThanOrEqual(savedY - 2);
      assert.ok(Math.abs((await page.evaluate(() => scrollY)) - savedY) <= 2, 'closing restores the catalog position');
      await settled();
      const closed = (await transitions()).at(-1);
      assert.equal(closed.kind, 'close');
      assert.deepEqual(closed.names.sort(), ['shop-card', 'shop-card-logo', 'shop-card-title'], 'the page header shrinks into its card');
      assert.equal(reads, beforeReads);

      // Browser history moves between the same two views.
      await page.goForward();
      await expect(heading).toHaveText(/ChatGPT Pro 200/);
      await settled();
      await page.goBack();
      await expect(storefront).toBeVisible();
      await expect.poll(() => page.evaluate(y => Math.abs(scrollY - y) <= 2, savedY)).toBe(true);
      await settled();

      // Purchases still confirm the current catalog, and focus returns to the button.
      await trigger.click();
      await expect(heading).toBeFocused();
      await page.getByRole('button', { name: '立即购买', exact: true }).click();
      const purchase = page.getByRole('dialog', { name: '购买信息', exact: true });
      await expect(purchase.getByLabel('可复制的购买信息')).toHaveValue(/ChatGPT Pro 200/);
      assert.ok(reads > beforeReads, 'purchase still verifies the current catalog');
      await page.keyboard.press('Escape');
      await expect(purchase).toHaveCount(0);
      await expect(page.getByRole('button', { name: '立即购买', exact: true })).toBeFocused();
      await page.getByRole('button', { name: '加入购物车', exact: true }).click();
      await expect(productPage.getByRole('status')).toContainText('已加入购物车');
      await expect(page.getByRole('link', { name: /^购物车（/ })).toHaveText('购物车（1）');
      await back.click();
      await expect(storefront).toBeVisible();
      await page.evaluate(() => localStorage.clear());
    }

    // Only one brand plan: no plan switcher.
    await page.goto(`${base}/?product=9`);
    await expect(heading).toHaveText('Claude Pro');
    await expect(page.locator('.shop-plans')).toHaveCount(0);

    // A shared link stays usable; closing it goes to the catalog, never another site.
    await page.goto(`${base}/?product=1#plus-referral`);
    await expect(page.locator('#plus-referral')).toBeFocused();
    await page.reload();
    await expect(page.locator('#plus-referral')).toBeFocused();
    const sharedLength = await page.evaluate(() => history.length);
    await back.click();
    await expect(page).toHaveURL(`${base}/`);
    await expect(storefront).toBeVisible();
    assert.equal(await page.evaluate(() => history.length), sharedLength, 'the shared entry becomes the catalog');
    await page.goto(`${base}/?product=99999`);
    await expect(page.getByRole('heading', { name: '商品不存在或已下架' })).toBeVisible();
    await page.getByRole('button', { name: '返回商品目录' }).click();
    await expect(storefront).toBeVisible();

    // Repeated close commands must not step back past the catalog.
    await page.getByRole('link', { name: 'ChatGPT Plus', exact: true }).click();
    await expect(heading).toHaveText(/ChatGPT Plus/);
    await settled();
    await back.evaluate(link => { link.click(); link.click(); });
    await expect(page).toHaveURL(`${base}/`);
    await expect(storefront).toBeVisible();
    await settled();

    // A reload keeps the catalog position.
    await page.setViewportSize({ width: 390, height: 700 });
    await page.evaluate(() => scrollTo({ top: 600, behavior: 'instant' }));
    await expect.poll(() => page.evaluate(() => scrollY)).toBe(600);
    await expect.poll(() => page.evaluate(() => history.state?.__lancoaiScrollY)).toBe(600);
    await page.reload();
    await expect(page.locator('.shop-product-row').first()).toBeVisible();
    await expect.poll(() => page.evaluate(() => scrollY)).toBe(600);

    // A selected plan reached through the referral anchor must not add history.
    await page.goto(`${base}/?product=1#plus-referral`);
    await expect(page.locator('#plus-referral')).toBeFocused();
    const planHistory = await page.evaluate(() => history.length);
    await page.getByRole('navigation', { name: 'GPT 其他商品' }).getByRole('link', { name: 'Plus', exact: true }).click();
    await expect(page).toHaveURL(`${base}/?product=1`);
    assert.equal(await page.evaluate(() => history.length), planHistory);

    // Leaving for SMS unmounts the catalog. Restore only after its next fetch.
    await page.goto(base);
    await expect(page.locator('[data-shop-card="4"]')).toBeVisible();
    await page.evaluate(() => scrollTo({ top: 600, behavior: 'instant' }));
    await expect.poll(() => page.evaluate(() => history.state?.__lancoaiScrollY)).toBe(600);
    await page.getByRole('link', { name: '短信接码', exact: true }).evaluate(link => link.click());
    await expect(page).toHaveURL(`${base}/?view=sms`);
    await page.goBack();
    await expect(page.locator('[data-shop-card="4"]')).toBeVisible();
    await expect.poll(() => page.evaluate(() => scrollY)).toBe(600);

    // The router and rendered page agree about unknown view query parameters.
    await page.goto(`${base}/?view=shop&product=1`);
    await expect(heading).toHaveText(/ChatGPT Plus/);
    await back.click();
    await expect(storefront).toBeVisible();
    await settled();

    // The selected plan can be absent from the retained catalog filter.
    await page.getByRole('searchbox', { name: '搜索商品' }).fill('ChatGPT Plus');
    await page.getByRole('link', { name: 'ChatGPT Plus', exact: true }).click();
    await expect(heading).toHaveText(/ChatGPT Plus/);
    await settled();
    await page.getByRole('navigation', { name: 'GPT 其他商品' }).getByRole('link', { name: 'Go', exact: true }).click();
    await expect(heading).toHaveText(/ChatGPT Go/);
    await settled();
    await back.click();
    await expect(storefront).toBeVisible();
    await expect(page.locator('#shop-catalog-title')).toBeFocused();
    await expect(page.getByRole('searchbox', { name: '搜索商品' })).toHaveValue('ChatGPT Plus');
    await settled();
    await page.getByRole('searchbox', { name: '搜索商品' }).fill('');

    // Rapid clicks may interrupt a snapshot. Only the last product wins.
    await page.evaluate(() => {
      document.querySelector('[data-shop-card="1"] .shop-product-title a').click();
      document.querySelector('[data-shop-card="2"] .shop-product-title a').click();
    });
    await expect(heading).toHaveText(/ChatGPT Go/);
    await settled();
    await back.click();
    await expect(storefront).toBeVisible();
    await settled();

    // Less motion: the views switch without an animation.
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const count = (await transitions()).length;
    await page.getByRole('link', { name: 'ChatGPT Go', exact: true }).click();
    await expect(heading).toHaveText(/ChatGPT Go/);
    await back.click();
    await expect(storefront).toBeVisible();
    assert.equal((await transitions()).length, count, 'no view transition when less motion is preferred');
    assert.equal(await page.evaluate(() => document.documentElement.dataset.shopTransition ?? null), null);
    // Browsers without View Transitions keep all navigation functional.
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.evaluate(() => { document.startViewTransition = undefined; });
    await page.getByRole('link', { name: 'ChatGPT Go', exact: true }).click();
    await expect(heading).toHaveText(/ChatGPT Go/);
    await back.click();
    await expect(storefront).toBeVisible();
    await expect(page.getByRole('link', { name: 'ChatGPT Go', exact: true })).toBeFocused();
    assert.equal((await transitions()).length, count);
    assert.deepEqual(errors, []);
    console.log('PASS: card grows into the product page and back, whole-card target, retained catalog/filter/scroll/focus, no repeat load, plan switching without history entries, share URL, history, reload position, nested purchase, persistent actions, mobile and reduced motion');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
