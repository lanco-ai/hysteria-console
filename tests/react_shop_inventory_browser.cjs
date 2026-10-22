const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

const GEMINI = '2000000000001:1';
const MUSE = '2000000000002:1';
const SMS = '5000000000001:1';

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const base = process.env.PREVIEW_BASE_URL;
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', dialog => void dialog.accept());
    const merchant = { revision: 1, telegram: 'demo_shop', wechat: '', sources: {}, last_success: 1, error: null, is_stale: false, cooldown_seconds: 0, retry_after_seconds: 0,
      skus: { [MUSE]: { price_cents: 1888, published: true } }, inventory: {},
      items: [
        { key: GEMINI, product_id: '2000000000001', title: 'Gemini Pro 18Months (link) · $0.85', public_title: 'Gemini Pro 18 个月', label: '标准规格', cost_cents: 612, available: false, provider: 'prodseller', category: 'Gemini' },
        { key: MUSE, product_id: '2000000000002', title: 'Muse AI · $1.00', public_title: 'Muse AI', label: '标准规格', cost_cents: 720, available: false, provider: 'prodseller', category: 'Muse AI' },
        { key: SMS, product_id: '5000000000001', title: '短信接码激活码', public_title: '短信接码激活码', label: '单次接码', cost_cents: 200, available: true, provider: 'sms', category: '短信接码' },
        { key: '6000000000011:1', product_id: '6000000000011', title: 'Muse AI Token 帮拉取', public_title: 'Muse AI Token 帮拉取', label: '10 亿 Token / 份', cost_cents: 0, available: true, provider: 'local', category: 'Muse AI' },
      ] };
    // A fictional stand-in for the private inventory API: lines only ever leave on reveal or delivery.
    let revision = 0, nextId = 0, failAdd = 0, addRequests = 0;
    const units = [];
    const requests = [];
    const view = () => {
      const skus = {};
      for (const unit of units) {
        const entry = skus[unit.sku] ??= { stock: [], delivered: [] };
        entry[unit.delivered_at ? 'delivered' : 'stock'].push({ id: unit.id, added_at: unit.added_at, delivered_at: unit.delivered_at, note: unit.note });
      }
      return { revision, skus };
    };
    await page.route('**/api/v1/shop/admin', route => route.fulfill({ json: merchant }));
    await page.route('**/api/v1/shop/admin/inventory**', route => {
      const request = route.request();
      const suffix = new URL(request.url()).pathname.split('/inventory')[1];
      const payload = request.method() === 'POST' ? request.postDataJSON() : null;
      requests.push([request.method(), suffix, payload]);
      if (request.method() === 'GET') return route.fulfill({ json: view() });
      if (suffix === '/add') {
        addRequests += 1;
        assert.ok(Buffer.byteLength(request.postData(), 'utf8') <= 65536, 'import respects the server byte limit');
        if (failAdd && addRequests === failAdd) return route.fulfill({ status: 503, json: { error: 'unavailable' } });
        if (payload.revision !== revision) return route.fulfill({ status: 409, json: { error: 'conflict' } });
        const known = new Set(units.filter(unit => unit.sku === payload.sku).map(unit => unit.line));
        let added = 0;
        for (const line of payload.lines) if (!known.has(line)) { known.add(line); added += 1; units.push({ id: String(++nextId).padStart(32, '0'), sku: payload.sku, line, added_at: 1800000000 + nextId, delivered_at: 0, note: '' }); }
        revision += 1;
        return route.fulfill({ json: { ...view(), added, skipped: payload.lines.length - added } });
      }
      if (suffix === '/deliver') {
        const unit = units.find(item => item.sku === payload.sku && !item.delivered_at);
        Object.assign(unit, { delivered_at: 1800001000, note: payload.note });
        revision += 1;
        return route.fulfill({ json: { ...view(), delivered: { id: unit.id, line: unit.line, delivered_at: unit.delivered_at, note: unit.note } } });
      }
      const [, , id, action] = suffix.split('/');
      const unit = units.find(item => item.id === id);
      if (action === 'reveal') return route.fulfill({ json: { line: unit.line } });
      if (action === 'remove') units.splice(units.indexOf(unit), 1);
      if (action === 'restock') Object.assign(unit, { delivered_at: 0, note: '' });
      revision += 1;
      return route.fulfill({ json: view() });
    });

    await page.goto(base + '/admin/shop');
    await expect(page.locator('.sidebar-nav .sidebar-link')).toHaveText(['商品管理', '本店库存', '采购预订']);
    await page.getByRole('link', { name: '本店库存', exact: true }).click();
    await expect(page).toHaveURL(`${base}/admin/shop?view=inventory`);
    const panel = page.getByRole('region', { name: '本店库存', exact: true });
    await expect(panel.locator('.shop-inventory-product h2')).toHaveText(['Gemini Pro 18 个月', 'Muse AI', '短信接码激活码']);
    await expect(panel.getByRole('article', { name: 'Muse AI Token 帮拉取 库存' })).toHaveCount(0);
    const muse = panel.getByRole('article', { name: 'Muse AI 库存' });
    const gemini = panel.getByRole('article', { name: 'Gemini Pro 18 个月 库存' });
    await expect(muse).toContainText('已上架 · ¥18.88');
    await expect(gemini).toContainText('未上架');
    await expect(muse.getByRole('button', { name: '发货一件', exact: true })).toBeDisabled();

    // Drafts and delivery notes survive sidebar navigation, but never enter browser storage.
    const secretDraft = 'draft-only@example.invalid | keep-this';
    await muse.getByLabel('添加库存').fill(secretDraft);
    await muse.getByLabel('发货备注').fill('草稿备注');
    await page.getByRole('link', { name: '商品管理', exact: true }).click();
    await page.getByRole('link', { name: '采购预订', exact: true }).click();
    await page.getByRole('link', { name: '本店库存', exact: true }).click();
    await expect(muse.getByLabel('添加库存')).toHaveValue(secretDraft);
    await expect(muse.getByLabel('发货备注')).toHaveValue('草稿备注');
    assert.equal(await page.evaluate(() => {
      const event = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(event); return event.defaultPrevented;
    }), true, 'leaving the admin session warns about unsaved private input');
    assert.equal(await page.evaluate(text => JSON.stringify([localStorage, sessionStorage]).includes(text), secretDraft), false);
    await muse.getByLabel('发货备注').fill('');

    // Paste a batch; an exact repeat is skipped.
    await muse.getByLabel('添加库存').fill('first@example.invalid | pw1\n\nsecond@example.invalid | pw2\nfirst@example.invalid | pw1\n');
    await muse.getByRole('button', { name: '添加 3 件到库存', exact: true }).click();
    await expect(panel.getByRole('status')).toContainText('Muse AI：已添加 2 件，跳过 1 件重复');
    await expect(muse.locator('.shop-inventory-counts')).toContainText('现货2件');
    await expect(muse.getByLabel('添加库存')).toHaveValue('');
    assert.equal(units.filter(unit => unit.sku === MUSE).length, 2);
    assert.equal((await page.content()).includes('first@example.invalid'), false, 'lines stay off the page until asked for');

    // Delivering gives the buyer the account and how to take it over.
    await muse.getByLabel('发货备注').fill('买家 wx_buyer');
    await muse.getByRole('button', { name: '发货一件', exact: true }).click();
    const sent = muse.getByLabel('Muse AI 发货内容');
    await expect(sent).toHaveValue(/^Muse AI 账号：first@example\.invalid \| pw1\n/);
    for (const step of ['A）更改邮箱：「联络资料」→ 增加你的电邮；验证新电邮后，删除原旧电邮。', 'B）更改密码：输入新密码，并勾选「如有其他人使用了你的帐户，请选择此选项」，登出其他装置。', 'C）换绑 2FA：进入「双重验证」，重新绑定为你自己的手机号或验证器 App（如 Google Authenticator）。']) {
      await expect(sent).toHaveValue(new RegExp(step.replace(/[()（）|.?+*[\]^$\\]/g, '\\$&')));
    }
    const delivery = requests.find(([, suffix]) => suffix === '/deliver')[2];
    assert.equal(delivery.sku, MUSE);
    assert.equal(delivery.note, '买家 wx_buyer');
    assert.match(delivery.request, /^[0-9a-f]{32}$/);
    await muse.getByRole('button', { name: '复制发货内容', exact: true }).click();
    assert.match(await page.evaluate(() => navigator.clipboard.readText()), /C）换绑 2FA/);
    await expect(muse.locator('.shop-inventory-counts')).toContainText('现货1件');
    await expect(muse.locator('.shop-inventory-counts')).toContainText('已发货1件');

    // Units are revealed one at a time; a mistake can be removed or returned.
    await muse.getByText('现货明细（1）').click();
    await muse.locator('.shop-inventory-details').first().getByRole('button', { name: '查看', exact: true }).click();
    await expect(muse.locator('.shop-inventory-details code')).toHaveText('second@example.invalid | pw2');
    await muse.locator('.shop-inventory-details').first().getByRole('button', { name: '删除', exact: true }).click();
    await expect(muse.locator('.shop-inventory-counts')).toContainText('现货0件');
    await muse.getByText('发货记录（1）').click();
    await expect(muse.locator('.shop-inventory-details').nth(1)).toContainText('买家 wx_buyer');
    await muse.locator('.shop-inventory-details').nth(1).getByRole('button', { name: '退回现货', exact: true }).click();
    await expect(muse.locator('.shop-inventory-counts')).toContainText('现货1件');
    await expect(muse.locator('.shop-inventory-counts')).toContainText('已发货0件');

    // Gemini takes links; unpublished stock warns that buyers cannot see it.
    await gemini.getByLabel('添加库存').fill('https://example.invalid/claim/1');
    await gemini.getByRole('button', { name: '添加 1 件到库存', exact: true }).click();
    await expect(gemini).toContainText('未上架：在商品管理上架后，顾客才能看到现货并下单。');
    await gemini.getByRole('button', { name: '发货一件', exact: true }).click();
    await expect(gemini.getByLabel('Gemini Pro 18 个月 发货内容')).toHaveValue(/^Gemini Pro 18 个月 领取链接：https:\/\/example\.invalid\/claim\/1\n请先登录你自己的 Google 账号/);

    // An SMS activation code ships with where to redeem it and how 二验、三验 work.
    const sms = panel.getByRole('article', { name: '短信接码激活码 库存' });
    await expect(sms.getByLabel('添加库存')).toHaveAttribute('placeholder', '每行一件，例如：\n激活码');
    await sms.getByLabel('添加库存').fill('FICTIONAL-SMS-CDK-0001');
    await sms.getByRole('button', { name: '添加 1 件到库存', exact: true }).click();
    await sms.getByRole('button', { name: '发货一件', exact: true }).click();
    await expect(sms.getByLabel('短信接码激活码 发货内容')).toHaveValue(new RegExp(`^短信接码激活码：FICTIONAL-SMS-CDK-0001\n使用：打开本店短信接码页面 ${base.replace(/[.]/g, '\\.')}/\\?view=sms ，输入激活码`));
    await expect(sms.getByLabel('短信接码激活码 发货内容')).toHaveValue(/二验、三验/);

    for (const width of [1440, 1024, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `fits ${width}px`);
      if ([1440, 390].includes(width) && process.env.REACT_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, `inventory-${width}.png`), fullPage: true });
      }
    }

    // 商品管理 shows the count beside the SKU and links to the stock.
    merchant.inventory = { [MUSE]: 1 };
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('link', { name: '商品管理', exact: true }).click();
    await page.locator('.shop-admin-brands').getByRole('button', { name: /^Muse AI/ }).click();
    const chip = page.locator('.shop-admin-stock');
    await expect(chip).toHaveText('现货 1');
    await chip.click();
    await expect(page).toHaveURL(`${base}/admin/shop?view=inventory`);

    // Byte-bounded imports: a failed later batch preserves only the pending lines.
    const bulk = Array.from({ length: 400 }, (_, index) => `https://example.invalid/redeem/${index}?token=${'测试"\\'.repeat(40)}`);
    await muse.getByLabel('添加库存').fill(bulk.join('\n'));
    const before = units.filter(unit => unit.sku === MUSE).length;
    failAdd = addRequests + 2;
    await muse.getByRole('button', { name: '添加 400 件到库存', exact: true }).click();
    await expect(panel.getByRole('alert')).toContainText('剩余输入已保留');
    const remaining = (await muse.getByLabel('添加库存').inputValue()).split('\n');
    assert.ok(remaining.length > 0 && remaining.length < 400);
    assert.equal(units.filter(unit => unit.sku === MUSE).length, before + 400 - remaining.length);
    await page.getByRole('link', { name: '商品管理', exact: true }).click();
    await page.getByRole('link', { name: '本店库存', exact: true }).click();
    await expect(muse.getByLabel('添加库存')).toHaveValue(remaining.join('\n'));
    failAdd = 0;
    await muse.getByRole('button', { name: `添加 ${remaining.length} 件到库存`, exact: true }).click();
    await expect(muse.getByLabel('添加库存')).toHaveValue('');
    assert.equal(units.filter(unit => unit.sku === MUSE).length, before + 400);
    assert.equal(new Set(units.filter(unit => unit.sku === MUSE).map(unit => unit.line)).size, before + 400);

    // The storefront sells own stock and shows how much there is.
    const shopper = await browser.newPage({ viewport: { width: 390, height: 844 } });
    shopper.on('pageerror', error => errors.push(error.message));
    await shopper.route('**/api/v1/shop/visit', route => route.fulfill({ json: { ok: true } }));
    await shopper.route('**/api/v1/shop/catalog', route => route.fulfill({ json: { currency: 'CNY', telegram: 'demo_shop', wechat: '', status: 'ready', updated_at: 1, products: [
      { id: '2000000000002', title: 'Muse AI', category: 'Muse AI', description: '', after_sales: '', variants: [{ id: MUSE, label: '标准规格', price_cents: 1888, available: true, quantity: 5, sales: null }] },
    ] } }));
    await shopper.goto(base);
    const card = shopper.locator('[data-shop-card="2000000000002"]');
    await expect(card.locator('.shop-product-availability')).toHaveText('现货 5 件');
    await card.getByRole('link', { name: 'Muse AI', exact: true }).click();
    await expect(shopper.locator('.shop-detail-stock')).toHaveText('现货 5 件');
    await expect(shopper.getByRole('button', { name: '立即购买', exact: true })).toBeEnabled();
    assert.deepEqual(errors, []);
    console.log('PASS: 本店库存 page in the sidebar, batch add with repeats skipped, private lines, delivery with Muse takeover steps and Gemini link text, reveal/remove/restock, unpublished hint, 商品管理 stock chip, storefront 现货 on card and page, five widths');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
