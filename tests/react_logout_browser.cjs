const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const baseUrl = process.env.PREVIEW_BASE_URL || 'http://127.0.0.1:18765';
const fixturePassword = process.env.REACT_PREVIEW_LOGIN_PASSWORD;
const screenshotDir = process.env.REACT_LOGOUT_SCREENSHOT_DIR;
const logoutError = '退出结果未确认，请检查网络后重试。';

const realms = [
  {
    name: 'admin', entry: '/__react/logout', legacy: '/logout', api: '/api/v1/logout',
    heading: '退出管理后台？', action: '/logout', cancel: '/admin', cookie: 'sid',
  },
  {
    name: 'user', entry: '/__react/user/logout', legacy: '/user/logout', api: '/api/v1/user/logout',
    heading: '退出用户面板？', action: '/user/logout', cancel: '/user/panel', cookie: 'usid',
  },
];

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function routeOf(url) {
  const parsed = new URL(url);
  return `${parsed.pathname}${parsed.search}`;
}

function collectFailures(page, scenario, { allowedResponses = [], allowedFailures = [] } = {}) {
  const failures = [];
  page.on('pageerror', error => failures.push(`${scenario} pageerror: ${error.message}`));
  page.on('requestfailed', request => {
    const pair = `${request.method()} ${routeOf(request.url())}`;
    if (!allowedFailures.includes(pair)) failures.push(`${scenario} requestfailed: ${pair} ${request.failure()?.errorText || 'unknown'}`);
  });
  page.on('response', response => {
    if (response.status() < 400) return;
    const pair = `${response.request().method()} ${routeOf(response.url())} ${response.status()}`;
    if (!allowedResponses.includes(pair)) failures.push(`${scenario} response: ${pair}`);
  });
  return failures;
}

function assertClean(failures) {
  assert.deepEqual(failures, [], 'scenario must have no unexpected errors or failed requests');
}

async function addCookies(context, cookies) {
  await context.addCookies(Object.entries(cookies).map(([name, value]) => ({ name, value, url: baseUrl })));
}

async function gotoLogout(page, realm, fragment = '') {
  const response = await page.goto(`${baseUrl}${realm.entry}${fragment}`);
  assert.equal(response.status(), 200, `${realm.name} controlled logout entry must be available`);
  await page.getByRole('heading', { name: realm.heading, exact: true }).waitFor();
}

async function verifyConfirmationDocumentsAndCancel(browser) {
  for (const realm of realms) {
    const context = await browser.newContext({ viewport: { width: 1024, height: 800 } });
    const page = await context.newPage();
    const failures = collectFailures(page, `${realm.name} confirmation`, {
      allowedResponses: realm.name === 'user'
        ? ['GET /api/v1/user/panel 401']
        : ['GET /api/v1/admin/overview-page 401', 'GET /api/v1/session 401'],
    });
    const posts = [];
    page.on('request', request => {
      if (request.method() === 'POST') posts.push(routeOf(request.url()));
    });
    await gotoLogout(page, realm, '#main-content');
    assert.equal(await page.title(), '确认退出');
    assert.equal(await page.locator('body').getAttribute('class'), null);
    assert.equal(await page.locator('.skip-link').getAttribute('href'), '#main-content');
    assert.equal(await page.locator('#main-content').getAttribute('tabindex'), '-1');
    assert.equal(await page.locator('#main-content').evaluate(node => node === document.activeElement), true);
    assert.equal(await page.locator('.auth-brand-text strong').innerText(), 'preview.invalid');
    assert.equal(await page.locator('.auth-brand-text small').innerText(), '安全退出');
    assert.equal(await page.locator('.auth-subtitle').innerText(), '确认后会结束当前设备的登录会话；其他设备不受影响。');
    assert.deepEqual(await page.locator('form').evaluate(form => ({ method: form.getAttribute('method'), action: form.getAttribute('action') })), {
      method: 'post', action: realm.action,
    });
    assert.equal(await page.locator('.auth-back').getAttribute('href'), realm.cancel);
    await page.locator('.auth-back').click();
    const expectedCancel = `${baseUrl}/__react${realm.cancel === '/admin' ? '/admin' : '/user/panel'}`;
    await page.waitForURL(expectedCancel);
    assert.deepEqual(posts, [], 'cancel must not submit a mutation');
    assertClean(failures);
    await context.close();
  }
}

