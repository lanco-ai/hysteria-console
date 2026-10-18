const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

const ID = '6000000000001';
const FIRST = `${ID}:1`;
const SECOND = `${ID}:2`;

(async () => {
  const base = process.env.PREVIEW_BASE_URL;
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  let externalRequests = 0;
  const shots = process.env.REACT_SCREENSHOT_DIR;
  async function shot(page, name) {
    if (!shots) return;
    fs.mkdirSync(shots, { recursive: true });
    await page.screenshot({ path: path.join(shots, name), fullPage: true });
  }
  async function isolatedContext(options) {
    const context = await browser.newContext(options);
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== base) { externalRequests++; return route.abort(); }
      return route.continue();
    });
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    return context;
  }
  try {
    const admin = await isolatedContext({ viewport: { width: 1440, height: 1000 } });
    await admin.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const manager = await admin.newPage();
    const saves = [];
    manager.on('request', request => {
      if (request.method() === 'PUT' && request.url().endsWith('/api/v1/shop/admin')) saves.push(request.postDataJSON());
    });
    const product = manager.locator(`section[aria-labelledby="shop-admin-product-${ID}"]`);
    const toggle = manager.getByRole('button', { name: `商品设置 ${ID}`, exact: true });
    const title = manager.getByLabel(`商品名称 ${ID}`, { exact: true });
    const first = manager.getByLabel(`规格名称 ${FIRST}`, { exact: true });
    const second = manager.getByLabel(`规格名称 ${SECOND}`, { exact: true });
    const save = manager.getByRole('button', { name: '保存商品设置', exact: true });
    const readAdmin = async () => (await admin.request.get(`${base}/api/v1/shop/admin`)).json();
    async function showLocal() {
      await manager.getByRole('group', { name: '品牌', exact: true }).getByRole('button', { name: /^Claude / }).click();
      if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
    }

    await manager.goto(`${base}/admin/shop`);
    // Source-fed products retain their original read-only names.
    await manager.getByRole('button', { name: '商品设置 2', exact: true }).click();
    await expect(manager.getByLabel('商品名称 2', { exact: true })).toHaveCount(0);
    await expect(manager.getByLabel('规格名称 2:7', { exact: true })).toHaveCount(0);
    const baseline = await readAdmin();
    await manager.getByRole('group', { name: '品牌', exact: true }).getByRole('button', { name: /^Claude / }).click();
    await expect(product.locator('.shop-admin-supplier')).toHaveText('本店');
    await expect(title).toHaveCount(0);
    await toggle.click();
    await expect(title).toHaveValue('Claude 开通指导');
    await expect(first).toHaveValue('微软邮箱');
    await expect(second).toHaveValue('谷歌邮箱');
    await save.click();
    await expect(manager.getByRole('status')).toContainText('已保存');
    assert.equal((await readAdmin()).local_names, undefined, 'default names must not opt old settings into a new schema');

    await manager.getByLabel('商家 Telegram 用户名').fill('shop_owner');
    await title.fill('Claude 本人账号');
    await title.pressSequentially('开通指导');
    await expect(title).toBeFocused();
    await expect(title).toHaveValue('Claude 本人账号开通指导');
    await first.fill('微软邮箱操作指导');
    await second.fill('谷歌邮箱操作指导');
    await expect(toggle).toContainText('已修改');
    await expect(manager.locator('.shop-admin-savebar')).toContainText('名称');
    await expect(product.locator('h4')).toHaveText('Claude 开通指导');
    await manager.getByLabel(`售价 ${FIRST}`, { exact: true }).fill('123.45');
    await manager.getByLabel(`优惠价 ${FIRST}`, { exact: true }).fill('110');
    await manager.getByLabel(`上架 ${FIRST}`, { exact: true }).check();
    await manager.getByLabel(`售价 ${SECOND}`, { exact: true }).fill('156.78');
    await manager.getByLabel(`上架 ${SECOND}`, { exact: true }).check();
    await manager.getByLabel(`优惠标签 ${ID}`, { exact: true }).selectOption('限时价');
    await manager.getByRole('button', { name: '刷新来源', exact: true }).click();
    await expect(manager.getByRole('status')).toContainText('未保存输入已保留');
    await expect(title).toHaveValue('Claude 本人账号开通指导');
    await expect(first).toHaveValue('微软邮箱操作指导');
    await expect(manager.getByLabel(`优惠标签 ${ID}`, { exact: true })).toHaveValue('限时价');
    manager.once('dialog', dialog => dialog.dismiss());
    await manager.getByRole('button', { name: '重新读取', exact: true }).click();
    await expect(title).toHaveValue('Claude 本人账号开通指导');

    const beforeInvalid = saves.length;
    for (const invalid of ['  ', '<b>商品</b>', '隐藏\u202e方向']) {
      await title.fill(invalid);
      await save.click();
      await expect(manager.getByRole('alert')).toContainText('商品名称');
      assert.equal(saves.length, beforeInvalid);
    }
    await title.fill('Claude 本人账号开通指导');
    await first.fill('');
    await save.click();
    await expect(manager.getByRole('alert')).toContainText('规格名称');
    assert.equal(saves.length, beforeInvalid);
    await first.fill('微软邮箱操作指导');
    await save.click();
    await expect(manager.getByRole('status')).toContainText('已保存');
    const persisted = await readAdmin();
    assert.deepEqual(persisted.local_names, {
      products: { [ID]: 'Claude 本人账号开通指导' },
      variants: { [FIRST]: '微软邮箱操作指导', [SECOND]: '谷歌邮箱操作指导' },
    });
    assert.deepEqual(persisted.items.filter(row => row.provider !== 'local'), baseline.items.filter(row => row.provider !== 'local'));
    assert.deepEqual(persisted.products[ID], baseline.products[ID]);
    assert.deepEqual(persisted.items.filter(row => row.product_id === ID).map(row => [row.key, row.cost_cents]), [[FIRST, 8500], [SECOND, 10500]]);
    assert.equal(saves.at(-1).client, 3);
    await expect(toggle).not.toContainText('已修改');

    await manager.reload();
    await showLocal();
    await expect(title).toHaveValue('Claude 本人账号开通指导');
    await expect(first).toHaveValue('微软邮箱操作指导');
    await expect(product.locator('h4')).toHaveText('Claude 本人账号开通指导');
    await manager.getByLabel('搜索商品或规格').fill('微软邮箱操作指导');
    await expect(product).toBeVisible();
    await expect(manager.getByLabel(`售价 ${FIRST}`, { exact: true })).toBeVisible();
    await manager.getByLabel('搜索商品或规格').fill('');

    // A failed request retains name drafts; a later conflict must not overwrite another save.
    await title.fill('尚未保存的操作指导');
    await manager.route('**/api/v1/shop/admin', route => route.request().method() === 'PUT'
      ? route.fulfill({ status: 503, json: { error: 'storage_unavailable' } }) : route.continue());
    await save.click();
    await expect(manager.getByRole('alert')).toContainText('请求失败');
    await expect(title).toHaveValue('尚未保存的操作指导');
    await manager.unroute('**/api/v1/shop/admin');
    const concurrentStatus = await manager.evaluate(async ({ id }) => {
      const current = await (await fetch('/api/v1/shop/admin')).json();
      const payload = { revision: current.revision, client: 3, telegram: current.telegram,
        wechat: current.wechat, skus: current.skus, display: current.display, products: current.products,
        local_names: { ...current.local_names, products: { [id]: '另一个页面保存的指导' } } };
      return (await fetch('/api/v1/shop/admin', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).status;
    }, { id: ID });
    assert.equal(concurrentStatus, 200);
    await manager.getByRole('button', { name: '刷新来源', exact: true }).click();
    await expect(manager.getByRole('status')).toContainText('未保存输入已保留');
    await expect(title).toHaveValue('尚未保存的操作指导');
    await expect(product.locator('h4')).toHaveText('Claude 本人账号开通指导');
    await save.click();
    await expect(manager.getByRole('alert')).toContainText('已被其他页面修改');
    await expect(title).toHaveValue('尚未保存的操作指导');
    assert.equal((await readAdmin()).local_names.products[ID], '另一个页面保存的指导');
    manager.once('dialog', dialog => dialog.accept());
    await manager.getByRole('button', { name: '重新读取', exact: true }).click();
    await expect(title).toHaveValue('另一个页面保存的指导');
    await title.fill('Claude 本人账号开通指导');
    await save.click();
    await expect(manager.getByRole('status')).toContainText('已保存');

    const publicContext = await isolatedContext({ permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await publicContext.newPage();
    for (const width of [1440, 390]) {
      await manager.setViewportSize({ width, height: 1000 });
      await showLocal();
      await expect(product.locator('.shop-admin-supplier')).toHaveText('本店');
      await expect(product.locator('.shop-admin-cost strong')).toHaveText(['¥85.00', '¥105.00']);
      assert(await manager.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await shot(manager, `local-names-admin-${width}.png`);
      await page.setViewportSize({ width, height: 1000 });
      await page.goto(`${base}/`);
      const catalog = await (await publicContext.request.get(`${base}/api/v1/shop/catalog`)).json();
      const local = catalog.products.find(item => item.id === ID);
      assert.equal(local.title, 'Claude 本人账号开通指导');
      assert.deepEqual(local.variants.map(item => item.id), [FIRST, SECOND]);
      assert.equal(/cost_cents|source_url|local_names/.test(JSON.stringify(catalog)), false);
      await expect(page.getByRole('link', { name: local.title, exact: true })).toBeVisible();
      await page.getByRole('link', { name: local.title, exact: true }).click();
      await expect(page.locator('.shop-detail-heading h1')).toContainText(local.title);
      await expect(page.locator('.shop-detail-heading h1 .shop-promo-badge')).toHaveText('限时价');
      await expect(page.getByRole('button', { name: '微软邮箱操作指导', exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: '谷歌邮箱操作指导', exact: true })).toBeVisible();
      await expect(page.locator('.shop-detail-price .shop-promo-price')).toHaveText('限时价：¥110.00');
      await expect(page.locator('.shop-detail-copy')).toContainText('本人账号');
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await shot(page, `local-names-detail-${width}.png`);
      await page.getByRole('button', { name: '加入购物车', exact: true }).click();
      await page.goto(`${base}/?view=cart`);
      await expect(page.locator('.shop-cart-row')).toContainText('Claude 本人账号开通指导');
      await expect(page.locator('.shop-cart-row')).toContainText('微软邮箱操作指导');
      await expect(page.locator('.shop-cart-row .shop-promo-price')).toHaveText('限时价：¥110.00');
      await page.getByRole('button', { name: '确认购买信息', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: '购买信息' });
      const message = dialog.getByLabel('可复制的购买信息');
      await expect(message).toHaveValue(/Claude 本人账号开通指导/);
      await expect(message).toHaveValue(/微软邮箱操作指导/);
      await expect(message).toHaveValue(/限时价 ¥110\.00/);
      await dialog.getByRole('button', { name: '复制购买信息', exact: true }).click();
      await expect(dialog).toContainText('购买信息已复制');
      assert.match(await page.evaluate(() => navigator.clipboard.readText()), /Claude 本人账号开通指导/);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await shot(page, `local-names-cart-copy-${width}.png`);
      await page.keyboard.press('Escape');
    }
    // The existing custom promo-label editor still saves independently of local names.
    await manager.getByLabel(`优惠标签 ${ID}`, { exact: true }).selectOption('__custom');
    await manager.getByLabel(`自定义标签 ${ID}`, { exact: true }).fill('指导优惠');
    await save.click();
    await expect(manager.getByRole('status')).toContainText('已保存');
    await manager.reload();
    await showLocal();
    await expect(manager.getByLabel(`自定义标签 ${ID}`, { exact: true })).toHaveValue('指导优惠');
    await expect(title).toHaveValue('Claude 本人账号开通指导');
    assert.equal((await readAdmin()).display[ID].badge, '指导优惠');
    // A merchant-owned product remains its own plan even when a source plan has the same name.
    await title.fill('Claude Pro｜美区官方 iOS 充值 · 掉订阅全程质保');
    await save.click();
    await expect(manager.getByRole('status')).toContainText('已保存');
    const matchingPlans = manager.getByRole('article', { name: 'Claude Pro｜美区官方 iOS 充值 · 掉订阅全程质保', exact: true });
    await expect(matchingPlans).toHaveCount(2);
    const localPlan = matchingPlans.filter({ has: manager.getByRole('button', { name: `商品设置 ${ID}`, exact: true }) });
    await expect(localPlan.locator('.shop-admin-product')).toHaveCount(1);
    await expect(localPlan.locator('.shop-plan-best')).toHaveCount(0);
    await expect(localPlan.locator('.shop-plan-warn')).toHaveCount(0);
    await expect(localPlan.locator('.shop-admin-supplier')).toHaveText('本店');
    await title.fill('Claude 本人账号开通指导');
    await save.click();
    await expect(manager.getByRole('status')).toContainText('已保存');
    assert.deepEqual(errors, []);
    assert.equal(externalRequests, 0);
    console.log('PASS: local-only names, stable identity, editable promo label, save/reload/refresh/conflict, real catalog/detail/cart/copy at 1440 and 390px.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
