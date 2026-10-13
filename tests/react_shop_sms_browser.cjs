const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const base = process.env.PREVIEW_BASE_URL;
    const context = await browser.newContext();
    const page = await context.newPage();
    const errors = [], requests = [], external = [];
    page.on('pageerror', error => errors.push(error.message));
    context.on('request', request => {
      if (!request.url().startsWith(base) && !request.url().startsWith('data:')) external.push(request.url());
      if (request.url().includes('/shop/sms/')) requests.push({ method: request.method(), url: request.url(), body: request.postData() });
    });
    await page.clock.install();
    const open = async code => {
      await page.goto(`${base}/?view=sms`);
      await page.getByLabel('激活码', { exact: true }).fill(code);
      await page.getByRole('button', { name: '验证激活码' }).click();
      await expect(page.getByText('激活码已验证', { exact: true })).toBeVisible();
      await page.getByRole('button', { name: '获取接收号码' }).click();
      await expect(page.getByText('+12025550123', { exact: true })).toBeVisible();
    };
    const screenshot = async name => {
      if (process.env.REACT_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, name), fullPage: true, animations: 'disabled' });
      }
    };
    for (const width of [1440, 1024, 390, 320]) {
      await page.setViewportSize({ width, height: 950 });
      await page.goto(`${base}/?view=sms`);
      await expect(page.getByRole('heading', { name: '一个号码，接收你的验证码。' })).toBeVisible();
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `overflow at ${width}`);
      await screenshot(`sms-entry-${width}.png`);
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await open('NORMAL-TEST-FAKE-CDK');
    await screenshot('sms-waiting-1440.png');
    const before = requests.filter(r => r.url.endsWith('/check')).length;
    await page.clock.runFor(9000);
    assert.equal(requests.filter(r => r.url.endsWith('/check')).length, before);
    await page.clock.runFor(1500);
    await expect(page.getByText('验证码已收到', { exact: true })).toBeVisible();
    await expect(page.getByText('246810', { exact: true })).toBeVisible();
    await screenshot('sms-received-1440.png');
    const terminalCount = requests.length;
    await page.clock.runFor(60_000);
    assert.equal(requests.length, terminalCount, 'terminal state keeps polling');
    const stored = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
    assert.ok(!stored.includes('NORMAL-TEST') && !page.url().includes('NORMAL-TEST'));
    assert.ok(requests.every(r => r.method === 'POST' && !r.url.includes('CDK')));

    // Cancellation stays bound to the current order and continues querying until confirmed.
    await open('CANCEL-TEST-FAKE-CDK');
    await page.getByRole('button', { name: '号码无法使用？申请取消' }).click();
    await page.getByRole('button', { name: '确认取消当前号码' }).click();
    await expect(page.getByText('正在确认取消', { exact: true })).toBeVisible();
    const acquisitions = requests.filter(r => r.url.endsWith('/acquire')).length;
    await page.clock.runFor(10_500);
    await expect(page.getByText('本次接码已取消', { exact: true })).toBeVisible();
    assert.equal(requests.filter(r => r.url.endsWith('/acquire')).length, acquisitions);
    await page.getByRole('button', { name: '返回激活码输入' }).click();
    await expect(page.getByLabel('激活码', { exact: true })).toHaveValue('');

    // Repeated clicks cannot acquire two numbers; provider 429 controls polling cadence.
    let releaseAcquire;
    const held = new Promise(resolve => { releaseAcquire = resolve; });
    await page.route('**/shop/sms/acquire', async route => { await held; await route.continue(); });
    await page.goto(`${base}/?view=sms`);
    await page.getByLabel('激活码', { exact: true }).fill('WAIT-TEST-FAKE-CDK');
    await page.getByRole('button', { name: '验证激活码' }).click();
    const acquire = page.getByRole('button', { name: '获取接收号码' });
    await expect(acquire).toBeVisible();
    const acquireBefore = requests.filter(r => r.url.endsWith('/acquire')).length;
    await acquire.evaluate(button => { button.click(); button.click(); });
    await expect(page.getByRole('button', { name: '正在获取号码…' })).toBeDisabled();
    releaseAcquire();
    await expect(page.getByText('+12025550123', { exact: true })).toBeVisible();
    assert.equal(requests.filter(r => r.url.endsWith('/acquire')).length, acquireBefore + 1);
    await page.unroute('**/shop/sms/acquire');
    let polls = 0;
    await page.route('**/shop/sms/check', async route => {
      polls += 1;
      await route.fulfill({ status: 429, headers: { 'Content-Type': 'application/json', 'Retry-After': '35' }, body: JSON.stringify({ ok: false, error: 'rate_limited' }) });
    });
    await page.clock.runFor(10_500);
    await expect(page.getByRole('alert')).toContainText('请求较频繁');
    await page.clock.runFor(30_000);
    assert.equal(polls, 1);
    await page.clock.runFor(6000);
    await expect.poll(() => polls).toBe(2);
    await page.unroute('**/shop/sms/check');
    await page.getByRole('link', { name: '← 返回商店' }).click();
    const away = requests.length;
    await page.clock.runFor(60_000);
    assert.equal(requests.length, away, 'page kept polling after navigation');

    // A transient query failure retains the number/order and retries, without another acquisition.
    await open('RETRY-TEST-FAKE-CDK');
    let retryPolls = 0;
    await page.route('**/shop/sms/check', async route => {
      retryPolls += 1;
      if (retryPolls === 1) await route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ ok: false, error: 'service_unavailable' }) });
      else await route.continue();
    });
    const beforeRetryAcquire = requests.filter(r => r.url.endsWith('/acquire')).length;
    await page.clock.runFor(10_500);
    await expect(page.getByRole('alert')).toContainText('暂时不可用');
    await expect(page.getByText('+12025550123', { exact: true })).toBeVisible();
    await page.clock.runFor(10_500);
    await expect(page.getByText('246810', { exact: true })).toBeVisible();
    assert.equal(requests.filter(r => r.url.endsWith('/acquire')).length, beforeRetryAcquire);
    await page.unroute('**/shop/sms/check');

    // An HTML proxy error never reaches product copy as a JSON/parser diagnostic.
    await page.goto(`${base}/?view=sms`);
    await page.route('**/shop/sms/verify', route => route.fulfill({ status: 502, contentType: 'text/html', body: '<html>temporary proxy failure</html>' }));
    await page.getByLabel('激活码', { exact: true }).fill('HTML-TEST-FAKE-CDK');
    await page.getByRole('button', { name: '验证激活码' }).click();
    await expect(page.getByRole('alert')).toHaveText('接码服务返回异常，请稍后重试。');
    await page.unroute('**/shop/sms/verify');

    // Supplier quote is private; new SKU is unpriced/unpublished until explicit merchant save.
    const admin = await browser.newContext();
    await admin.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const state = await (await admin.request.get(`${base}/api/v1/shop/admin`)).json();
    const id = '5000000000001', key = `${id}:1`;
    assert.equal(state.items.find(item => item.key === key).cost_cents, 100);
    assert.equal(state.skus[key], undefined);
    assert.ok(!(await (await context.request.get(`${base}/api/v1/shop/catalog`)).json()).products.some(p => p.id === id));
    const adminPage = await admin.newPage();
    await adminPage.goto(`${base}/admin/shop`);
    await adminPage.getByRole('button', { name: /^短信接码 / }).click();
    await expect(adminPage.getByRole('heading', { name: '短信接码激活码' }).first()).toBeVisible();
    const saved = await admin.request.put(`${base}/api/v1/shop/admin`, { data: { client: 3, revision: state.revision, telegram: 'test_shop', skus: { [key]: { price_cents: 250, published: true } }, products: state.products } });
    assert.equal(saved.status(), 200);
    await page.goto(`${base}/?product=${id}`);
    await expect(page.getByRole('link', { name: '进入短信接码' })).toHaveAttribute('href', '/?view=sms');
    await expect(page.getByRole('img', { name: '短信接码', exact: true })).toBeVisible();
    const catalog = await (await context.request.get(`${base}/api/v1/shop/catalog`)).text();
    assert.ok(!catalog.includes('zephyr') && !catalog.includes('cost_cents'));
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log('PASS: SMS draft/private cost, merchant category, redemption, 10s polling, terminal stop, cancel pending, double-click, 429, navigation cleanup, secret locality and 4 viewport layouts');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