async function verifyHeldDuplicateAndFixedRoutes(browser) {
  for (const realm of realms) {
    const context = await browser.newContext({ viewport: { width: 1024, height: 800 } });
    const page = await context.newPage();
    const failures = collectFailures(page, `${realm.name} duplicate guard`);
    const gate = deferred();
    const started = deferred();
    let requests = 0;
    await page.route(`**${realm.api}`, async route => {
      requests += 1;
      assert.equal(route.request().postDataBuffer()?.length || 0, 0, 'logout must submit an empty form body');
      assert.equal((await route.request().allHeaders()).accept, 'application/json');
      started.resolve();
      await gate.promise;
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, redirect_to: '/login' }) });
    }, { times: 1 });
    await gotoLogout(page, realm);
    await page.getByRole('button', { name: '确认退出', exact: true }).click();
    await started.promise;
    const button = page.getByRole('button', { name: '正在退出…', exact: true });
    assert.equal(await button.isDisabled(), true);
    assert.equal(await button.getAttribute('aria-busy'), 'true');
    await page.locator('form').evaluate(form => {
      for (let index = 0; index < 5; index += 1) form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    assert.equal(requests, 1, 'synchronous duplicate guard must send one request');
    gate.resolve();
    await page.waitForURL(`${baseUrl}/login`).catch(async error => {
      throw new Error(`${realm.name} held logout did not navigate; url=${page.url()} alerts=${JSON.stringify(await page.getByRole('alert').allTextContents())} failures=${JSON.stringify(failures)}`, { cause: error });
    });
    assert.equal(requests, 1);
    assertClean(failures);
    await context.close();
  }
}

async function verifyFaultRecovery(browser) {
  const faults = [
    { name: '503', status: 503, type: 'application/json', body: '{}', allowedResponses: ['POST /api/v1/logout 503'] },
    { name: 'wrong MIME', status: 200, type: 'text/html', body: JSON.stringify({ ok: true, redirect_to: '/login' }) },
    { name: 'malformed JSON', status: 200, type: 'application/json', body: '{bad json' },
    { name: 'extra key', status: 200, type: 'application/json', body: JSON.stringify({ ok: true, redirect_to: '/login', session: 'secret' }) },
    { name: 'unsafe destination', status: 200, type: 'application/json', body: JSON.stringify({ ok: true, redirect_to: 'https://attacker.invalid/' }) },
    { name: 'non-200 success', status: 201, type: 'application/json', body: JSON.stringify({ ok: true, redirect_to: '/login' }) },
  ];
  for (const fault of faults) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const failures = collectFailures(page, fault.name, { allowedResponses: fault.allowedResponses || [] });
    let requests = 0;
    await page.route('**/api/v1/logout', async route => {
      requests += 1;
      if (requests === 1) {
        await route.fulfill({ status: fault.status, contentType: fault.type, body: fault.body });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, redirect_to: '/login' }) });
      }
    });
    await gotoLogout(page, realms[0]);
    await page.getByRole('button', { name: '确认退出', exact: true }).click();
    const alert = page.getByRole('alert').filter({ hasText: logoutError });
    await alert.waitFor();
    assert.equal(requests, 1, `${fault.name} must not retry automatically`);
    assert.equal(await page.getByRole('button', { name: '确认退出', exact: true }).isEnabled(), true);
    assert.equal(new URL(page.url()).pathname, '/__react/logout');
    assert.equal(await page.evaluate(() => localStorage.length), 0);
    await page.getByRole('button', { name: '确认退出', exact: true }).click();
    await page.waitForURL(`${baseUrl}/login`);
    assert.equal(requests, 2, `${fault.name} must allow one manual retry`);
    assertClean(failures);
    await context.close();
  }

  const context = await browser.newContext();
  const page = await context.newPage();
  const failures = collectFailures(page, 'network recovery', { allowedFailures: ['POST /api/v1/logout'] });
  let requests = 0;
  await page.route('**/api/v1/logout', async route => {
    requests += 1;
    if (requests === 1) await route.abort('failed');
    else await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, redirect_to: '/login' }) });
  });
  await gotoLogout(page, realms[0]);
  await page.getByRole('button', { name: '确认退出', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: logoutError }).waitFor();
  assert.equal(requests, 1);
  await page.waitForTimeout(50);
  assert.equal(requests, 1, 'network failure must not retry unsolicited');
  await page.getByRole('button', { name: '确认退出', exact: true }).click();
  await page.waitForURL(`${baseUrl}/login`);
  assert.equal(requests, 2);
  assertClean(failures);
  await context.close();
}

