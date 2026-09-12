const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const baseUrl = process.env.PREVIEW_BASE_URL || 'http://127.0.0.1:18765';
const fixturePassword = process.env.REACT_PREVIEW_LOGIN_PASSWORD;
const screenshotDir = process.env.REACT_LOGIN_SCREENSHOT_DIR;
const transportError = '登录结果未确认，请检查网络后重试。';

function routeOf(url) {
  const parsed = new URL(url);
  return `${parsed.pathname}${parsed.search}`;
}

function collectFailures(page, scenario, { allowedResponses = [], allowedFailures = [] } = {}) {
  const failures = [];
  const apiRequests = [];
  page.on('pageerror', error => failures.push(`${scenario} pageerror: ${error.message}`));
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/')) apiRequests.push(`${request.method()} ${routeOf(request.url())}`);
  });
  page.on('requestfailed', request => {
    const pair = `${request.method()} ${routeOf(request.url())}`;
    if (!allowedFailures.includes(pair)) failures.push(`${scenario} requestfailed: ${pair} ${request.failure()?.errorText || 'unknown network failure'}`);
  });
  page.on('response', response => {
    if (response.status() < 400) return;
    const pair = `${response.request().method()} ${routeOf(response.url())} ${response.status()}`;
    if (!allowedResponses.includes(pair)) failures.push(`${scenario} response: ${pair}`);
  });
  return { failures, apiRequests };
}

function assertClean(collection) {
  assert.deepEqual(collection.failures, [], 'page must have no unexpected errors or failed requests');
}

async function gotoLogin(page, suffix = '') {
  const response = await page.goto(`${baseUrl}/__react/login${suffix}`);
  assert.equal(response.status(), 200, 'controlled React login entry must be available');
  await page.locator('#form-admin').waitFor();
}

async function fulfillJson(route, payload, status = 200, headers = {}) {
  await route.fulfill({ status, contentType: 'application/json', headers, body: JSON.stringify(payload) });
}

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

