const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const base = process.env.PREVIEW_BASE_URL;
  const id = '2000000000002';
  const key = `${id}:1`;
  const errors = [], external = [];
  const allowedLinks = [
    'https://dev-api.cc/muse.php', 'https://www.tmail.pk/login', 'https://muse.ai/',
    'https://www.youtube.com/watch?v=lpsRG9VSDuQ',
  ];
  try {
    const admin = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const customer = await browser.newContext();
    for (const context of [admin, customer]) {
      await context.route('**/*', route => {
        if (new URL(route.request().url()).origin !== base) {
          external.push(route.request().url());
          return route.abort();
        }
        return route.continue();
      });
      context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    }
    await admin.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const readAdmin = async () => (await admin.request.get(`${base}/api/v1/shop/admin`)).json();
    const catalog = async () => (await customer.request.get(`${base}/api/v1/shop/catalog`)).json();
    const save = async changes => {
      const current = await readAdmin();
      const response = await admin.request.put(`${base}/api/v1/shop/admin`, { data: {
        client: 3, revision: current.revision, telegram: current.telegram, wechat: current.wechat,
        skus: current.skus, products: current.products, display: current.display,
        ...changes,
      }, headers: { 'Sec-Fetch-Site': 'same-origin' } });
      assert.equal(response.status(), 200);
    };
    const screenshot = async (page, name) => {
      if (!process.env.REACT_SCREENSHOT_DIR) return;
      fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, name), fullPage: true, animations: 'disabled' });
    };
    const initial = await readAdmin();
    const muse = initial.items.find(item => item.product_id === id);
    assert.equal(muse.category, 'Muse AI');
    assert.equal(muse.provider, 'prodseller');
    assert.equal(muse.cost_cents, 792);
    assert.equal(initial.skus[key], undefined);
    assert.equal(initial.products[id].after_sales, '');
    assert.equal((await catalog()).products.some(item => item.id === id), false);

    const page = await admin.newPage();
    await page.goto(`${base}/admin/shop`);
    await page.getByRole('group', { name: '品牌', exact: true }).getByRole('button', { name: /^Muse AI / }).click();
    const product = page.locator(`section[aria-labelledby="shop-admin-product-${id}"]`);
    await expect(product).toBeVisible();
    await expect(product).toContainText('ProdSeller');
    await expect(product.locator('.shop-admin-cost')).toContainText('¥7.92');
    await expect(page.getByLabel(`售价 ${key}`, { exact: true })).toHaveValue('');
    await expect(page.getByLabel(`上架 ${key}`, { exact: true })).not.toBeChecked();
    await page.getByRole('button', { name: `商品设置 ${id}`, exact: true }).click();
    await expect(product).toContainText('商品说明支持 Markdown');
    await expect(page.getByLabel(`售后条款 ${id}`, { exact: true })).toHaveValue('');
    await expect(page.getByLabel(`商品说明 ${id}`, { exact: true })).toHaveValue(initial.products[id].description);
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await screenshot(page, `muse-admin-draft-${width}.png`);
    }

    // This publication happens only in the temporary fixture store.
    await page.getByLabel(`售价 ${key}`, { exact: true }).fill('19.90');
    await page.getByLabel(`上架 ${key}`, { exact: true }).check();
    await page.getByRole('button', { name: '保存商品设置', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('已保存');
    const published = await catalog();
    const publicMuse = published.products.find(item => item.id === id);
    assert.equal(publicMuse.variants[0].price_cents, 1990);
    assert.equal(publicMuse.variants[0].available, true);
    const wire = JSON.stringify(publicMuse).toLowerCase();
    for (const privateValue of ['prodseller', 'cost_cents', 'source_url', 'buy_url', '$1.10', '$1.00', '$1.20', '6ac65a53d07e0053469f477d', 'supplier marketing']) {
      assert.equal(wire.includes(privateValue), false);
    }
    const detail = await customer.newPage();
    for (const width of [390, 1024, 1440]) {
      await detail.setViewportSize({ width, height: 1000 });
      await detail.goto(`${base}/?product=${id}`);
      const notice = detail.locator('.shop-notice');
      await expect(notice.getByRole('heading', { name: 'Muse AI', exact: true })).toBeVisible();
      await expect(notice.locator('ol li')).toHaveCount(4);
      await expect(notice).toContainText('1 Billion');
      await expect(detail.getByRole('img', { name: 'Muse AI', exact: true }).first()).toBeVisible();
      assert.deepEqual((await notice.locator('a').evaluateAll(links => links.map(link => link.href))).sort(), [...allowedLinks].sort());
      for (const href of allowedLinks) {
        const link = notice.locator(`a[href="${href}"]`);
        await expect(link).toHaveAttribute('target', '_blank');
        await expect(link).toHaveAttribute('rel', 'noopener noreferrer');
      }
      assert.ok(await detail.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await screenshot(detail, `muse-detail-${width}.png`);
    }

    const unsafeLinks = [
      'http://dev-api.cc/muse.php', 'https://dev-api.cc/other.php',
      'https://dev-api.cc/muse.php?x=1', 'https://dev-api.cc/muse.php#fragment',
      'https://dev-api.cc.evil.invalid/muse.php', 'https://user@dev-api.cc/muse.php',
      'https://www.tmail.pk/other', 'https://www.tmail.pk/login?next=evil',
      'https://www.youtube.com/watch?v=other', 'https://www.youtube.com/watch?v=lpsRG9VSDuQ&x=1',
      'https://muse.ai/other', 'https://muse.ai.evil.invalid/',
      'javascript:window.noticePwned=1',
    ];
    const malicious = initial.products[id].description + '\n\n' + unsafeLinks.map((href, i) => `[不允许链接${i}](${href})`).join('\n\n') + '\n\n<script>window.noticePwned=1</script>\n\n![远程图片](https://example.invalid/image.png)';
    await save({ products: { ...initial.products, [id]: { description: malicious, after_sales: '' } } });
    await detail.reload();
    await expect(detail.locator('.shop-notice a')).toHaveCount(4);
    await expect(detail.locator('.shop-notice script, .shop-notice img')).toHaveCount(0);
    assert.equal(await detail.evaluate(() => window.noticePwned), undefined);

    // Merchant edits and explicit clears survive a real API save and reload.
    for (const description of ['# 本店 Muse 操作说明\n\n请使用交付的邮箱登录。', '']) {
      await save({ products: { ...initial.products, [id]: { description, after_sales: '' } } });
      assert.equal((await readAdmin()).products[id].description, description);
      assert.equal((await catalog()).products.find(item => item.id === id).description, description);
      await detail.reload();
      if (description) await expect(detail.getByRole('heading', { name: '本店 Muse 操作说明', exact: true })).toBeVisible();
      else await expect(detail.locator('.shop-detail-copy')).toContainText('未填写');
    }
    await save({ source_enabled: { prodseller: false } });
    assert.equal((await catalog()).products.some(item => item.id === id), false);
    const disabled = await readAdmin();
    assert.equal(disabled.sources.prodseller.enabled, false);
    assert.equal(disabled.skus[key].price_cents, 1990);
    assert.equal(disabled.skus[key].published, true);
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log('PASS: Muse draft, private API cost, brand, synthetic publication, Markdown and exact link safety, editable copy, source disable, 390/1024/1440 layout');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