async function verifyTimeoutAndPageTransitions(browser) {
  const timeoutContext = await browser.newContext();
  const timeoutPage = await timeoutContext.newPage();
  const timeoutFailures = collectFailures(timeoutPage, 'logout timeout', { allowedFailures: ['POST /api/v1/logout'] });
  let delayedRoute;
  let requests = 0;
  const started = deferred();
  await timeoutPage.route('**/api/v1/logout', async route => {
    requests += 1;
    if (requests === 1) {
      delayedRoute = route;
      started.resolve();
    } else {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, redirect_to: '/login' }) });
    }
  });
  await timeoutPage.clock.install();
  await gotoLogout(timeoutPage, realms[0]);
  await timeoutPage.getByRole('button', { name: '确认退出', exact: true }).click();
  await started.promise;
  await timeoutPage.clock.fastForward(15_000);
  await timeoutPage.getByRole('alert').filter({ hasText: logoutError }).waitFor();
  assert.equal(requests, 1);
  assert.equal(await timeoutPage.getByRole('button', { name: '确认退出', exact: true }).isEnabled(), true);
  await timeoutPage.clock.fastForward(30_000);
  assert.equal(requests, 1, 'timeout must not retry unsolicited');
  await timeoutPage.getByRole('button', { name: '确认退出', exact: true }).click();
  await timeoutPage.waitForURL(`${baseUrl}/login`);
  assert.equal(requests, 2, 'timeout must allow one manual retry');
  await delayedRoute.abort('failed').catch(() => {});
  assertClean(timeoutFailures);
  await timeoutContext.close();

  for (const eventName of ['pagehide', 'pageshow']) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const failures = collectFailures(page, `${eventName} invalidation`, { allowedFailures: ['POST /api/v1/logout'] });
    let delayed;
    let count = 0;
    const requestStarted = deferred();
    await page.route('**/api/v1/logout', async route => {
      count += 1;
      if (count === 1) {
        delayed = route;
        requestStarted.resolve();
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, redirect_to: '/login' }) });
      }
    });
    await gotoLogout(page, realms[0]);
    await page.getByRole('button', { name: '确认退出', exact: true }).click();
    await requestStarted.promise;
    await page.evaluate(name => window.dispatchEvent(new PageTransitionEvent(name)), eventName);
    if (eventName === 'pageshow') {
      assert.equal(await page.getByRole('button', { name: '确认退出', exact: true }).isEnabled(), true);
    }
    await delayed.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, redirect_to: '/login' }) }).catch(() => {});
    await page.waitForTimeout(50);
    assert.equal(new URL(page.url()).pathname, '/__react/logout', 'stale settlement must not navigate');
    assert.equal(await page.getByRole('alert').count(), 0, 'stale settlement must not show feedback');
    assert.equal(count, 1, 'transition must not retry unsolicited');
    if (eventName === 'pageshow') {
      await page.getByRole('button', { name: '确认退出', exact: true }).click();
      await page.waitForURL(`${baseUrl}/login`);
      assert.equal(count, 2, 'pageshow must allow a fresh attempt');
    }
    assertClean(failures);
    await context.close();
  }
}

