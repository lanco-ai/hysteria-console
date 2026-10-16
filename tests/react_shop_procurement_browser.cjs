const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const base = process.env.PREVIEW_BASE_URL;
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const page = await context.newPage();
    const merchant = { revision: 1, telegram: '', wechat: '', items: [], skus: {}, sources: {},
      last_success: 1, error: null, is_stale: false, cooldown_seconds: 0, retry_after_seconds: 0 };
    const data = { revision: 0, enabled: true, scheduler_enabled: true, balance_cents: null, balance_at: null,
      balance_error: '', products: [{ id: 'fictional-gemini', title: 'Gemini Pro 18 个月' }], jobs: [] };
    const requests = [];
    await page.route('**/api/v1/shop/admin', route => route.fulfill({ json: merchant }));
    await page.route('**/api/v1/shop/admin/procurement**', async route => {
      const request = route.request();
      const suffix = new URL(request.url()).pathname.split('/procurement')[1];
      requests.push([request.method(), suffix]);
      if (request.method() === 'GET') return route.fulfill({ json: data });
      const payload = request.postDataJSON();
      if (suffix === '/balance') { data.balance_cents = 500; data.balance_at = Math.floor(Date.now() / 1000); }
      else if (suffix === '/preorders') data.jobs.unshift({ ...payload, title: 'Gemini Pro 18 个月', status: 'draft', reason: '', checked_at: 0, order_id: null, amount_cents: null, delivered_count: 0 });
      else {
        const action = suffix.split('/').at(-1);
        if (action === 'reveal') return route.fulfill({ json: { keys: ['fictional-account|fictional-password'] } });
        if (action === 'enable') { assert.equal(payload.accept_supplier_price, true); data.jobs[0].status = 'waiting'; }
        if (action === 'pause') data.jobs[0].status = 'paused';
        if (action === 'cancel') data.jobs[0].status = 'cancelled';
      }
      data.revision++;
      return route.fulfill({ json: data });
    });
    await page.goto(base + '/admin/shop');
    await page.getByRole('button', { name: '采购预订', exact: true }).click();
    const panel = page.getByRole('region', { name: '采购预订', exact: true });
    await expect(panel.getByText('暂无预订。先保存草稿，再单独启用。')).toBeVisible();
    assert.equal(requests.filter(([method]) => method === 'POST').length, 0);
    await panel.getByRole('button', { name: '查询余额', exact: true }).click();
    await expect(panel.locator('.shop-procurement-balance')).toContainText('5.00 USDT');
    await panel.getByLabel('购买数量', { exact: true }).fill('2');
    await panel.getByLabel('触发单价（USDT）', { exact: true }).fill('1.25');
    await panel.getByRole('button', { name: '保存预订草稿', exact: true }).click();
    await expect(panel.locator('.shop-procurement-status')).toHaveText('草稿');
    assert.equal(requests.filter(([, suffix]) => suffix.endsWith('/enable')).length, 0);
    await panel.getByRole('button', { name: '启用预订', exact: true }).click();
    const confirm = panel.getByRole('group', { name: '确认启用预订' });
    const activate = confirm.getByRole('button', { name: '确认启用并允许扣款' });
    await expect(activate).toBeDisabled();
    await expect(confirm).toContainText('2.50 USDT');
    await confirm.getByRole('checkbox').check();
    await activate.click();
    await expect(panel.locator('.shop-procurement-status')).toHaveText('等待到货');
    await panel.getByRole('button', { name: '暂停', exact: true }).click();
    await expect(panel.locator('.shop-procurement-status')).toHaveText('已暂停');

    for (const width of [1440, 1024, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `fits ${width}px`);
      const fields = await panel.locator('.shop-procurement-form input').evaluateAll(nodes => nodes.every(node => node.getBoundingClientRect().width >= 80));
      assert(fields, `readable inputs at ${width}px`);
      if ([1440, 390].includes(width) && process.env.REACT_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
        await panel.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, `procurement-${width}.png`) });
      }
    }
    // Delivery must be absent from the list response and DOM until explicitly revealed.
    data.jobs[0] = { ...data.jobs[0], status: 'delivered', order_id: 'a'.repeat(24), amount_cents: 250, delivered_count: 2 };
    await page.getByRole('button', { name: '采购预订', exact: true }).click();
    await page.getByRole('button', { name: '采购预订', exact: true }).click();
    await expect(panel.locator('.shop-procurement-status')).toHaveText('采购完成');
    await expect(panel.getByRole('button', { name: '启用预订', exact: true })).toHaveCount(0);
    await expect(panel.locator('textarea')).toHaveCount(0);
    await panel.getByRole('button', { name: '查看交付内容（2 件）', exact: true }).click();
    await expect(panel.locator('textarea')).toHaveValue('fictional-account|fictional-password');
    await panel.getByRole('button', { name: '隐藏交付内容', exact: true }).click();
    await expect(panel.locator('textarea')).toHaveCount(0);
    data.jobs[0].status = 'uncertain'; data.jobs[0].reason = 'result_uncertain';
    await page.getByRole('button', { name: '采购预订', exact: true }).click();
    await page.getByRole('button', { name: '采购预订', exact: true }).click();
    await expect(panel).toContainText('系统不会再次下单');
    await expect(panel.getByRole('button', { name: '启用预订', exact: true })).toHaveCount(0);
    assert.equal(requests.filter(([, suffix]) => suffix.endsWith('/enable')).length, 1);
    // A fresh mobile visit, including the surrounding overview and fixed save bar.
    data.jobs[0] = { ...data.jobs[0], status: 'waiting', reason: 'stock_low', order_id: null, amount_cents: null, delivered_count: 0 };
    await page.setViewportSize({ width: 390, height: 1000 });
    await page.goto(base + '/admin/shop');
    await page.getByRole('button', { name: '采购预订', exact: true }).click();
    await expect(panel.locator('.shop-procurement-status')).toHaveText('等待到货');
    await expect(page.locator('.shop-admin-savebar')).toHaveCSS('position', 'static');
    const visibleFields = await panel.locator('input, select, h2').evaluateAll(nodes => nodes.every(node => {
      const box = node.getBoundingClientRect();
      return box.left >= 0 && box.right <= innerWidth;
    }));
    assert(visibleFields, 'fresh mobile page keeps labels and fields within viewport');
    if (process.env.REACT_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, 'procurement-mobile-full.png'), fullPage: true });
    console.log('Procurement UI: draft, explicit activation, pause, layouts, private delivery and uncertainty passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