async function verifyDocumentValidationAndKeyboard(browser) {
  const context = await browser.newContext({ viewport: { width: 1024, height: 900 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const collection = collectFailures(page, 'document and keyboard');
  await gotoLogin(page, '?msg=ignored&username=ignored#main-content');
  assert.equal(await page.title(), '管理员登录 · Hysteria');
  assert.equal(await page.locator('body').getAttribute('class'), 'page-auth page-admin-login');
  assert.equal(await page.locator('#main-content').evaluate(element => element === document.activeElement), true);
  assert.equal(await page.locator('body.has-shell, .app, .sidebar').count(), 0);
  assert.equal(await page.locator('#admin-username').inputValue(), '');
  assert.equal(await page.locator('.login-feedback').innerText(), '');
  assert.equal(collection.apiRequests.length, 0, 'login load must not read a session or any API');
  assert.equal(await page.locator('script[src*="login.js"], script[src*="ui-core.js"], script[src*="shell.js"]').count(), 0);
  await page.locator('.auth-submit').click();
  assert.equal(collection.apiRequests.length, 0, 'native required validation must prevent submission');
  assert.equal(await page.locator('#admin-username').evaluate(input => input.validity.valueMissing), true);
  const maxLength = Number(await page.locator('#admin-password').getAttribute('maxlength'));
  assert(maxLength > 0);
  await page.locator('#admin-password').fill('x'.repeat(maxLength + 4));
  assert.equal((await page.locator('#admin-password').inputValue()).length, maxLength);

  const toggle = page.locator('#login-password-toggle');
  assert.equal(await toggle.getAttribute('aria-label'), '显示密码');
  assert.equal(await toggle.getAttribute('aria-pressed'), 'false');
  assert.equal(await page.locator('#admin-password').getAttribute('type'), 'password');
  await toggle.click();
  assert.equal(await toggle.innerText(), '隐藏');
  assert.equal(await toggle.getAttribute('aria-label'), '隐藏密码');
  assert.equal(await toggle.getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('#admin-password').getAttribute('type'), 'text');
  await toggle.focus();
  await page.keyboard.press('Space');
  assert.equal(await page.locator('#admin-password').getAttribute('type'), 'password');
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#admin-password').getAttribute('type'), 'text');

  let requestCount = 0;
  await page.route('**/api/v1/login', async route => {
    requestCount += 1;
    const request = route.request();
    assert.equal(request.method(), 'POST');
    assert.equal(request.headers().accept, 'application/json');
    assert.match(request.headers()['content-type'], /^application\/x-www-form-urlencoded/);
    assert.equal(request.postData(), 'admin_username=admin&admin_password=wrong');
    await fulfillJson(route, { ok: false, message: '用户名或密码错误' });
  }, { times: 1 });
  await page.locator('#admin-username').fill('admin');
  await page.locator('#admin-password').fill('wrong');
  await page.locator('#admin-password').press('Enter');
  await page.getByRole('alert').waitFor();
  assert.equal(requestCount, 1, 'Enter submits exactly once');
  assert.equal(await page.locator('#admin-password').inputValue(), '');
  assert.equal(await page.locator('#admin-password').getAttribute('type'), 'password');
  assertClean(collection);
  await context.close();
}

async function verifyPendingGuardAndDraftSafety(browser) {
  const context = await browser.newContext({ viewport: { width: 1024, height: 800 } });
  const page = await context.newPage();
  const collection = collectFailures(page, 'pending and draft safety');
  await gotoLogin(page);
  const gate = deferred();
  const started = deferred();
  let requestCount = 0;
  await page.route('**/api/v1/login', async route => {
    requestCount += 1;
    started.resolve();
    await gate.promise;
    await fulfillJson(route, { ok: false, message: '用户名或密码错误' });
  }, { times: 1 });
  await page.locator('#admin-username').fill(' admin ');
  await page.locator('#admin-password').fill('submitted-password');
  await page.locator('.auth-submit').click();
  await started.promise;
  assert.equal(await page.locator('.auth-submit').isDisabled(), true);
  assert.equal(await page.locator('.auth-submit').getAttribute('aria-busy'), 'true');
  assert.equal(await page.locator('.auth-submit-text').innerText(), '正在验证…');
  assert.equal(await page.locator('#login-progress').innerText(), '正在验证登录信息');
  assert.equal(await page.locator('#admin-username').isEditable(), true);
  assert.equal(await page.locator('#admin-password').isEditable(), true);
  await page.locator('#form-admin').evaluate(form => {
    for (let index = 0; index < 5; index += 1) form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
  await page.locator('#admin-username').fill('new draft');
  await page.locator('#admin-password').fill('new-password');
  gate.resolve();
  await page.getByRole('alert').waitFor();
  assert.equal(requestCount, 1, 'synchronous pending guard must prevent duplicate requests');
  assert.equal(await page.locator('#admin-username').inputValue(), 'new draft');
  assert.equal(await page.locator('#admin-password').inputValue(), 'new-password');
  assert.equal(await page.locator('.auth-submit').isEnabled(), true);
  assert.equal(await page.locator('#login-progress').innerText(), '');
  assertClean(collection);
  await context.close();
}

async function verifyRealAuthentication(browser) {
  assert(fixturePassword, 'runner must expose only the fictional fixture password');
  const failedContext = await browser.newContext();
  const failedPage = await failedContext.newPage();
  const failedCollection = collectFailures(failedPage, 'real invalid credentials');
  await gotoLogin(failedPage);
  await failedPage.locator('#admin-username').fill(' admin ');
  await failedPage.locator('#admin-password').fill('wrong-fixture-password');
  await failedPage.locator('.auth-submit').click();
  await failedPage.getByRole('alert').filter({ hasText: '用户名或密码错误' }).waitFor();
  assert.equal(await failedPage.locator('#admin-username').inputValue(), 'admin');
  assert.equal(await failedPage.locator('#admin-password').inputValue(), '');
  assert.equal((await failedContext.cookies()).some(cookie => cookie.name === 'sid'), false);
  assertClean(failedCollection);
  await failedContext.close();

  const authenticated = await browser.newContext();
  const page = await authenticated.newPage();
  const collection = collectFailures(page, 'real correct credentials');
  await gotoLogin(page);
  await page.locator('#admin-username').fill('admin');
  await page.locator('#admin-password').fill(fixturePassword);
  await page.locator('.auth-submit').click();
  await page.waitForURL(`${baseUrl}/admin?msg=login+success`);
  const sid = (await authenticated.cookies()).find(cookie => cookie.name === 'sid');
  assert(sid, 'successful login must store sid');
  assert.equal(sid.httpOnly, true);
  assert.deepEqual(await page.evaluate(async () => {
    const response = await fetch('/api/v1/session');
    return { status: response.status, body: await response.json() };
  }), { status: 200, body: { role: 'admin' } });
  assertClean(collection);

  const anonymous = await browser.newContext();
  const anonymousPage = await anonymous.newPage();
  const anonymousCollection = collectFailures(anonymousPage, 'separate anonymous browser', { allowedResponses: ['GET /api/v1/session 401'] });
  await anonymousPage.goto(baseUrl + '/login');
  assert.deepEqual(await anonymousPage.evaluate(async () => {
    const response = await fetch('/api/v1/session');
    return { status: response.status, body: await response.json() };
  }), { status: 401, body: { error: 'login_required' } });
  assertClean(anonymousCollection);
  await anonymous.close();
  await authenticated.close();
}

async function verifyFeedbackFaults(browser) {
  const cases = [
    {
      name: 'rate limit', status: 429,
      body: JSON.stringify({ ok: false, message: '登录尝试过于频繁，请 1 小时后再试' }),
      contentType: 'application/json', expected: '登录尝试过于频繁，请 1 小时后再试', clears: true,
      allowedResponses: ['POST /api/v1/login 429'],
    },
    { name: 'service unavailable', status: 503, body: '{}', contentType: 'application/json', expected: transportError, clears: false, allowedResponses: ['POST /api/v1/login 503'] },
    { name: 'malformed JSON', status: 200, body: '{bad json', contentType: 'application/json', expected: transportError, clears: false, allowedResponses: [] },
    { name: 'HTML', status: 200, body: JSON.stringify({ ok: false, message: 'wrong MIME' }), contentType: 'text/html', expected: transportError, clears: false, allowedResponses: [] },
    { name: 'unexpected shape', status: 200, body: JSON.stringify({ ok: false, message: 'hidden', session_id: 'must-not-be-read' }), contentType: 'application/json', expected: transportError, clears: false, allowedResponses: [] },
    { name: 'untrusted redirect', status: 200, body: JSON.stringify({ ok: true, redirect_to: 'https://attacker.invalid/' }), contentType: 'application/json', expected: transportError, clears: false, allowedResponses: [] },
    { name: 'success status mismatch', status: 201, body: JSON.stringify({ ok: true, redirect_to: '/admin?msg=login+success' }), contentType: 'application/json', expected: transportError, clears: false, allowedResponses: [] },
  ];
  for (const fault of cases) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const collection = collectFailures(page, fault.name, { allowedResponses: fault.allowedResponses });
    let requests = 0;
    await page.route('**/api/v1/login', async route => {
      requests += 1;
      await route.fulfill({ status: fault.status, contentType: fault.contentType, body: fault.body });
    }, { times: 1 });
    await gotoLogin(page);
    await page.locator('#admin-username').fill('draft-user');
    await page.locator('#admin-password').fill('draft-password');
    await page.locator('.auth-submit').click();
    await page.getByRole('alert').filter({ hasText: fault.expected }).waitFor();
    assert.equal(requests, 1, `${fault.name} must not retry automatically`);
    assert.equal(await page.locator('#admin-username').inputValue(), 'draft-user');
    assert.equal(await page.locator('#admin-password').inputValue(), fault.clears ? '' : 'draft-password');
    assert.equal(await page.evaluate(() => localStorage.length), 0, `${fault.name} must not persist response data`);
    assertClean(collection);
    await context.close();
  }

  const context = await browser.newContext();
  const page = await context.newPage();
  const collection = collectFailures(page, 'network failure', { allowedFailures: ['POST /api/v1/login'] });
  let requests = 0;
  await page.route('**/api/v1/login', async route => { requests += 1; await route.abort('failed'); }, { times: 1 });
  await gotoLogin(page);
  await page.locator('#admin-username').fill('network-user');
  await page.locator('#admin-password').fill('network-password');
  await page.locator('.auth-submit').click();
  await page.getByRole('alert').filter({ hasText: transportError }).waitFor();
  assert.equal(requests, 1);
  assert.equal(await page.locator('#admin-password').inputValue(), 'network-password');
  assertClean(collection);
  await context.close();

  const timeoutContext = await browser.newContext();
  const timeoutPage = await timeoutContext.newPage();
  const timeoutCollection = collectFailures(timeoutPage, 'timeout', { allowedFailures: ['POST /api/v1/login'] });
  const timeoutStarted = deferred();
  let delayedRoute;
  await timeoutPage.route('**/api/v1/login', async route => { delayedRoute = route; timeoutStarted.resolve(); });
  await timeoutPage.clock.install();
  await gotoLogin(timeoutPage);
  await timeoutPage.locator('#admin-username').fill('timeout-user');
  await timeoutPage.locator('#admin-password').fill('timeout-password');
  await timeoutPage.locator('.auth-submit').click();
  await timeoutStarted.promise;
  await timeoutPage.clock.fastForward(15_000);
  await timeoutPage.getByRole('alert').filter({ hasText: transportError }).waitFor();
  assert.equal(await timeoutPage.locator('#admin-password').inputValue(), 'timeout-password');
  await delayedRoute.abort('failed').catch(() => {});
  assertClean(timeoutCollection);
  await timeoutContext.close();

  const escapedContext = await browser.newContext();
  const escapedPage = await escapedContext.newPage();
  const escapedCollection = collectFailures(escapedPage, 'escaped feedback');
  const hostile = '<img src=x onerror=alert(1)> & failure';
  await escapedPage.route('**/api/v1/login', route => fulfillJson(route, { ok: false, message: hostile }), { times: 1 });
  await gotoLogin(escapedPage);
  await escapedPage.locator('#admin-username').fill('admin');
  await escapedPage.locator('#admin-password').fill('wrong');
  await escapedPage.locator('.auth-submit').click();
  const alert = escapedPage.getByRole('alert');
  await alert.waitFor();
  assert.equal(await alert.innerText(), hostile);
  assert.equal(await alert.getAttribute('aria-live'), 'assertive');
  assert.equal(await alert.getAttribute('aria-atomic'), 'true');
  assert.equal(await alert.locator('img').count(), 0);
  assertClean(escapedCollection);
  await escapedContext.close();
}

async function verifyLifecycleStaleSuppression(browser) {
  for (const eventName of ['pageshow', 'pagehide']) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const collection = collectFailures(page, `${eventName} stale result`, { allowedFailures: ['POST /api/v1/login'] });
    const started = deferred();
    let delayedRoute;
    await page.route('**/api/v1/login', async route => { delayedRoute = route; started.resolve(); });
    await gotoLogin(page);
    await page.locator('#admin-username').fill('admin');
    await page.locator('#admin-password').fill('late-password');
    await page.locator('#login-password-toggle').click();
    await page.locator('.auth-submit').click();
    await started.promise;
    await page.evaluate(name => window.dispatchEvent(new PageTransitionEvent(name)), eventName);
    if (eventName === 'pageshow') {
      assert.equal(await page.locator('.auth-submit').isEnabled(), true);
      assert.equal(await page.locator('#admin-password').getAttribute('type'), 'password');
      assert.equal(await page.locator('#login-progress').innerText(), '');
    }
    await delayedRoute.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: false, message: 'STALE' }) }).catch(() => {});
    await page.waitForTimeout(50);
    assert.equal(await page.getByText('STALE', { exact: true }).count(), 0);
    assertClean(collection);
    await context.close();
  }

  const context = await browser.newContext();
  const page = await context.newPage();
  const collection = collectFailures(page, 'unmount stale result', { allowedFailures: ['POST /api/v1/login'] });
  const started = deferred();
  let delayedRoute;
  await page.route('**/api/v1/login', async route => { delayedRoute = route; started.resolve(); });
  await gotoLogin(page);
  await page.locator('#admin-username').fill('admin');
  await page.locator('#admin-password').fill('late-password');
  await page.locator('.auth-submit').click();
  await started.promise;
  await page.goto(baseUrl + '/login');
  await delayedRoute.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: false, message: 'STALE' }) }).catch(() => {});
  assert.equal(await page.getByText('STALE', { exact: true }).count(), 0);
  assertClean(collection);
  await context.close();
}

