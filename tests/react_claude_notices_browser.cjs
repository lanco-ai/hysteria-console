const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

async function main() {
  const base = process.env.PREVIEW_BASE_URL;
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const evidence = { assertions: [], externalRequests: [], pageErrors: [] };
  try {
    const admin = await browser.newContext();
    await admin.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const initial = await (await admin.request.get(`${base}/api/v1/shop/admin`)).json();
    const ids = ['1000000000005', '1000000000007', '1000000000008'];
    const skus = Object.fromEntries(initial.items.map(item => [item.key, { price_cents: 12345, published: true }]));
    const originals = { '5': { description: '原 GPT **纯文本**', after_sales: '原 GPT 售后' }, '1000000000011': { description: '原 Grok **纯文本**', after_sales: '原 Grok 售后' } };
    const seeded = await admin.request.put(`${base}/api/v1/shop/admin`, { data: { revision: initial.revision, telegram: 'fixture_shop', skus, products: originals } });
    assert.equal(seeded.status(), 200);
    const manager = await admin.newPage();
    const context = await browser.newContext();
    const page = await context.newPage();
    for (const session of [context, admin]) session.on('request', request => { if (!request.url().startsWith(base) && !request.url().startsWith('data:')) evidence.externalRequests.push(request.url()); });
    for (const tab of [page, manager]) tab.on('pageerror', error => evidence.pageErrors.push(error.message));
    // A source refresh may introduce Claude after the initial merchant read.
    const beforeClaude = await (await admin.request.get(`${base}/api/v1/shop/admin`)).json();
    await manager.route('**/api/v1/shop/admin', route => route.fulfill({ json: { ...beforeClaude, items: beforeClaude.items.filter(item => !ids.includes(item.product_id)), products: originals } }));
    await manager.goto(`${base}/admin/shop`);
    await expect(manager.locator('.shop-admin-row')).toHaveCount(9);
    await manager.unroute('**/api/v1/shop/admin');
    await manager.getByRole('button', { name: '刷新来源', exact: true }).click();
    await expect(manager.locator('.shop-admin-row')).toHaveCount(12);
    for (const id of ids) {
      const section = manager.locator('.shop-admin-product').filter({ has: manager.getByLabel(`商品说明 ${id}`, { exact: true }) });
      await section.locator('summary').click();
      await expect(manager.getByLabel(`商品说明 ${id}`, { exact: true })).toHaveValue(initial.products[id].description);
      await expect(manager.getByLabel(`售后条款 ${id}`, { exact: true })).toHaveValue(initial.products[id].after_sales);
      await expect(manager.getByLabel(`商品说明 ${id}`, { exact: true })).toHaveAttribute('maxlength', '4000');
    }
    const defaults = await (await page.request.get(`${base}/api/v1/shop/catalog`)).json();
    for (const width of [390, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      for (const id of ids) {
        await page.goto(`${base}/?product=${id}`);
        await expect(page.locator('.shop-notice')).toHaveCount(2);
        await expect(page.getByRole('heading', { name: '⚠️ 充值前重要须知', exact: true })).toBeVisible();
        await expect(page.locator('.shop-notice')).toContainText(['Important Notice Before Purchase', '质保规则']);
        await expect(page.locator('.shop-notice img')).toHaveCount(3);
        for (const image of await page.locator('.shop-notice img').all()) {
          await image.scrollIntoViewIfNeeded();
          await expect(image).toHaveAttribute('loading', 'lazy');
          await expect(image).toHaveAttribute('alt', /示例/);
          await expect.poll(() => image.evaluate(node => node.complete && node.naturalWidth > 0)).toBe(true);
          const dimensions = await image.evaluate(node => ({ local: node.src.startsWith(location.origin), naturalWidth: node.naturalWidth, naturalHeight: node.naturalHeight, parentWidth: node.parentElement.getBoundingClientRect().width, rectWidth: node.getBoundingClientRect().width, rectHeight: node.getBoundingClientRect().height }));
          assert.equal(dimensions.local && Math.abs(dimensions.rectWidth / dimensions.rectHeight - dimensions.naturalWidth / dimensions.naturalHeight) < 0.001 && dimensions.rectWidth <= dimensions.parentWidth, true, JSON.stringify(dimensions));
        }
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        if (id === ids[0] && process.env.CLAUDE_PROJECT_SCREENSHOTS) await page.screenshot({ path: path.join(process.env.CLAUDE_PROJECT_SCREENSHOTS, `claude-${width}.jpg`), type: 'jpeg', quality: 65, fullPage: true });
      }
      evidence.assertions.push(`${width}px three exact Claude notices/headings/lists/local lazy images/natural ratios/no overflow`);
    }
    for (const [id, description] of [['5', originals['5'].description], ['1000000000011', originals['1000000000011'].description]]) {
      await page.goto(`${base}/?product=${id}`);
      await expect(page.getByText(description, { exact: true })).toBeVisible();
      await expect(page.locator('.shop-notice')).toHaveCount(0);
      await expect(page.locator('.shop-detail-copy strong')).toHaveCount(0);
    }
    evidence.assertions.push('GPT and Grok saved copy stay plain text');
    await manager.getByRole('button', { name: '保存商品设置', exact: true }).click();
    await expect(manager.getByRole('status')).toContainText('已保存');
    let merchant = await (await admin.request.get(`${base}/api/v1/shop/admin`)).json();
    for (const id of ids) assert.deepEqual(merchant.products[id], { description: defaults.products.find(p => p.id === id).description, after_sales: defaults.products.find(p => p.id === id).after_sales });
    await manager.getByLabel(`商品说明 ${ids[0]}`, { exact: true }).fill('## 本店自定义\n\n**自定义正文**');
    await manager.getByLabel(`售后条款 ${ids[0]}`, { exact: true }).fill('自定义售后');
    await manager.getByLabel(`商品说明 ${ids[1]}`, { exact: true }).fill('');
    await manager.getByLabel(`售后条款 ${ids[1]}`, { exact: true }).fill('');
    await manager.getByRole('button', { name: '保存商品设置', exact: true }).click();
    await expect(manager.getByRole('status')).toContainText('已保存');
    await page.goto(`${base}/?product=${ids[0]}`);
    await expect(page.getByRole('heading', { name: '本店自定义', exact: true })).toBeVisible();
    await expect(page.locator('.shop-notice strong')).toHaveText('自定义正文');
    await expect(page.getByText('自定义售后', { exact: true })).toBeVisible();
    await page.goto(`${base}/?product=${ids[1]}`);
    await expect(page.getByText('商家暂未填写商品说明，请联系商家了解详情。', { exact: true })).toBeVisible();
    await expect(page.getByText('商家暂未填写售后条款，请在购买前与商家确认。', { exact: true })).toBeVisible();
    await expect(page.locator('.shop-notice img')).toHaveCount(0);
    evidence.assertions.push('admin defaults savable; custom Markdown/edit and explicit clear win with fallback');
    await manager.getByLabel(`商品说明 ${ids[0]}`, { exact: true }).fill('未保存 Claude 说明');
    await manager.getByLabel(`售后条款 ${ids[0]}`, { exact: true }).fill('未保存 Claude 售后');
    await manager.getByRole('button', { name: '刷新来源', exact: true }).click();
    await expect(manager.getByRole('status')).toContainText('未保存输入已保留');
    await expect(manager.getByLabel(`商品说明 ${ids[0]}`, { exact: true })).toHaveValue('未保存 Claude 说明');
    await expect(manager.getByLabel(`售后条款 ${ids[0]}`, { exact: true })).toHaveValue('未保存 Claude 售后');
    merchant = await (await admin.request.get(`${base}/api/v1/shop/admin`)).json();
    assert.deepEqual(merchant.skus, skus);
    assert.equal(merchant.telegram, 'fixture_shop');
    assert.deepEqual(merchant.products['5'], originals['5']);
    assert.deepEqual(merchant.products['1000000000011'], originals['1000000000011']);
    evidence.assertions.push('source refresh preserves dirty Claude copy and persisted retail/contact/GPT/Grok');
    const malicious = '## 安全标题\n\n<script>window.shopNoticePwned=1</script>\n<img src="https://external.invalid/html.png" onerror="window.shopNoticePwned=2">\n\n![外部图](https://external.invalid/markdown.png)\n\n![未知图](/other.png)\n\n![允许图](/shop-notices/claude/active-subscription)\n\n[外部链接](https://external.invalid/link)\n\n**安全强调**';
    const changed = await admin.request.put(`${base}/api/v1/shop/admin`, { data: { revision: merchant.revision, telegram: merchant.telegram, skus: merchant.skus, products: { ...merchant.products, [ids[0]]: { description: malicious, after_sales: '' } } } });
    assert.equal(changed.status(), 200);
    await page.goto(`${base}/?product=${ids[0]}`);
    await expect(page.getByRole('heading', { name: '安全标题', exact: true })).toBeVisible();
    await expect(page.locator('.shop-notice img')).toHaveCount(1);
    await expect(page.locator('.shop-notice img')).toHaveAttribute('alt', '允许图');
    await expect(page.locator('.shop-notice a, .shop-notice script, .shop-notice iframe')).toHaveCount(0);
    await expect(page.locator('.shop-notice strong')).toHaveText('安全强调');
    assert.equal(await page.evaluate(() => window.shopNoticePwned), undefined);
    evidence.assertions.push('raw HTML and events skipped; external/unknown Markdown images and links cannot load; allowlisted image/emphasis survives');
    assert.deepEqual(evidence.externalRequests, []);
    assert.deepEqual(evidence.pageErrors, []);
    if (process.env.CLAUDE_PROJECT_EVIDENCE) fs.writeFileSync(`${process.env.CLAUDE_PROJECT_EVIDENCE}/shop-browser.json`, JSON.stringify(evidence, null, 2) + '\n');
    await context.close(); await admin.close();
    console.log(`PASS: Claude notices (${evidence.assertions.length} assertions, no external requests or page errors)`);
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
