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
    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'clipboard', { value: { writeText: async value => { window.__smsClipboard = value; } } });
    });
    const page = await context.newPage();
    const errors = [], requests = [], external = [];
    page.on('pageerror', error => errors.push(error.message));
    context.on('request', request => {
      if (!request.url().startsWith(base) && !request.url().startsWith('data:')) external.push(request.url());
      if (request.url().includes('/shop/sms/')) requests.push({ method: request.method(), url: request.url(), body: request.postData() });
    });
    await page.clock.install();
    const open = async (code, displayedPhone = '+1 (202) 555-0123') => {
      await page.goto(`${base}/?view=sms`);
      await page.getByLabel('激活码', { exact: true }).fill(code);
      await page.getByRole('button', { name: '验证激活码' }).click();
      await expect(page.getByText('激活码已验证', { exact: true })).toBeVisible();
      await expect(page.getByLabel('激活码', { exact: true })).toBeDisabled();
      const beforeReveal = requests.length;
      await page.getByRole('button', { name: '显示激活码', exact: true }).click();
      await expect(page.getByLabel('激活码', { exact: true })).toHaveAttribute('type', 'text');
      await expect(page.getByLabel('激活码', { exact: true })).toHaveValue(code);
      assert.equal(requests.length, beforeReveal, 'locked visibility toggle made a request');
      await page.getByRole('button', { name: '获取接收号码' }).click();
      await expect(page.getByText(displayedPhone, { exact: true })).toBeVisible();
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
      await expect(page.getByRole('switch', { name: '指定手机号' })).not.toBeChecked();
      const codeInput = page.getByLabel('激活码', { exact: true });
      await codeInput.fill('VISIBILITY-TEST-FAKE-CDK');
      await expect(codeInput).toHaveAttribute('type', 'password');
      const visibilityRequests = requests.length;
      const showCode = page.getByRole('button', { name: '显示激活码', exact: true });
      await expect(showCode).toHaveAttribute('type', 'button');
      await expect(showCode).toHaveAttribute('aria-controls', 'sms-code');
      await expect(showCode).toHaveAttribute('aria-pressed', 'false');
      await showCode.focus();
      await showCode.press('Space');
      await expect(codeInput).toHaveAttribute('type', 'text');
      await expect(codeInput).toHaveValue('VISIBILITY-TEST-FAKE-CDK');
      const hideCode = page.getByRole('button', { name: '隐藏激活码', exact: true });
      await expect(hideCode).toHaveAttribute('aria-pressed', 'true');
      await hideCode.press('Enter');
      await expect(codeInput).toHaveAttribute('type', 'password');
      assert.equal(requests.length, visibilityRequests, 'visibility toggle submitted or made a request');
      await showCode.click();
      await codeInput.fill('VISIBILITY-NEW-FAKE-CDK');
      await expect(codeInput).toHaveAttribute('type', 'text');
      await page.getByRole('button', { name: '隐藏激活码', exact: true }).click();
      await expect(codeInput).toHaveAttribute('type', 'password');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `overflow at ${width}`);
      await screenshot(`sms-entry-${width}.png`);
      await page.getByRole('switch', { name: '指定手机号' }).check();
      await page.getByLabel('美国手机号', { exact: true }).fill('+1 (202) 555-0123');
      await page.getByLabel('美国手机号', { exact: true }).press('Tab');
      await expect(page.getByLabel('美国手机号', { exact: true })).toHaveValue('2025550123');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `specific-phone overflow at ${width}`);
      await screenshot(`sms-specific-${width}.png`);
      await open(`DISPLAY-${width}-FAKE-CDK`);
      await expect(page.getByText('美国 · USA', { exact: true })).toBeVisible();
      const number = page.locator('.sms-number strong');
      const box = await number.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= width, `phone bounds at ${width}`);
      assert.ok(await number.evaluate(element => element.scrollWidth <= element.clientWidth), `phone text clipped at ${width}`);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `number overflow at ${width}`);
      await page.getByRole('button', { name: '复制号码', exact: true }).click();
      assert.equal(await page.evaluate(() => window.__smsClipboard), '+12025550123');
      await screenshot(`sms-number-${width}.png`);
      await page.clock.runFor(10_500);
      await expect(page.getByText('246810', { exact: true })).toBeVisible();
      await screenshot(`sms-received-${width}.png`);
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

    // Reusing a phone starts a new verified order, clears the previous SMS, and never auto-acquires.
    await page.getByRole('button', { name: '再次使用此号码' }).click();
    await expect(page.getByRole('switch', { name: '指定手机号' })).toBeChecked();
    await expect(page.getByLabel('美国手机号', { exact: true })).toHaveValue('2025550123');
    await expect(page.getByLabel('激活码', { exact: true })).toHaveValue('NORMAL-TEST-FAKE-CDK');
    await expect(page.getByLabel('激活码', { exact: true })).toHaveAttribute('type', 'password');
    await expect(page.getByText('246810', { exact: true })).toHaveCount(0);
    assert.equal(requests.length, terminalCount, 'reuse button must not allocate a number');
    await page.getByLabel('激活码', { exact: true }).fill('REUSE-TEST-FAKE-CDK');
    await page.getByLabel('美国手机号', { exact: true }).fill('');
    await page.getByLabel('美国手机号', { exact: true }).pressSequentially('112025550123');
    await expect(page.getByLabel('美国手机号', { exact: true })).toHaveValue('112025550123');
    await expect(page.getByRole('button', { name: '验证激活码' })).toBeDisabled();
    const paddedInvalidPhone = `2025550123${' '.repeat(24)}4`;
    await page.getByLabel('美国手机号', { exact: true }).fill(paddedInvalidPhone);
    await page.getByLabel('美国手机号', { exact: true }).press('Tab');
    await expect(page.getByLabel('美国手机号', { exact: true })).toHaveValue(paddedInvalidPhone);
    await expect(page.getByRole('button', { name: '验证激活码' })).toBeDisabled();
    await page.getByLabel('美国手机号', { exact: true }).fill('20255501234');
    await expect(page.getByRole('button', { name: '验证激活码' })).toBeDisabled();
    await page.getByLabel('美国手机号', { exact: true }).fill('+1 (202) 555-0123');
    const reuseStart = requests.length;
    for (const sms of ['112233', '223344', '334455']) {
      await page.getByRole('button', { name: '验证激活码' }).click();
      await expect(page.getByLabel('美国手机号', { exact: true })).toBeDisabled();
      await page.getByRole('button', { name: '获取接收号码' }).click();
      await expect(page.getByText('+1 (202) 555-0123', { exact: true })).toBeVisible();
      await page.clock.runFor(10_500);
      await expect(page.getByText(sms, { exact: true })).toBeVisible();
      await page.getByRole('button', { name: '再次使用此号码' }).click();
      await expect(page.getByText(sms, { exact: true })).toHaveCount(0);
    }
    const reuseRequests = requests.slice(reuseStart);
    assert.deepEqual(reuseRequests.filter(r => r.url.endsWith('/acquire')).map(r => JSON.parse(r.body)),
      Array.from({ length: 3 }, () => ({ activation_code: 'REUSE-TEST-FAKE-CDK', specific_phone: '2025550123' })));
    assert.deepEqual(reuseRequests.filter(r => r.url.endsWith('/check')).map(r => JSON.parse(r.body).order_id), [124, 125, 126]);
    await page.getByRole('button', { name: '验证激活码' }).click();
    await expect(page.getByText('剩余 0 次', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '获取接收号码' })).toBeDisabled();
    await page.getByRole('button', { name: '使用其他激活码' }).click();
    await expect(page.getByLabel('激活码', { exact: true })).toHaveValue('');
    await expect(page.getByLabel('激活码', { exact: true })).toHaveAttribute('type', 'password');
    await expect(page.getByLabel('美国手机号', { exact: true })).toHaveValue('2025550123');

    // An unavailable specified phone stays selected and never falls back to random acquisition.
    await page.getByLabel('激活码', { exact: true }).fill('UNAVAILABLE-TEST-CDK');
    await page.getByLabel('美国手机号', { exact: true }).fill('2025550199');
    await page.getByRole('button', { name: '验证激活码' }).click();
    const unavailableStart = requests.length;
    await page.getByRole('button', { name: '获取接收号码' }).click();
    await expect(page.getByRole('alert')).toContainText('指定号码暂不可用');
    await expect(page.getByRole('switch', { name: '指定手机号' })).toBeChecked();
    await expect(page.getByLabel('美国手机号', { exact: true })).toHaveValue('2025550199');
    await page.clock.runFor(60_000);
    assert.equal(requests.length, unavailableStart + 1);

    // An existing pending order is recovered without trying to replace its phone.
    await page.getByRole('button', { name: '使用其他激活码' }).click();
    await page.getByLabel('激活码', { exact: true }).fill('PENDING-TEST-FAKE-CDK');
    await page.getByRole('button', { name: '验证激活码' }).click();
    await expect(page.getByText('此激活码有未结束的接码，将恢复原订单，不重新申请指定号码。')).toBeVisible();
    await page.getByRole('button', { name: '恢复当前号码' }).click();
    await expect(page.getByText('+1 (202) 555-0123', { exact: true })).toBeVisible();
    assert.deepEqual(JSON.parse(requests.filter(r => r.url.endsWith('/acquire')).at(-1).body), { activation_code: 'PENDING-TEST-FAKE-CDK' });

    // Another device can acquire between verify and acquire. Keep the returned order visible.
    await page.goto(`${base}/?view=sms`);
    await page.getByLabel('激活码', { exact: true }).fill('RACE-TEST-FAKE-CDK');
    await page.getByRole('switch', { name: '指定手机号' }).check();
    await page.getByLabel('美国手机号', { exact: true }).fill('2025550198');
    await page.getByRole('button', { name: '验证激活码' }).click();
    await page.getByRole('button', { name: '获取接收号码' }).click();
    await expect(page.getByText('+1 (202) 555-0123', { exact: true })).toBeVisible();
    await expect(page.getByText('返回的号码与指定号码不同，已保留本次订单。请先核对处理，不要重复取号。')).toBeVisible();
    const mismatchAcquires = requests.filter(r => r.url.endsWith('/acquire')).length;
    await page.clock.runFor(10_500);
    await expect(page.getByText('246810', { exact: true })).toBeVisible();
    await expect(page.getByText('返回的号码与指定号码不同，已保留本次订单。请先核对处理，不要重复取号。')).toBeVisible();
    assert.equal(requests.filter(r => r.url.endsWith('/acquire')).length, mismatchAcquires);

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
    await expect(page.getByText('+1 (202) 555-0123', { exact: true })).toBeVisible();
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
    await expect(page.getByText('+1 (202) 555-0123', { exact: true })).toBeVisible();
    await page.clock.runFor(10_500);
    await expect(page.getByText('246810', { exact: true })).toBeVisible();
    assert.equal(requests.filter(r => r.url.endsWith('/acquire')).length, beforeRetryAcquire);
    await page.unroute('**/shop/sms/check');

    // Country labels do not confuse Canada/foreign numbers with US reuse.
    for (const [code, phone, country] of [
      ['FOREIGN-TEST-FAKE-CDK', '+447700900123', '英国 · UK'],
      ['CANADA-TEST-FAKE-CDK', '+1 (416) 555-0123', '加拿大 · Canada'],
      ['UNKNOWN-TEST-FAKE-CDK', '2025550123', '国家/地区未确认'],
      ['LEGACY-TEST-FAKE-CDK', '+1 (202) 555-0123', '国家/地区未确认'],
    ]) {
      await open(code, phone);
      await expect(page.getByText(country, { exact: true })).toBeVisible();
      await expect(page.getByText('美国 · USA', { exact: true })).toHaveCount(0);
      await page.clock.runFor(10_500);
      await expect(page.getByText('246810', { exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: '再次使用此号码' })).toHaveCount(0);
    }

    // Unknown country restricts reuse without claiming an identical requested number differs.
    await page.goto(`${base}/?view=sms`);
    await page.getByLabel('激活码', { exact: true }).fill('LEGACY-MATCH-TEST-FAKE-CDK');
    await page.getByRole('switch', { name: '指定手机号' }).check();
    await page.getByLabel('美国手机号', { exact: true }).fill('2025550123');
    await page.getByRole('button', { name: '验证激活码' }).click();
    await page.getByRole('button', { name: '获取接收号码' }).click();
    await expect(page.getByText('+1 (202) 555-0123', { exact: true })).toBeVisible();
    await expect(page.getByText('国家/地区未确认', { exact: true })).toBeVisible();
    await expect(page.getByText('返回的号码与指定号码不同，已保留本次订单。请先核对处理，不要重复取号。')).toHaveCount(0);
    const sameUnknownAcquires = requests.filter(r => r.url.endsWith('/acquire')).length;
    await page.clock.runFor(10_500);
    await expect(page.getByText('246810', { exact: true })).toBeVisible();
    await expect(page.getByText('返回的号码与指定号码不同，已保留本次订单。请先核对处理，不要重复取号。')).toHaveCount(0);
    await expect(page.getByRole('button', { name: '再次使用此号码' })).toHaveCount(0);
    assert.equal(requests.filter(r => r.url.endsWith('/acquire')).length, sameUnknownAcquires);

    // A previous gateway omitted country entirely; retain its explicit +1 reuse contract.
    await page.route('**/shop/sms/acquire', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, phone: '+12025550123', order_id: 123, status: 'PENDING', sms_code: null, wait_seconds: 0 }) }));
    await open('OLD-API-TEST-FAKE-CDK');
    await expect(page.getByText('国家/地区未确认', { exact: true })).toBeVisible();
    await page.clock.runFor(10_500);
    await expect(page.getByText('246810', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '再次使用此号码' }).click();
    await expect(page.getByLabel('美国手机号', { exact: true })).toHaveValue('2025550123');
    await expect(page.getByLabel('激活码', { exact: true })).toHaveAttribute('type', 'password');
    await page.unroute('**/shop/sms/acquire');

    // Verification locks the credential field while display controls remain available.
    await page.goto(`${base}/?view=sms`);
    await page.getByLabel('激活码', { exact: true }).fill('LOCKED-TEST-FAKE-CDK');
    await page.getByRole('button', { name: '验证激活码' }).click();
    await expect(page.getByLabel('激活码', { exact: true })).toBeDisabled();
    const lockedRequests = requests.length;
    await page.getByRole('button', { name: '显示激活码', exact: true }).click();
    await expect(page.getByLabel('激活码', { exact: true })).toHaveAttribute('type', 'text');
    await expect(page.getByLabel('激活码', { exact: true })).toHaveValue('LOCKED-TEST-FAKE-CDK');
    assert.equal(requests.length, lockedRequests);
    await page.getByRole('button', { name: '使用其他激活码' }).click();
    await expect(page.getByLabel('激活码', { exact: true })).toHaveValue('');
    await expect(page.getByLabel('激活码', { exact: true })).toHaveAttribute('type', 'password');

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
    console.log('PASS: SMS country/full number/canonical copy, accessible credential visibility and reset, Canada/foreign/unknown labels, draft/private cost, specified phone, three separate reuse orders, remaining credit, unavailable/no fallback, pending recovery, mismatch race, 10s polling, cancel pending, double-click, 429, navigation cleanup, secret locality and 4 viewport layouts');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
