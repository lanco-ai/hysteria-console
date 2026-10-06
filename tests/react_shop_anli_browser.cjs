const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');
const base = process.env.PREVIEW_BASE_URL;
const screenshots = process.env.ANLI_SCREENSHOT_DIR;

async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const evidence = { task_id: 'anli-source-catalog-20261006', assertions: [], externalRequests: [], pageErrors: [] };
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    context.on('request', request => { if (!request.url().startsWith(base) && !request.url().startsWith('data:')) evidence.externalRequests.push(request.url()); });
    page.on('pageerror', error => evidence.pageErrors.push(error.message.slice(0, 160)));
    await page.goto(base);
    await expect(page.getByRole('heading', { name: '暂无商品' })).toBeVisible();
    const admin = await browser.newContext();
    await admin.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const manager = await admin.newPage();
    await manager.goto(`${base}/admin/shop`);
    await expect(manager.locator('.shop-admin-product')).toHaveCount(10);
    await expect(manager.locator('.shop-admin-row')).toHaveCount(12);
    await expect(manager.locator('.shop-admin-source-status p')).toHaveCount(2);
    for (const id of [5, 7, 8, 11, 10]) {
      const key = `${1000000000000 + id}:1`;
      await expect(manager.getByLabel(`售价 ${key}`, { exact: true })).toHaveValue('');
      await expect(manager.getByLabel(`上架 ${key}`, { exact: true })).not.toBeChecked();
    }
    evidence.assertions.push('five new products draft/no retail; original five GPT products/seven variants intact');
    await manager.getByLabel('商家 Telegram 用户名').fill('fixture_shop');
    for (const [key, price] of [['2:7', '1100.01'], ['2:2', '1200.02'], ['1000000000005:1', '155.00'], ['1000000000007:1', '850.00'], ['1000000000008:1', '1750.00'], ['1000000000011:1', '205.00'], ['1000000000010:1', '670.00']]) {
      await manager.getByLabel(`售价 ${key}`, { exact: true }).fill(price);
      await manager.getByLabel(`上架 ${key}`, { exact: true }).check();
    }
    const claudeGroup = manager.locator('.shop-admin-product').filter({ has: manager.getByLabel('售价 1000000000005:1', { exact: true }) });
    await claudeGroup.locator('summary').click();
    await manager.getByLabel('商品说明 1000000000005', { exact: true }).fill('本店 Claude 说明');
    await manager.getByLabel('售后条款 1000000000005', { exact: true }).fill('本店 Claude 售后');
    await manager.getByRole('button', { name: '保存商品设置' }).click();
    await expect(manager.getByRole('status')).toContainText('已保存');
    const catalogResponse = await page.request.get(`${base}/api/v1/shop/catalog`);
    const catalogText = await catalogResponse.text();
    const catalog = JSON.parse(catalogText);
    assert.equal(catalog.products.length, 6);
    assert.equal(/cost_cents|source_url|faka\.anligpt|supplier marketing|sources|order_sold/.test(catalogText), false);
    assert.ok(catalog.products.every(product => product.variants.every(variant => variant.quantity === null && variant.sales === null)));
    evidence.assertions.push('retail-only public projection; no upstream contacts, HTML, prices, URLs or sales');
    if (screenshots) fs.mkdirSync(screenshots, { recursive: true });
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(base);
      await expect(page.locator('.shop-product-row')).toHaveCount(6);
      for (const [category, count] of [['全部商品', 6], ['GPT', 1], ['Claude', 3], ['Grok', 2]]) {
        const button = page.locator('.shop-category-list button').filter({ hasText: category });
        await expect(button.locator('.shop-count')).toHaveText(String(count));
      }
      await expect(page.locator('.shop-group-heading')).toHaveText(['GPT 商品（1）', 'Claude 商品（3）', 'Grok 商品（2）']);
      await expect(page.getByRole('img', { name: 'ChatGPT', exact: true })).toHaveCount(1);
      await expect(page.getByRole('img', { name: 'Claude', exact: true })).toHaveCount(3);
      await expect(page.getByRole('img', { name: 'Grok', exact: true })).toHaveCount(2);
      const imageState = await page.locator('.shop-product-thumbnail img').evaluateAll(images => images.every(image => image.complete && image.naturalWidth > 0 && image.src.startsWith(location.origin)));
      assert.equal(imageState, true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      if (screenshots) await page.screenshot({ path: path.join(screenshots, `catalog-${width}.jpg`), type: 'jpeg', quality: 65, fullPage: true });
      await page.locator('.shop-category-list button').filter({ hasText: 'Claude' }).click();
      await expect(page.locator('.shop-product-row')).toHaveCount(3);
      await page.getByRole('searchbox', { name: '搜索商品' }).fill('20x');
      await expect(page.locator('.shop-product-row')).toHaveCount(1);
      await expect(page.getByRole('button', { name: 'Claude Max 20x 暂不可购买' })).toBeDisabled();
      await page.getByRole('link', { name: 'Claude Max 20x 商品详情', exact: true }).click();
      await expect(page.getByRole('button', { name: '加入购物车', exact: true })).toBeDisabled();
      await page.goto(`${base}/?product=1000000000005`);
      await expect(page.locator('.shop-detail h1')).toHaveText('Claude Pro');
      await expect(page.locator('.shop-detail-image img')).toHaveAttribute('alt', 'Claude');
      await expect(page.getByText('本店 Claude 说明', { exact: true })).toBeVisible();
      await expect(page.getByText('本店 Claude 售后', { exact: true })).toBeVisible();
      await page.getByLabel('购买数量', { exact: true }).fill('2');
      await expect(page.getByTestId('purchase-total')).toHaveText('¥310.00');
      if (screenshots) await page.screenshot({ path: path.join(screenshots, `detail-${width}.jpg`), type: 'jpeg', quality: 65, fullPage: true });
      await page.getByRole('button', { name: '加入购物车', exact: true }).click();
      await page.goto(base);
      await page.getByRole('button', { name: '将 Grok SuperGrok 加入购物车', exact: true }).click();
      await page.getByRole('link', { name: /^购物车（/ }).click();
      await expect(page.locator('.shop-cart-row')).toHaveCount(2);
      await expect(page.getByTestId('cart-total')).toHaveText('¥515.00');
      assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('hysteria.shop.cart.v1'))), [{ id: '1000000000005:1', quantity: 2 }, { id: '1000000000011:1', quantity: 1 }]);
      await page.getByRole('button', { name: '确认购买信息' }).click();
      await expect(page.getByLabel('可复制的购买信息')).toHaveValue(/Claude Pro[\s\S]*Grok SuperGrok[\s\S]*合计：¥515.00 CNY/);
      await page.keyboard.press('Escape');
      await page.getByRole('button', { name: '清空购物车' }).click();
      evidence.assertions.push(`${width}px category/search/brand/detail/soldout/cart/current purchase totals`);
    }
    await manager.getByLabel('售价 1000000000005:1', { exact: true }).fill('199.99');
    await manager.getByLabel('商品说明 1000000000005', { exact: true }).fill('未保存说明');
    await manager.getByLabel('售后条款 1000000000005', { exact: true }).fill('未保存售后');
    await manager.getByLabel('商家 Telegram 用户名').fill('unsaved_shop');
    await manager.getByRole('button', { name: '刷新来源' }).click();
    await expect(manager.getByRole('status')).toContainText('售价和未保存输入已保留');
    await expect(manager.getByLabel('售价 1000000000005:1', { exact: true })).toHaveValue('199.99');
    await expect(manager.getByLabel('商品说明 1000000000005', { exact: true })).toHaveValue('未保存说明');
    await expect(manager.getByLabel('售后条款 1000000000005', { exact: true })).toHaveValue('未保存售后');
    await expect(manager.getByLabel('商家 Telegram 用户名')).toHaveValue('unsaved_shop');
    await expect(manager.locator('.shop-admin-source-status p').first()).toContainText('商品信息已更新');
    await expect(manager.locator('.shop-admin-source-status p').last()).toContainText('来源暂不可用');
    if (screenshots) { await manager.setViewportSize({ width: 390, height: 900 }); await manager.screenshot({ path: path.join(screenshots, 'admin-partial-390.jpg'), type: 'jpeg', quality: 65, fullPage: false }); }
    await page.goto(base);
    await expect(page.getByRole('button', { name: 'Claude Pro 暂不可购买', exact: true })).toBeDisabled();
    await expect(page.getByRole('link', { name: '选择 ChatGPT Pro 200 规格', exact: true })).toBeVisible();
    const partial = await (await page.request.get(`${base}/api/v1/shop/catalog`)).json();
    assert.equal(partial.status, 'ready');
    assert.equal(partial.telegram, 'fixture_shop');
    assert.equal(partial.products.find(product => product.id === '1000000000005').variants[0].price_cents, 15500);
    evidence.assertions.push('actual partial source failure preserves every dirty merchant field; fresh GPT remains buyable and stale secondary disabled');
    await manager.route('**/api/v1/shop/refresh', route => route.fulfill({ status: 503, json: {} }));
    await manager.getByRole('button', { name: '刷新来源' }).click();
    await expect(manager.getByRole('alert')).toContainText('请求失败');
    await expect(manager.getByLabel('售价 1000000000005:1', { exact: true })).toHaveValue('199.99');
    await expect(manager.getByLabel('商家 Telegram 用户名')).toHaveValue('unsaved_shop');
    evidence.assertions.push('refresh HTTP failure preserves dirty retail/contact/copy');
    assert.deepEqual(evidence.externalRequests, []);
    assert.deepEqual(evidence.pageErrors, []);
    if (process.env.ANLI_BROWSER_EVIDENCE) fs.writeFileSync(process.env.ANLI_BROWSER_EVIDENCE, JSON.stringify(evidence, null, 2) + '\n');
    await context.close(); await admin.close();
    console.log(`PASS: Anli real-app fixture (${evidence.assertions.length} flow assertions, 0 external requests, 0 page errors)`);
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