async function bounds(page, selector) {
  const box = await page.locator(selector).first().boundingBox();
  assert(box, `${selector} must have visible bounds`);
  return box;
}

async function compareBounds(reactPage, legacyPage, width) {
  for (const selector of ['#main-content', '.login-stage', '.login-layout', '.login-panel', '.login-form .field', '.auth-submit']) {
    const actual = await bounds(reactPage, selector);
    const expected = await bounds(legacyPage, selector);
    for (const key of ['x', 'y', 'width', 'height']) {
      assert(Math.abs(actual[key] - expected[key]) <= 2, `${selector} ${key} parity at ${width}px: React ${actual[key]}, legacy ${expected[key]}`);
    }
  }
  assert.equal(await reactPage.locator('.login-story').isVisible(), await legacyPage.locator('.login-story').isVisible());
  if (await reactPage.locator('.login-story').isVisible()) {
    const actual = await bounds(reactPage, '.login-story');
    const expected = await bounds(legacyPage, '.login-story');
    for (const key of ['x', 'y', 'width', 'height']) assert(Math.abs(actual[key] - expected[key]) <= 2);
  }
}

async function contentSnapshot(page) {
  const normalized = locator => locator.evaluate(element => element.innerHTML.replace(/>\s+</g, '><').trim());
  return {
    text: (await page.locator('#main-content').innerText()).replace(/\s+/g, ' ').trim(),
    links: await page.locator('a').evaluateAll(links => links.map(link => ({ text: link.textContent.trim(), href: link.getAttribute('href') }))),
    form: await page.locator('#form-admin').evaluate(form => ({ method: form.getAttribute('method'), action: form.getAttribute('action') })),
    fields: await page.locator('#form-admin input').evaluateAll(inputs => inputs.map(input => ({
      id: input.id, name: input.getAttribute('name'), type: input.getAttribute('type'), required: input.hasAttribute('required'),
      autocomplete: input.getAttribute('autocomplete'), autocapitalize: input.getAttribute('autocapitalize'),
      spellcheck: input.getAttribute('spellcheck'), maxlength: input.getAttribute('maxlength'), placeholder: input.getAttribute('placeholder'),
    }))),
    networkSvg: await normalized(page.locator('.login-network svg')),
    kickerSvg: await normalized(page.locator('.login-panel-kicker svg')),
    background: await page.evaluate(() => getComputedStyle(document.body).backgroundColor),
  };
}