async function createAdminContext(browser) {
  assert(fixturePassword, 'runner must expose the fictional preview password');
  const context = await browser.newContext();
  const response = await context.request.post(`${baseUrl}/api/v1/login`, {
    form: { admin_username: 'admin', admin_password: fixturePassword },
  });
  assert.equal(response.status(), 200);
  assert.deepEqual(await response.json(), { ok: true, redirect_to: '/admin?msg=login+success' });
  return context;
}

async function verifyShellDirectLogoutAndErrors(browser) {
  const successContext = await createAdminContext(browser);
  const successPage = await successContext.newPage();
  const successFailures = collectFailures(successPage, 'shell direct success');
  const posts = [];
  successPage.on('request', request => {
    if (request.method() === 'POST') posts.push(routeOf(request.url()));
  });
  await successPage.goto(`${baseUrl}/__react/admin/logs`);
  await successPage.getByText('preview-admin', { exact: true }).waitFor();
  await successPage.getByRole('button', { name: '退出登录', exact: true }).click();
  await successPage.waitForURL(`${baseUrl}/login`);
  assert.deepEqual(posts, ['/api/v1/logout'], 'shell logout must use one exact API POST');
  assert.equal(successPage.url().includes('/__react/logout'), false, 'shell must not display a confirmation page');
  assertClean(successFailures);
  await successContext.close();

  for (const viewport of [{ width: 1024, height: 768 }, { width: 390, height: 844 }]) {
    const context = await createAdminContext(browser);
    if (viewport.width > 880) {
      await context.addInitScript(() => localStorage.setItem('hy2.sidebar', 'collapsed'));
    }
    const page = await context.newPage();
    const failures = collectFailures(page, `shell error ${viewport.width}`, { allowedResponses: ['POST /api/v1/logout 503'] });
    await page.setViewportSize(viewport);
    let requests = 0;
    const gate = deferred();
    const started = deferred();
    await page.route('**/api/v1/logout', async route => {
      requests += 1;
      started.resolve();
      await gate.promise;
      await route.fulfill({ status: 503, contentType: 'application/json', body: '{}' });
    });
    await page.goto(`${baseUrl}/__react/admin/logs`);
    await page.getByText('preview-admin', { exact: true }).waitFor();
    if (viewport.width <= 880) await page.locator('#sidebar-toggle').click();
    await page.getByRole('button', { name: '退出登录', exact: true }).click();
    await started.promise;
    const logoutButton = page.getByRole('button', { name: '退出登录', exact: true });
    assert.equal(await logoutButton.isDisabled(), true);
    assert.equal(await logoutButton.getAttribute('aria-busy'), 'true');
    assert.equal(await logoutButton.locator('span').innerText(), '正在退出…');
    gate.resolve();
    const alert = page.locator('#main-content > .err').filter({ hasText: logoutError });
    await alert.waitFor();
    await page.waitForFunction(() => document.activeElement?.getAttribute('role') === 'alert');
    assert.equal(await alert.evaluate(node => node === document.activeElement), true);
    assert.equal(await alert.isVisible(), true);
    assert.equal(requests, 1);
    if (viewport.width > 880) {
      assert.equal(await page.locator('.app').evaluate(node => node.classList.contains('sidebar-collapsed')), true);
    } else {
      const toggle = page.locator('#sidebar-toggle');
      assert.equal(await toggle.getAttribute('aria-expanded'), 'false', 'logout error must close mobile drawer');
      await toggle.click();
      await page.locator('#sidebar-close').click();
      await page.waitForFunction(() => document.activeElement?.id === 'sidebar-toggle');
      assert.equal(await alert.evaluate(node => node === document.activeElement), false, 'later drawer actions must not refocus old feedback');
    }
    assertClean(failures);
    await context.close();
  }
}

