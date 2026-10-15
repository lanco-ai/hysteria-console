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
    })),
  };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    let reads = 0;
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/v1/shop/catalog', route => { reads += 1; return route.fulfill({ json: fixture() }); });
    await page.route('**/api/v1/shop/visit', route => route.fulfill({ json: { ok: true } }));
    const sheet = page.getByRole('dialog', { name: '商品详情', exact: true });
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
      await trigger.scrollIntoViewIfNeeded();
      await trigger.focus();
      const savedY = await page.evaluate(() => { window.__catalogNode = document.querySelector('.shop-catalog'); return scrollY; });
      if (width === 390) assert.ok(savedY > 100, 'exercise a scrolled mobile catalog');
      const beforeReads = reads;
      await trigger.click();
      await expect(sheet).toBeVisible();
      await expect(sheet.getByRole('heading', { name: /ChatGPT Pro 200/ })).toBeFocused();
      assert.equal(reads, beforeReads, 'opening details reuses the loaded catalog');
      assert.ok(await page.evaluate(() => window.__catalogNode === document.querySelector('.shop-catalog')), 'catalog stays mounted');
      await expect(sheet.getByRole('button', { name: '立即购买', exact: true })).toBeInViewport();
      await sheet.locator('.shop-detail-scroll').evaluate(node => { node.scrollTop = node.scrollHeight; });
      await expect(sheet.getByRole('button', { name: '立即购买', exact: true })).toBeInViewport();
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      for (let tab = 0; tab < 14; tab += 1) {
        await page.keyboard.press('Tab');
        // Native dialogs may move Tab to browser chrome (activeElement=body),
        // but must never focus the inert catalog underneath.
        assert.ok(await sheet.evaluate(node => document.activeElement === document.body || node.contains(document.activeElement)), 'Tab cannot reach the catalog');
      }
      await sheet.locator('.shop-detail-scroll').evaluate(node => { node.scrollTop = 0; });
      if (width === 390 || width === 1440) await screenshot(`details-${width}.png`);
      await page.keyboard.press('Escape');
      await expect(sheet).not.toBeVisible();
      await expect(page).toHaveURL(`${base}/`);
      await expect(trigger).toBeFocused();
      await expect(page.getByRole('searchbox', { name: '搜索商品' })).toHaveValue('ChatGPT');
      await expect(page.getByRole('button', { name: /^GPT / })).toHaveAttribute('aria-pressed', 'true');
      assert.ok(Math.abs((await page.evaluate(() => scrollY)) - savedY) <= 2, 'close restores catalog position');
      assert.equal(reads, beforeReads);
      await page.goForward();
      await expect(sheet).toBeVisible();
      await page.goBack();
      await expect(sheet).not.toBeVisible();
      await trigger.click();
      await sheet.getByRole('button', { name: '立即购买', exact: true }).click();
      const purchase = page.getByRole('dialog', { name: '购买信息', exact: true });
      await expect(purchase.getByLabel('可复制的购买信息')).toHaveValue(/ChatGPT Pro 200/);
      assert.ok(reads > beforeReads, 'purchase still verifies current catalog');
      await page.keyboard.press('Escape');
      await expect(purchase).toHaveCount(0);
      await expect(sheet).toBeVisible();
      await expect(sheet.getByRole('button', { name: '立即购买', exact: true })).toBeFocused();
      await sheet.getByRole('button', { name: '加入购物车', exact: true }).click();
      await expect(sheet.getByRole('status')).toContainText('已加入购物车');
      await sheet.getByRole('button', { name: '继续选购', exact: true }).click();
      await expect(sheet).not.toBeVisible();
    }

    // A shared URL stays usable and closes to the catalog, never another site.
    await page.goto(`${base}/?product=1#plus-referral`);
    await expect(sheet.locator('#plus-referral')).toBeFocused();
    await page.reload();
    await expect(sheet.locator('#plus-referral')).toBeFocused();
    await sheet.getByRole('button', { name: '关闭商品详情' }).click();
    await expect(page).toHaveURL(`${base}/`);
    await expect(sheet).not.toBeVisible();
    await page.goto(`${base}/?product=99999`);
    await expect(sheet.getByRole('heading', { name: '商品不存在或已下架' })).toBeVisible();
    await sheet.getByRole('button', { name: '返回商品目录' }).click();
    await expect(sheet).not.toBeVisible();
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.getByRole('link', { name: 'ChatGPT Plus', exact: true }).click();
    await expect(sheet).toBeVisible();
    assert.equal(await sheet.evaluate(node => getComputedStyle(node).animationName), 'none');
    await page.keyboard.press('Escape');
    await expect(sheet).not.toBeVisible();
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.getByRole('link', { name: 'ChatGPT Plus', exact: true }).click();
    await expect(sheet).toBeVisible();
    // Repeated close commands must not step back past the catalog.
    await sheet.getByRole('button', { name: '关闭商品详情' }).evaluate(button => { button.click(); button.click(); });
    await expect(page).toHaveURL(`${base}/`);
    await expect(sheet).not.toBeVisible();
    await page.getByRole('link', { name: 'ChatGPT Plus', exact: true }).click();
    await expect(sheet).toBeVisible();
    await page.mouse.click(10, 300);
    await expect(sheet).not.toBeVisible();
    assert.deepEqual(errors, []);
    console.log('PASS: retained catalog/filter/scroll/focus, no repeat load, share URL, history, native modality, nested purchase, persistent actions, mobile and reduced motion');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