async function verifyLegacyParity(browser) {
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, reducedMotion: 'reduce' });
  for (const width of [1920, 1024, 390]) {
    const reactPage = await context.newPage();
    const reactCollection = collectFailures(reactPage, `React parity ${width}`);
    await reactPage.setViewportSize({ width, height: 1080 });
    await gotoLogin(reactPage, '#main-content');
    assert.equal(await reactPage.locator('#main-content').evaluate(element => element === document.activeElement), true);

    const legacyPage = await context.newPage();
    const legacyCollection = collectFailures(legacyPage, `legacy parity ${width}`);
    await legacyPage.setViewportSize({ width, height: 1080 });
    const response = await legacyPage.goto(`${baseUrl}/login#main-content`);
    assert.equal(response.status(), 200);
    await legacyPage.locator('#form-admin').waitFor();
    await legacyPage.waitForFunction(() => document.readyState === 'complete');
    assert.equal(await legacyPage.locator('#main-content').evaluate(element => element === document.activeElement), true);
    assert.equal(await legacyPage.locator('script[src*="login.js"]').count(), 1);
    assert.equal(await legacyPage.locator('script[src*="ui-core.js"]').count(), 1);
    await reactPage.evaluate(() => scrollTo(0, 0));
    await legacyPage.evaluate(() => scrollTo(0, 0));
    assert.deepEqual(await contentSnapshot(reactPage), await contentSnapshot(legacyPage), `legacy/React content parity at ${width}px`);
    await compareBounds(reactPage, legacyPage, width);
    assert.equal(await reactPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(await legacyPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    if (screenshotDir) {
      fs.mkdirSync(screenshotDir, { recursive: true });
      await reactPage.screenshot({ path: path.join(screenshotDir, `react-login-${width}.png`), fullPage: true });
      await legacyPage.screenshot({ path: path.join(screenshotDir, `legacy-login-${width}.png`), fullPage: true });
    }
    assertClean(reactCollection);
    assertClean(legacyCollection);
    await reactPage.close();
    await legacyPage.close();
  }
  await context.close();
}

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    await verifyDocumentValidationAndKeyboard(browser);
    await verifyPendingGuardAndDraftSafety(browser);
    await verifyRealAuthentication(browser);
    await verifyFeedbackFaults(browser);
    await verifyLifecycleStaleSuppression(browser);
    await verifyLegacyParity(browser);
    console.log('PASS: React login real auth, validation, fault recovery, stale safety, isolation, and visual parity');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
