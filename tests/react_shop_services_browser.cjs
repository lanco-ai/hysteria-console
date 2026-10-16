const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

const base = process.env.PREVIEW_BASE_URL;
// 2026-10-31 23:59:59 in Beijing: the student service's last moment.
const STUDENT_ENDS_AT = 1793462399;
const variant = (id, price) => ({ id, label: '标准规格', price_cents: price, available: true, quantity: null, sales: null });
const catalog = { currency: 'CNY', telegram: 'demo_shop', wechat: '', status: 'ready', updated_at: 1000, products: [
  { id: '1', title: 'ChatGPT Plus', category: 'GPT', description: '使用自己的账号兑换。', after_sales: '', variants: [variant('1:1', 12800)] },
  { id: '6000000000003', title: 'ChatGPT 学生认证', category: 'GPT', description: '2026 年 10 月 31 日截止，到期后自动下架。', after_sales: '', ends_at: STUDENT_ENDS_AT, variants: [variant('6000000000003:1', 9900)] },
  { id: '6000000000002', title: 'VPN 搭建指导', category: 'VPN', description: '本店不提供 VPN、节点或账号。', after_sales: '', variants: [{ ...variant('6000000000002:1', 19900), label: '远程搭建指导' }] },
] };

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/v1/shop/catalog', route => route.fulfill({ json: catalog }));
    await page.route('**/api/v1/shop/visit', route => route.fulfill({ json: { ok: true } }));
    await page.goto(base);

    // The VPN service has its own category, after the AI products, with its own picture.
    await expect(page.getByRole('button', { name: /^VPN / })).toBeVisible();
    await expect(page.locator('.shop-group-heading').last()).toHaveText('VPN 商品（1）');
    const vpn = page.locator('[data-shop-card="6000000000002"]');
    await expect(vpn.getByRole('img', { name: 'VPN 搭建' })).toBeVisible();
    await expect(vpn.locator('.shop-ends-badge')).toHaveCount(0);

    // The student service says when it ends, on its card and its page.
    const student = page.locator('[data-shop-card="6000000000003"]');
    await expect(student.locator('.shop-ends-badge')).toHaveText('10月31日截止');
    await student.getByRole('link', { name: 'ChatGPT 学生认证', exact: true }).click();
    await expect(page.locator('#shop-detail-title')).toHaveText('ChatGPT 学生认证');
    await expect(page.locator('.shop-detail-heading .shop-ends-badge')).toHaveText('10月31日截止');
    await expect(page.getByRole('navigation', { name: 'GPT 其他商品' }).getByRole('link')).toHaveText(['Plus', '学生认证']);
    await expect.poll(() => page.evaluate(() => document.documentElement.dataset.shopTransition ?? null)).toBe(null);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'product page fits 390px');
    if (process.env.REACT_SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, 'service-student-390.png') });
    }
    await page.getByRole('link', { name: '商品目录', exact: true }).click();
    await page.getByRole('button', { name: /^VPN / }).click();
    await expect(page.locator('.shop-product-row')).toHaveCount(1);
    await vpn.getByRole('link', { name: 'VPN 搭建指导', exact: true }).click();
    await expect(page.getByText('本店不提供 VPN、节点或账号。')).toBeVisible();
    await expect(page.locator('.shop-plans')).toHaveCount(0);
    assert.deepEqual(errors, []);
    console.log('PASS: VPN guidance category and picture, student service deadline on card and page, GPT plan switcher, mobile fit');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
