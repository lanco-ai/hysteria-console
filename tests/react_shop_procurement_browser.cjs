const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

const GEMINI = 'fictional-gemini';
const MUSE = 'fictional-muse';

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const base = process.env.PREVIEW_BASE_URL;
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    // Leaving 商品管理 with unsaved input asks first; these checks continue.
    page.on('dialog', dialog => void dialog.accept());
    const merchant = { revision: 1, telegram: '', wechat: '', items: [], skus: {}, sources: {},
      last_success: 1, error: null, is_stale: false, cooldown_seconds: 0, retry_after_seconds: 0 };
    const quotedAt = Math.floor(Date.now() / 1000) - 600;
    // Both out of stock: the catalog still knows their prices.
    const data = { revision: 0, enabled: true, scheduler_enabled: true, balance_cents: 400, balance_at: quotedAt, balance_error: 'invalid_response',
      products: [
        { id: GEMINI, title: 'Gemini Pro 18 个月', quote: { price_cents: 85, stock: null, in_stock: false, quoted_at: quotedAt } },
        { id: MUSE, title: 'Muse AI', quote: { price_cents: 100, stock: null, in_stock: false, quoted_at: quotedAt } },
      ], jobs: [] };
    const requests = [];
    await page.route('**/api/v1/shop/admin', route => route.fulfill({ json: merchant }));
    await page.route('**/api/v1/shop/admin/procurement**', async route => {
      const request = route.request();
      const suffix = new URL(request.url()).pathname.split('/procurement')[1];
      requests.push([request.method(), suffix, request.method() === 'POST' ? request.postDataJSON() : null]);
      if (request.method() === 'GET') return route.fulfill({ json: data });
      const payload = request.postDataJSON();
      if (suffix === '/balance') {
        data.balance_error = ''; data.balance_cents = 500; data.balance_at = Math.floor(Date.now() / 1000);
        data.products[0].quote = { price_cents: 85, stock: 3, in_stock: true, quoted_at: data.balance_at };
      } else if (suffix === '/preorders') data.jobs.unshift({ ...payload, title: payload.product_id === MUSE ? 'Muse AI' : 'Gemini Pro 18 个月', status: 'draft', reason: '', checked_at: 0, order_id: null, amount_cents: null, delivered_count: 0 });
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

    // 采购预订 is its own page in the sidebar, not a switch inside 商品管理.
    await page.goto(base + '/admin/shop');
    await expect(page.getByRole('button', { name: '采购预订' })).toHaveCount(0);
    const wechat = page.getByLabel('商家微信号');
    await wechat.fill('demo_shop');
    await page.getByRole('link', { name: '采购预订', exact: true }).click();
    await expect(page).toHaveURL(`${base}/admin/shop?view=procurement`);
    await expect(page.getByRole('link', { name: '采购预订', exact: true })).toHaveAttribute('aria-current', 'page');
    await expect(page.locator('.page-title')).toContainText('采购预订');
    const panel = page.getByRole('region', { name: '采购预订', exact: true });
    await expect(panel.getByText('暂无预订')).toBeVisible();
    await expect(panel.locator('.shop-procurement-banner')).toContainText('还有未保存的修改');
    assert.equal(requests.filter(([method]) => method === 'POST').length, 0);

    // Prices show while out of stock; an explicit refresh adds the live stock count.
    const gemini = panel.locator('.shop-procurement-product').filter({ hasText: 'Gemini Pro 18 个月' });
    const muse = panel.locator('.shop-procurement-product').filter({ hasText: 'Muse AI' });
    await expect(gemini).toContainText('0.85 USDT');
    await expect(gemini).toContainText('缺货');
    await expect(muse).toContainText('1.00 USDT');
    await expect(muse).toContainText('缺货');
    await expect(panel).toContainText('缺货时同样显示供应商价格');
    await expect(panel.locator('.shop-procurement-balance')).toContainText('上次成功查询于');
    await expect(panel.locator('.shop-procurement-balance')).toContainText('供应商余额格式异常，显示上次余额');
    await panel.getByRole('button', { name: '刷新余额和报价', exact: true }).click();
    await expect(panel.locator('.shop-procurement-balance')).not.toContainText('上次余额');
    await expect(panel.locator('.shop-procurement-balance')).toContainText('5.00 USDT');
    await expect(gemini).toContainText('库存 3');

    await muse.click();
    await expect(muse.getByRole('radio')).toBeChecked();
    await panel.getByLabel('购买数量', { exact: true }).fill('2');
    await panel.getByRole('button', { name: '按现价 1.00 填入触发单价', exact: true }).click();
    await expect(panel.getByLabel('触发单价（USDT）', { exact: true })).toHaveValue('1.00');
    await expect(panel.locator('.shop-procurement-estimate')).toContainText('条件金额 2.00 USDT（2 × 1.00）');
    await panel.getByLabel('触发单价（USDT）', { exact: true }).fill('3.00');
    await expect(panel.locator('.shop-procurement-estimate')).toContainText('高于当前余额');
    await panel.getByLabel('触发单价（USDT）', { exact: true }).fill('1.25');
    const deadline = panel.getByLabel('截止时间（本地）', { exact: true });
    await panel.getByRole('button', { name: '3 天后', exact: true }).click();
    const chosen = new Date(await deadline.inputValue()).getTime();
    assert.ok(Math.abs(chosen - (Date.now() + 3 * 86400000)) < 120000, 'quick deadline is three days out');
    await panel.getByRole('button', { name: '保存预订草稿', exact: true }).click();
    await expect(panel.locator('.shop-procurement-status')).toHaveText('草稿');
    const created = requests.find(([, suffix]) => suffix === '/preorders')[2];
    assert.equal(created.product_id, MUSE);
    assert.equal(created.quantity, 2);
    assert.equal(created.trigger_price_cents, 125);
    assert.equal(requests.filter(([, suffix]) => suffix.endsWith('/enable')).length, 0);

    // Enabling asks inside the preorder's own card.
    await panel.getByRole('button', { name: '启用预订', exact: true }).click();
    const job = panel.locator('.shop-procurement-job').first();
    const confirm = job.getByRole('group', { name: '确认启用预订' });
    const activate = confirm.getByRole('button', { name: '确认启用并允许扣款' });
    await expect(activate).toBeDisabled();
    await expect(confirm).toContainText('2.50 USDT');
    await confirm.getByRole('checkbox').check();
    await activate.click();
    await expect(panel.locator('.shop-procurement-status')).toHaveText('等待到货');
    await expect(panel.locator('.shop-procurement-records')).toContainText('未完成 1');
    await panel.getByRole('button', { name: '暂停', exact: true }).click();
    await expect(panel.locator('.shop-procurement-status')).toHaveText('已暂停');

    for (const width of [1440, 1024, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `fits ${width}px`);
      const fields = await panel.locator('.shop-procurement-form input:not([type="radio"])').evaluateAll(nodes => nodes.every(node => node.getBoundingClientRect().width >= 80));
      assert(fields, `readable inputs at ${width}px`);
      const layout = await panel.evaluate(node => {
        const form = node.querySelector('.shop-procurement-form').getBoundingClientRect();
        const records = node.querySelector('.shop-procurement-records').getBoundingClientRect();
        return { formRight: form.right, recordsLeft: records.left, formBottom: form.bottom, recordsTop: records.top };
      });
      if (width === 1440) assert.ok(layout.formRight <= layout.recordsLeft, `form beside records at ${width}px`);
      if (width <= 768) assert.ok(layout.formBottom <= layout.recordsTop, `form above records at ${width}px`);
      if ([1440, 390].includes(width) && process.env.REACT_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, `procurement-${width}.png`), fullPage: true });
      }
    }
    await page.setViewportSize({ width: 1440, height: 1000 });

    // Back in 商品管理 the unsaved edit is still there.
    await page.getByRole('link', { name: '商品管理', exact: true }).click();
    await expect(page).toHaveURL(`${base}/admin/shop`);
    await expect(page.getByLabel('商家微信号')).toHaveValue('demo_shop');

    // Delivery must be absent from the list response and DOM until explicitly revealed.
    data.jobs[0] = { ...data.jobs[0], status: 'delivered', order_id: 'a'.repeat(24), amount_cents: 250, delivered_count: 2 };
    await page.goto(base + '/admin/shop?view=procurement');
    await expect(panel.locator('.shop-procurement-status')).toHaveText('采购完成');
    await expect(panel.getByRole('button', { name: '启用预订', exact: true })).toHaveCount(0);
    await expect(panel.locator('textarea')).toHaveCount(0);
    await panel.getByRole('button', { name: '查看交付内容（2 件）', exact: true }).click();
    await expect(panel.locator('textarea')).toHaveValue('fictional-account|fictional-password');
    await panel.getByRole('button', { name: '隐藏交付内容', exact: true }).click();
    await expect(panel.locator('textarea')).toHaveCount(0);
    data.jobs[0].status = 'uncertain'; data.jobs[0].reason = 'result_uncertain';
    await page.reload();
    await expect(panel).toContainText('系统不会再次下单');
    await expect(panel.getByRole('button', { name: '启用预订', exact: true })).toHaveCount(0);
    assert.equal(requests.filter(([, suffix]) => suffix.endsWith('/enable')).length, 1);

    // A fresh mobile visit straight to the page.
    data.jobs[0] = { ...data.jobs[0], status: 'waiting', reason: 'stock_low', order_id: null, amount_cents: null, delivered_count: 0 };
    await page.setViewportSize({ width: 390, height: 1000 });
    await page.goto(base + '/admin/shop?view=procurement');
    await expect(panel.locator('.shop-procurement-status')).toHaveText('等待到货');
    await expect(panel).toContainText('库存不足，等待数量齐全后一次购买。');
    const visibleFields = await panel.locator('input, h2').evaluateAll(nodes => nodes.every(node => {
      const box = node.getBoundingClientRect();
      return box.left >= 0 && box.right <= innerWidth;
    }));
    assert(visibleFields, 'fresh mobile page keeps labels and fields within viewport');
    if (process.env.REACT_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, 'procurement-mobile-full.png'), fullPage: true });
    assert.deepEqual(errors, []);
    console.log('PASS: procurement page in the sidebar, out-of-stock prices, live stock refresh, current-price fill, estimate and balance warning, draft, in-card activation, pause, layouts, private delivery, uncertainty and unsaved product edits');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