async function verifyRealRealmIsolation(browser) {
  const admin = process.env.REACT_PREVIEW_ADMIN_COOKIE;
  const user = process.env.REACT_PREVIEW_USER_COOKIE;
  const adminOther = process.env.REACT_PREVIEW_ADMIN_OTHER_COOKIE;
  const userOther = process.env.REACT_PREVIEW_USER_OTHER_COOKIE;
  assert(admin && user && adminOther && userOther, 'runner must expose fictional current and second-device sessions');

  const context = await browser.newContext();
  await addCookies(context, { sid: admin, usid: user });
  const page = await context.newPage();
  await gotoLogout(page, realms[0]);
  await page.getByRole('button', { name: '确认退出', exact: true }).click();
  await page.waitForURL(`${baseUrl}/login`);
  assert.equal((await context.cookies()).some(cookie => cookie.name === 'sid'), false);
  assert.equal((await context.cookies()).some(cookie => cookie.name === 'usid'), true);
  assert.deepEqual(await context.request.get(`${baseUrl}/api/v1/session`).then(response => response.json()), { role: 'user', username: 'demo_alex' });

  await gotoLogout(page, realms[1]);
  await page.getByRole('button', { name: '确认退出', exact: true }).click();
  await page.waitForURL(`${baseUrl}/login`);
  assert.equal((await context.cookies()).some(cookie => cookie.name === 'usid'), false);
  assert.equal((await context.cookies()).some(cookie => cookie.name === 'sid'), false);
  await context.close();

  const adminSecond = await browser.newContext();
  await addCookies(adminSecond, { sid: adminOther });
  assert.deepEqual(await adminSecond.request.get(`${baseUrl}/api/v1/session`).then(response => response.json()), { role: 'admin' });
  await adminSecond.close();
  const userSecond = await browser.newContext();
  await addCookies(userSecond, { usid: userOther });
  assert.deepEqual(await userSecond.request.get(`${baseUrl}/api/v1/session`).then(response => response.json()), { role: 'user', username: 'demo_alex' });
  await userSecond.close();

  for (let attempt = 0; attempt < 2; attempt += 1) {
    const anonymous = await browser.newContext();
    const anonymousPage = await anonymous.newPage();
    await gotoLogout(anonymousPage, realms[0]);
    await anonymousPage.getByRole('button', { name: '确认退出', exact: true }).click();
    await anonymousPage.waitForURL(`${baseUrl}/login`);
    await anonymous.close();
  }
}

async function bounds(page, selector) {
  const box = await page.locator(selector).first().boundingBox();
  assert(box, `${selector} must have visible bounds`);
  return box;
}

async function confirmationSnapshot(page) {
  return {
    title: await page.title(),
    bodyClass: await page.locator('body').getAttribute('class'),
    text: (await page.locator('#main-content').innerText()).replace(/\s+/g, ' ').trim(),
    links: await page.locator('a').evaluateAll(links => links.map(link => ({ text: link.textContent.trim(), href: link.getAttribute('href') }))),
    form: await page.locator('form').evaluate(form => ({ method: form.getAttribute('method'), action: form.getAttribute('action') })),
    button: await page.locator('button').evaluate(button => ({ text: button.textContent.trim(), type: button.getAttribute('type'), className: button.className })),
  };
}

