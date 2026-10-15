const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

(async () => {
  const base = process.env.PREVIEW_BASE_URL;
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  let externalRequests = 0;
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== base) { externalRequests++; return route.abort(); }
      return route.continue();
    });
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const page = await context.newPage();
    const readAdmin = async () => (await context.request.get(`${base}/api/v1/shop/admin`)).json();
    const catalog = async () => (await context.request.get(`${base}/api/v1/shop/catalog`)).json();
    const directSave = async changes => {
      const current = await readAdmin();
      const payload = { revision: current.revision, client: 3, telegram: current.telegram,
        wechat: current.wechat, skus: current.skus, products: current.products,
        display: current.display, local_names: current.local_names, ...changes };
      return context.request.put(`${base}/api/v1/shop/admin`, { data: payload, headers: { 'Sec-Fetch-Site': 'same-origin' } });
    };
    const save = page.getByRole('button', { name: '保存商品设置', exact: true });
    const settings = page.getByRole('button', { name: '来源设置', exact: true });
    const panel = page.getByRole('group', { name: '来源读取设置', exact: true });
    const gpt = page.getByRole('switch', { name: '读取 强云', exact: true });
    const fanai = page.getByRole('switch', { name: '读取 Fanai', exact: true });
    const sms = page.getByRole('switch', { name: '读取 Zephyr', exact: true });
    const chip = page.locator('.shop-source-chip').filter({ hasText: '强云' });
    const pro = page.getByRole('article', { name: 'ChatGPT Pro 200', exact: true });
    const sourceProduct = page.locator('section[aria-labelledby="shop-admin-product-2"]');
    const onSale = page.locator('.shop-admin-kpis > div').filter({ has: page.getByText('在售商品', { exact: true }) }).locator('dd');
    const unpriced = page.locator('.shop-admin-kpis > div').filter({ has: page.getByText('待定价规格', { exact: true }) }).locator('dd');
    const baseline = await readAdmin();
    await page.goto(`${base}/admin/shop`);
    await settings.click();
    await expect(panel.getByRole('switch')).toHaveCount(3);
    await expect(page.getByRole('switch', { name: /本店/ })).toHaveCount(0);
    await expect(gpt).toBeChecked();
    await expect(page.locator('.shop-source-chip').filter({ hasText: 'Zephyr' })).toContainText('暂无商品');
    await expect(onSale).toHaveText('3');
    await expect(pro.locator('.shop-plan-warn')).toContainText('2 个来源同时上架');
    const initialUnpriced = Number(await unpriced.textContent());

    // A draft switch cannot claim that the live feed has already stopped.
    await gpt.uncheck();
    await expect(panel).toContainText('未保存');
    await expect(chip).not.toHaveClass(/is-disabled/);
    await expect(sourceProduct).toBeVisible();
    assert.equal((await catalog()).products.some(item => item.id === '2'), true);
    await page.getByRole('button', { name: '刷新来源', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('未保存输入已保留');
    await expect(gpt).not.toBeChecked();
    await expect(chip).not.toHaveClass(/is-disabled/);
    await save.click();
    await expect(page.getByRole('status')).toContainText('已保存');
    await expect(chip).toHaveClass(/is-disabled/);
    await expect(sourceProduct).toHaveCount(0);
    await expect(page.getByRole('group', { name: '规格筛选', exact: true }).getByRole('button', { name: /^全部 / })).toHaveText('全部 1');
    await page.getByRole('searchbox', { name: '搜索商品或规格', exact: true }).fill('2:7');
    await expect(page.getByText('没有符合条件的规格。', { exact: true })).toBeVisible();
    await expect(sourceProduct).toHaveCount(0);
    await page.getByRole('searchbox', { name: '搜索商品或规格', exact: true }).fill('');
    await expect(pro.locator('.shop-plan-head .badge.ok')).toHaveText('前台 ¥1150.00');
    await expect(pro.locator('.shop-plan-warn')).toHaveCount(0);
    await expect(pro.locator('.shop-plan-best')).toHaveCount(0);
    await expect(onSale).toHaveText('2');
    assert.equal(Number(await unpriced.textContent()), initialUnpriced - baseline.items.filter(row => row.provider === 'gpt' && baseline.skus[row.key]?.price_cents == null).length);
    let current = await readAdmin();
    assert.deepEqual(current.source_enabled, { gpt: false });
    assert.deepEqual(current.items, baseline.items);
    assert.deepEqual(current.skus['2:7'], baseline.skus['2:7']);
    assert.deepEqual(current.products['2'], baseline.products['2']);
    assert.deepEqual(current.display, baseline.display);
    assert.deepEqual(current.local_names, baseline.local_names);
    assert.equal((await catalog()).products.some(item => item.id === '2'), false);

    // An older client omitting the new field cannot silently re-enable it.
    assert.equal((await directSave({})).status(), 200);
    assert.deepEqual((await readAdmin()).source_enabled, { gpt: false });
    await page.reload();
    await settings.click();
    await expect(gpt).not.toBeChecked();
    await expect(sourceProduct).toHaveCount(0);

    // Save failure and a concurrent edit retain the draft and original revision.
    await gpt.check();
    await page.route('**/api/v1/shop/admin', route => route.request().method() === 'PUT'
      ? route.fulfill({ status: 503, json: { error: 'storage_unavailable' } }) : route.continue());
    await save.click();
    await expect(page.getByRole('alert')).toContainText('请求失败');
    await expect(gpt).toBeChecked();
    await expect(chip).toHaveClass(/is-disabled/);
    await expect(sourceProduct).toHaveCount(0);
    await page.unroute('**/api/v1/shop/admin');
    assert.equal((await directSave({ source_enabled: {} })).status(), 200);
    await page.getByRole('button', { name: '刷新来源', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('未保存输入已保留');
    await expect(gpt).toBeChecked();
    await expect(chip).toHaveClass(/is-disabled/);
    await expect(onSale).toHaveText('2');
    await save.click();
    await expect(page.getByRole('alert')).toContainText('已被其他页面修改');
    await expect(gpt).toBeChecked();
    page.once('dialog', dialog => dialog.dismiss());
    await page.getByRole('button', { name: '重新读取', exact: true }).click();
    await expect(chip).toHaveClass(/is-disabled/);
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: '重新读取', exact: true }).click();
    await expect(chip).not.toHaveClass(/is-disabled/);
    await expect(onSale).toHaveText('3');
    assert.equal((await catalog()).products.find(item => item.id === '2').variants[0].price_cents, 120000);

    // All external sources may be off; store-owned goods and name editing remain available.
    await gpt.uncheck(); await fanai.uncheck(); await sms.uncheck();
    await save.click();
    await expect(page.getByRole('status')).toContainText('已保存');
    await expect(onSale).toHaveText('1');
    assert.deepEqual((await catalog()).products.map(item => item.id), ['6000000000001']);
    assert.equal((await catalog()).status, 'ready');
    await expect(page.locator('.shop-admin-product')).toHaveCount(1);
    await expect(page.getByRole('group', { name: '品牌', exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: '商品设置 6000000000001', exact: true }).click();
    await expect(page.getByLabel('商品名称 6000000000001', { exact: true })).toHaveValue('本人账号开通指导');
    await expect(page.getByLabel('规格名称 6000000000001:1', { exact: true })).toHaveValue('微软邮箱指导');
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      await expect(panel).toBeVisible();
      if (width < 880) {
        await expect.poll(() => page.locator('.sidebar').evaluate(sidebar => sidebar.getBoundingClientRect().right))
          .toBeLessThanOrEqual(0);
      }
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      if (process.env.REACT_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, `source-controls-${width}.png`), fullPage: true, animations: 'disabled' });
      }
    }
    await gpt.check(); await fanai.check(); await sms.check();
    await save.click();
    await expect(page.getByRole('status')).toContainText('已保存');
    assert.equal((await catalog()).products.length, 3);
    assert.equal((await readAdmin()).source_enabled, undefined);
    await expect(sourceProduct).toBeVisible();
    await expect(page.getByLabel('售价 2:7', { exact: true })).toHaveValue('1200.00');
    current = await readAdmin();
    assert.deepEqual(current.skus['2:7'], baseline.skus['2:7']);
    assert.deepEqual(current.products['2'], baseline.products['2']);
    assert.deepEqual(current.display, baseline.display);

    // Previous API projections without either optional enable field are read as enabled.
    const legacy = await readAdmin();
    delete legacy.source_enabled;
    for (const source of Object.values(legacy.sources)) delete source.enabled;
    await page.route('**/api/v1/shop/admin', route => route.fulfill({ json: legacy }));
    await page.reload(); await settings.click();
    await expect(gpt).toBeChecked(); await expect(fanai).toBeChecked();
    await page.evaluate(() => document.fonts.ready);
    await page.waitForLoadState('networkidle');
    assert.deepEqual(errors, []);
    assert.equal(externalRequests, 0);
    console.log('PASS: disabled feeds absent from admin rows/search/counts, saved-switch semantics, clean re-enable with retained settings, drafts/conflicts, local names, 1440/390 layout');
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
