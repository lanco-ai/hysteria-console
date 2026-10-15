const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

(async () => {
  const base = process.env.PREVIEW_BASE_URL;
  const browser = await chromium.launch({ headless: true });
  const errors = []; let external = 0; let mutations = 0;
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== base) { external++; return route.abort(); }
      if (route.request().url().includes('/admin/sources/')) mutations++;
      return route.continue();
    });
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const admin = async () => (await context.request.get(`${base}/api/v1/shop/admin`)).json();
    const catalog = async () => (await context.request.get(`${base}/api/v1/shop/catalog`)).json();
    const page = await context.newPage();
    const oldPage = await context.newPage();
    await page.goto(`${base}/admin/shop`); await oldPage.goto(`${base}/admin/shop`);
    const baseline = await admin();
    const seven = baseline.items.filter(row => row.provider === 'shop16688');
    assert.equal(seven.length, 7);
    assert(seven.every(row => baseline.skus[row.key]?.price_cents == null && !baseline.skus[row.key]?.published));
    await page.getByRole('button', { name: '来源设置', exact: true }).click();
    const panel = page.getByRole('group', { name: '来源读取设置', exact: true });
    const remove = name => panel.getByRole('button', { name: `删除来源 ${name}`, exact: true });
    const add = name => panel.getByRole('button', { name: `重新添加 ${name}`, exact: true });
    const save = page.locator('.shop-admin-savebar > button');
    const reload = page.getByRole('button', { name: '重新读取', exact: true });
    const refresh = page.getByRole('button', { name: '刷新来源', exact: true });
    assert.equal(await remove('本店').count(), 0);

    // Cancel is a zero-request operation, and confirmation explains the irreversible scope.
    let confirmation = '';
    page.once('dialog', dialog => { confirmation = dialog.message(); return dialog.dismiss(); });
    await remove('16688').click();
    assert.equal(mutations, 0);
    for (const word of ['16688', '永久', '售价', '优惠', '文案', '标签', '补录销量', '上架', '重新定价', '浏览', '实际订单']) assert(confirmation.includes(word));
    assert.deepEqual((await admin()).skus, baseline.skus);

    await page.getByLabel('商家 Telegram 用户名', { exact: true }).fill('draft_contact');
    await expect(remove('16688')).toBeDisabled();
    await expect(panel).toContainText('先保存或重新读取');
    assert.equal(mutations, 0);
    page.once('dialog', dialog => dialog.accept()); await reload.click();
    await expect(remove('16688')).toBeEnabled();
    await expect(page.getByLabel('商家 Telegram 用户名', { exact: true })).toHaveValue('preview_shop');

    // Delete a published feed, so every old draft/copy/display must disappear too.
    let release;
    const hold = new Promise(resolve => { release = resolve; });
    await page.route('**/api/v1/shop/admin/sources/gpt', async route => {
      await hold; await route.continue();
    });
    page.once('dialog', dialog => dialog.accept()); await remove('强云').click();
    await expect(save).toBeDisabled(); await expect(refresh).toBeDisabled(); await expect(reload).toBeDisabled();
    await expect(remove('16688')).toBeDisabled();
    release(); await expect(page.getByRole('status')).toContainText('已永久删除');
    await page.unroute('**/api/v1/shop/admin/sources/gpt');
    await expect(page.locator('.shop-source-chip').filter({ hasText: '强云' })).toHaveCount(0);
    await expect(page.locator('section[aria-labelledby="shop-admin-product-2"]')).toHaveCount(0);
    await expect(add('强云')).toBeVisible();
    const removed = await admin();
    assert(!removed.skus['2:7'] && !removed.products['2'] && !removed.display['2']);
    assert.deepEqual(removed.skus['2000000000002:1'], baseline.skus['2000000000002:1']);
    assert.deepEqual(removed.products['2000000000002'], baseline.products['2000000000002']);
    assert.deepEqual(removed.local_names, baseline.local_names);
    assert.equal((await catalog()).products.some(row => row.id === '2'), false);

    // A stale page cannot acquire the new revision merely by refreshing supplier data.
    await oldPage.getByLabel('商家 Telegram 用户名', { exact: true }).fill('old_draft');
    await oldPage.getByRole('button', { name: '刷新来源', exact: true }).click();
    await expect(oldPage.getByRole('status')).toContainText('未保存输入已保留');
    await oldPage.getByRole('button', { name: '保存商品设置', exact: true }).click();
    await expect(oldPage.getByRole('alert')).toContainText('已被其他页面修改');
    await oldPage.close({ runBeforeUnload: false });

    await page.getByLabel('商家 Telegram 用户名', { exact: true }).fill('unsaved_contact');
    await expect(add('强云')).toBeDisabled();
    page.once('dialog', dialog => dialog.accept()); await reload.click();
    await add('强云').click();
    await expect(page.getByRole('status')).toContainText('重新定价');
    await expect(add('强云')).toHaveCount(0);
    assert.equal((await admin()).items.filter(row => row.provider === 'gpt').length, 0);
    await refresh.click(); await expect(page.getByRole('status')).toContainText('检查完成');
    const fresh = await admin();
    assert(fresh.items.filter(row => row.provider === 'gpt').every(row => !fresh.skus[row.key]));
    await page.getByRole('group', { name: '品牌', exact: true }).getByRole('button', { name: /^GPT / }).click();
    await expect(page.getByLabel('售价 2:7', { exact: true })).toHaveValue('');
    await expect(page.getByRole('checkbox', { name: '上架 2:7', exact: true })).not.toBeChecked();
    await save.click(); await expect(page.getByRole('status')).toContainText('已保存');
    assert.equal((await catalog()).products.some(row => row.id === '2'), false);

    page.once('dialog', dialog => dialog.accept()); await remove('16688').click();
    await expect(add('16688')).toBeVisible();
    assert.equal((await admin()).items.filter(row => row.provider === 'shop16688').length, 0);
    const retained = (await catalog()).products.find(row => row.id === '2000000000002');
    assert.equal(retained.variants[0].price_cents, 1888);
    assert.equal(retained.after_sales, '刚保存的 Muse 售后');
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      await expect(panel).toBeVisible(); await expect(add('16688')).toBeVisible();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      if (process.env.REACT_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, `source-lifecycle-${width}.png`), fullPage: true, animations: 'disabled' });
      }
    }
    await add('16688').click(); await expect(page.getByRole('status')).toContainText('重新定价');
    await refresh.click(); await expect(page.getByRole('status')).toContainText('检查完成');
    const readded = await admin();
    assert.equal(readded.items.filter(row => row.provider === 'shop16688').length, 7);
    assert(seven.every(row => !readded.skus[row.key]));
    assert.equal(readded.source_lifecycle.shop16688.generation, 2);
    assert.equal((await catalog()).products.length, 2);
    assert.deepEqual(errors, []); assert.equal(external, 0);
    console.log('PASS: irreversible-source confirmation/cancel, dirty and busy guards, stale-page 409, clean re-add, existing Muse/local preservation, 1440/390 layout');
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