async function verifyResponsiveLogout(browser) {
  const context = await browser.newContext({ reducedMotion: 'reduce' });
  for (const realm of realms) {
    for (const width of [1920, 1024, 390]) {
      const reactPage = await context.newPage();
      const reactFailures = collectFailures(reactPage, `React ${realm.name} parity ${width}`);
      await reactPage.setViewportSize({ width, height: 1080 });
      await gotoLogout(reactPage, realm);
      const current = await confirmationSnapshot(reactPage);
      assert.equal(current.form.method, 'post');
      assert.equal(current.form.action, realm.entry === '/__react/logout' ? '/logout' : '/user/logout');
      for (const selector of ['#main-content', '.auth-page', '.auth-wrap', '.auth-card', '.auth-title', 'form', '.btn-full', '.auth-back']) {
        const box = await bounds(reactPage, selector);
        assert(box.width > 0 && box.height > 0, `${realm.name} ${selector} must be visible at ${width}px`);
      }
      assert.equal(await reactPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      if (screenshotDir) {
        fs.mkdirSync(screenshotDir, { recursive: true });
        await reactPage.screenshot({ path: path.join(screenshotDir, `react-${realm.name}-logout-${width}.png`), fullPage: true });
      }
      assertClean(reactFailures);
      await reactPage.close();
    }
  }
  await context.close();
}

async function buildHarness() {
  const { build } = await import('vite');
  const react = (await import('@vitejs/plugin-react')).default;
  const result = await build({
    configFile: false,
    logLevel: 'silent',
    plugins: [react()],
    define: { 'process.env.NODE_ENV': JSON.stringify('production') },
    build: {
      write: false,
      minify: false,
      lib: {
        entry: path.resolve(__dirname, 'react_form_action_harness.tsx'),
        name: 'ReactFormActionHarness',
        formats: ['iife'],
      },
    },
  });
  const outputs = Array.isArray(result) ? result.flatMap(item => item.output) : result.output;
  const chunk = outputs.find(item => item.type === 'chunk');
  assert(chunk, 'Vite must produce the test-only harness IIFE');
  return chunk.code;
}

async function verifyRealRootLifecycle(browser) {
  const code = await buildHarness();
  const page = await browser.newPage();
  const failures = collectFailures(page, 'real root lifecycle');
  await page.setContent('<!doctype html><html><body></body></html>');
  await page.addScriptTag({ content: code });
  await page.getByRole('button', { name: '挂载' }).click();
  await page.getByRole('button', { name: '开始', exact: true }).click();
  assert.equal(await page.locator('#busy').innerText(), 'true');
  await page.getByRole('button', { name: '卸载' }).click();
  assert.equal(await page.locator('#aborts').innerText(), '1', 'unmount must abort the active operation');
  await page.getByRole('button', { name: '完成', exact: true }).click();
  await page.waitForTimeout(20);
  assert.equal(await page.locator('#results').innerText(), '0', 'late result after unmount must be ignored');
  assert.equal(await page.locator('#errors').innerText(), '0', 'late result after unmount must not become an error');
  await page.getByRole('button', { name: '挂载' }).click();
  await page.getByRole('button', { name: '开始', exact: true }).click();
  await page.getByRole('button', { name: '完成', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#results')?.textContent === '1');
  assert.equal(await page.locator('#busy').innerText(), 'false', 'remount must have a fresh usable lifecycle');

  await page.clock.install();
  await page.getByRole('button', { name: '超时后同步完成' }).click();
  await page.clock.fastForward(15_000);
  await page.waitForFunction(() => document.querySelector('#errors')?.textContent === '1');
  assert.equal(await page.locator('#results').innerText(), '1', 'an abort listener resolving synchronously must not turn timeout into success');
  assert.equal(await page.locator('#busy').innerText(), 'false');
  await page.getByRole('button', { name: '开始回调抛错' }).click();
  await page.waitForFunction(() => document.querySelector('#errors')?.textContent === '2');
  assert.equal(await page.locator('#results').innerText(), '1');
  assert.equal(await page.locator('#busy').innerText(), 'false', 'throwing start callback must not strand busy state');
  assertClean(failures);
  await page.close();
}

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    await verifyConfirmationDocumentsAndCancel(browser);
    await verifyHeldDuplicateAndFixedRoutes(browser);
    await verifyFaultRecovery(browser);
    await verifyTimeoutAndPageTransitions(browser);
    await verifyShellDirectLogoutAndErrors(browser);
    await verifyRealRealmIsolation(browser);
    await verifyResponsiveLogout(browser);
    await verifyRealRootLifecycle(browser);
    console.log('PASS: React logout confirmation, shell, faults, lifecycle, realm isolation, and responsive layout');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
