const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const baseUrl = process.env.PREVIEW_BASE_URL || 'http://127.0.0.1:18765';
const adminCookie = process.env.REACT_PREVIEW_ADMIN_COOKIE;
const adminOtherCookie = process.env.REACT_PREVIEW_ADMIN_OTHER_COOKIE;
const passwordUserCookie = process.env.REACT_PREVIEW_PASSWORD_USER_COOKIE;
const passwordUserOtherCookie = process.env.REACT_PREVIEW_PASSWORD_USER_OTHER_COOKIE;
const mustChangeCookie = process.env.REACT_PREVIEW_MUST_CHANGE_COOKIE;
const adminPassword = process.env.REACT_PREVIEW_LOGIN_PASSWORD;
const userPassword = process.env.REACT_PREVIEW_USER_PASSWORD;
const mustChangePassword = process.env.REACT_PREVIEW_MUST_CHANGE_PASSWORD;
const screenshotDir = process.env.REACT_PASSWORD_SCREENSHOT_DIR;

function routeOf(url) {
  const parsed = new URL(url);
  return `${parsed.pathname}${parsed.search}`;
}

function collectFailures(page, scenario, allowed = []) {
  const failures = [];
  page.on('pageerror', error => failures.push(`${scenario} pageerror: ${error.message}`));
  page.on('requestfailed', request => {
    const failure = `${request.method()} ${routeOf(request.url())} ${request.failure()?.errorText || 'unknown'}`;
    if (!allowed.includes(failure)) failures.push(`${scenario} requestfailed: ${failure}`);
  });
  page.on('response', response => {
    if (response.status() >= 400) {
      const failure = `${response.request().method()} ${routeOf(response.url())} ${response.status()}`;
      if (!allowed.includes(failure)) failures.push(`${scenario} response: ${failure}`);
    }
  });
  return failures;
}

function assertClean(failures) {
  assert.deepEqual(failures, []);
}

async function addCookie(context, name, value) {
  assert(value, `${name} fixture is required`);
  await context.addCookies([{ name, value, url: baseUrl }]);
}

async function goto(page, pathname) {
  const response = await page.goto(baseUrl + pathname);
  assert.equal(response.status(), 200);
}

async function formShape(page, selector) {
  return page.locator(selector).evaluate(form => ({
    method: form.getAttribute('method'),
    action: form.getAttribute('action'),
    fields: [...form.querySelectorAll('input[type="password"]')].map(input => ({
      id: input.id,
      name: input.name,
      required: input.required,
      minlength: input.getAttribute('minlength'),
      maxlength: input.getAttribute('maxlength'),
      autocomplete: input.getAttribute('autocomplete'),
      autofocus: input.autofocus,
      placeholder: input.getAttribute('placeholder'),
    })),
  }));
}

async function verifyPrototypeNamedInitialMessages(browser) {
  for (const value of ['__proto__', 'constructor', 'toString']) {
    for (const [realm, pagePath, apiPath, cookieName, cookieValue, formAction, expectedClass] of [
      ['admin', '/__react/admin/settings', '/api/v1/admin/settings', 'sid', adminCookie, '/admin/change-password', 'err'],
      ['user', '/__react/user/change-password', '/api/v1/user/password', 'usid', passwordUserCookie, '/user/change-password', 'err'],
    ]) {
      const context = await browser.newContext({ viewport: { width: 1024, height: 900 } });
      await addCookie(context, cookieName, cookieValue);
      const page = await context.newPage();
      const failures = collectFailures(page, `${realm} prototype query ${value}`);
      const queryValue = realm === 'admin' ? `err:${value}` : value;
      const response = page.waitForResponse(`**${apiPath}`);
      await goto(page, `${pagePath}?msg=${encodeURIComponent(queryValue)}`);
      await response;
      await page.waitForTimeout(50);
      assert.equal(await page.locator(`form[action="${formAction}"]`).count(), 1, `${realm} ${value} keeps the form usable`);
      const feedback = page.locator(`.${expectedClass}`).first();
      assert.equal(await feedback.innerText(), value, `${realm} ${value} renders literally`);
      assertClean(failures);
      await context.close();
    }
  }
}

async function verifyDocumentsAndPreferences(browser) {
  const context = await browser.newContext({ viewport: { width: 1024, height: 900 } });
  await addCookie(context, 'sid', adminCookie);
  const page = await context.newPage();
  const failures = collectFailures(page, 'settings document');
  await goto(page, '/__react/admin/settings#main-content');
  await page.getByText('admin', { exact: true }).waitFor();
  assert.equal(await page.title(), '设置');
  assert.equal(await page.locator('#main-content').evaluate(node => node === document.activeElement), true);
  assert.equal(await page.locator('.sidebar-link[aria-current="page"]').innerText(), '设置');
  assert.equal(await page.locator('.badge').innerText(), 'preview.invalid');
  assert.deepEqual(await formShape(page, 'form[action="/admin/change-password"]'), {
    method: 'post', action: '/admin/change-password', fields: [
      { id: 'settings-current-password', name: 'current', required: true, minlength: null, maxlength: '256', autocomplete: 'current-password', autofocus: false, placeholder: null },
      { id: 'settings-new-password', name: 'new', required: true, minlength: '8', maxlength: '256', autocomplete: 'new-password', autofocus: false, placeholder: null },
      { id: 'settings-confirm-password', name: 'confirm', required: true, minlength: '8', maxlength: '256', autocomplete: 'new-password', autofocus: false, placeholder: null },
    ],
  });
  const motion = page.locator('#sidebar-motion-toggle');
  assert.equal(await motion.isChecked(), false);
  await motion.check();
  assert.equal(await page.evaluate(() => document.documentElement.classList.contains('sidebar-motion-enabled')), true);
  assert.equal(await page.evaluate(() => localStorage.getItem('hy2.sidebar-motion')), 'enabled');
  await page.reload();
  await page.getByText('admin', { exact: true }).waitFor();
  assert.equal(await page.locator('#sidebar-motion-toggle').isChecked(), true);
  await page.locator('#sidebar-motion-toggle').uncheck();
  assert.equal(await page.evaluate(() => document.documentElement.classList.contains('sidebar-motion-enabled')), false);
  assert.equal(await page.evaluate(() => localStorage.getItem('hy2.sidebar-motion')), null);
  assertClean(failures);
  await context.close();

  const emptyNameContext = await browser.newContext({ viewport: { width: 1024, height: 900 } });
  await addCookie(emptyNameContext, 'sid', adminCookie);
  const emptyNamePage = await emptyNameContext.newPage();
  await emptyNamePage.route('**/api/v1/admin/settings', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ username: '', password_min_length: 8, password_max_length: 256 }),
  }));
  await goto(emptyNamePage, '/__react/admin/settings');
  await emptyNamePage.locator('.settings-page code').waitFor();
  assert.equal(await emptyNamePage.locator('.settings-page code').innerText(), '');
  assert.equal(await emptyNamePage.getByText('加载失败：响应数据格式无效', { exact: true }).count(), 0);
  await emptyNameContext.close();

  const denied = await browser.newContext({ viewport: { width: 1024, height: 900 } });
  await addCookie(denied, 'sid', adminCookie);
  await denied.addInitScript(() => {
    for (const method of ['getItem', 'setItem', 'removeItem']) {
      Object.defineProperty(Storage.prototype, method, { configurable: true, value() { throw new Error('storage denied'); } });
    }
  });
  const deniedPage = await denied.newPage();
  const deniedFailures = collectFailures(deniedPage, 'storage denied');
  await goto(deniedPage, '/__react/admin/settings');
  await deniedPage.getByText('admin', { exact: true }).waitFor();
  await deniedPage.locator('#sidebar-motion-toggle').check();
  assert.equal(await deniedPage.evaluate(() => document.documentElement.classList.contains('sidebar-motion-enabled')), true);
  await deniedPage.locator('#sidebar-motion-toggle').uncheck();
  assert.equal(await deniedPage.evaluate(() => document.documentElement.classList.contains('sidebar-motion-enabled')), false);
  assertClean(deniedFailures);
  await denied.close();

  const userContext = await browser.newContext({ viewport: { width: 1024, height: 900 } });
  await addCookie(userContext, 'usid', passwordUserCookie);
  const userPage = await userContext.newPage();
  const userFailures = collectFailures(userPage, 'user password document');
  await goto(userPage, '/__react/user/change-password#main-content');
  await userPage.getByText('demo_alex · preview.invalid', { exact: true }).waitFor();
  assert.equal(await userPage.title(), '修改面板密码');
  assert.equal(await userPage.locator('#main-content').evaluate(node => node === document.activeElement), true);
  assert.equal(await userPage.locator('.auth-header-back').getAttribute('href'), '/user/panel');
  assert.equal(await userPage.locator('.auth-back').getAttribute('href'), '/user/panel');
  assert.deepEqual(await formShape(userPage, 'form[action="/user/change-password"]'), {
    method: 'post', action: '/user/change-password', fields: [
      { id: 'user-current-password', name: 'current', required: true, minlength: null, maxlength: '256', autocomplete: 'current-password', autofocus: true, placeholder: '输入当前密码' },
      { id: 'user-new-password', name: 'new', required: true, minlength: '8', maxlength: '256', autocomplete: 'new-password', autofocus: false, placeholder: '输入新密码' },
      { id: 'user-confirm-password', name: 'confirm', required: true, minlength: '8', maxlength: '256', autocomplete: 'new-password', autofocus: false, placeholder: '再次输入新密码' },
    ],
  });
  assertClean(userFailures);
  await userContext.close();
}

async function verifyHeldMutationAndResponseValidation(browser) {
  const context = await browser.newContext({ viewport: { width: 1024, height: 900 } });
  await addCookie(context, 'usid', passwordUserCookie);
  const page = await context.newPage();
  const failures = collectFailures(page, 'held mutation');
  let held;
  let posts = 0;
  await page.route('**/api/v1/user/change-password', route => {
    posts += 1;
    held = route;
  });
  await goto(page, '/__react/user/change-password');
  await page.getByText('demo_alex · preview.invalid', { exact: true }).waitFor();
  const values = ['fixture-old', 'new-password-a', 'new-password-a'];
  for (const [name, value] of ['current', 'new', 'confirm'].map((name, index) => [name, values[index]])) {
    await page.locator(`[name="${name}"]`).fill(value);
  }
  await page.locator('form[action="/user/change-password"]').evaluate(form => {
    form.requestSubmit(); form.requestSubmit();
  });
  await page.getByRole('button', { name: '正在保存…' }).waitFor();
  assert.equal(posts, 1, 'immediate duplicate submit must issue one POST');
  assert.equal(await page.locator('[name="new"]').isEditable(), true);
  await page.locator('[name="new"]').fill('newer-edit-password');
  await held.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: false, code: 'new password mismatch' }) });
  await page.getByRole('alert').waitFor();
  assert.equal(await page.getByRole('alert').innerText(), '两次输入的新密码不一致');
  assert.equal(await page.locator('[name="current"]').inputValue(), '');
  assert.equal(await page.locator('[name="new"]').inputValue(), 'newer-edit-password');
  assert.equal(await page.locator('[name="confirm"]').inputValue(), '');
  assert.equal(await page.getByRole('button', { name: '保存新密码' }).isEnabled(), true);
  assertClean(failures);

  const invalidReplies = [
    { status: 503, body: { error: 'state_unavailable' } },
    { status: 401, body: { error: 'forbidden' } },
    { status: 403, body: { error: 'login_required' } },
    { status: 200, body: { ok: false, code: '<img src=x onerror=alert(1)>' } },
    { status: 200, body: { ok: false, code: 'new password short', extra: true } },
    { status: 200, raw: '{bad' },
  ];
  await page.unroute('**/api/v1/user/change-password');
  let attempts = 0;
  await page.route('**/api/v1/user/change-password', route => {
    const reply = invalidReplies[attempts++];
    route.fulfill({ status: reply.status, contentType: 'application/json', body: reply.raw || JSON.stringify(reply.body) });
  });
  for (const reply of invalidReplies) {
    await page.locator('[name="current"]').fill('keep-current');
    await page.locator('[name="new"]').fill('keep-new-password');
    await page.locator('[name="confirm"]').fill('keep-new-password');
    await page.getByRole('button', { name: '保存新密码' }).click();
    await page.getByText('修改结果未确认，请检查网络或重新登录后再试。', { exact: true }).waitFor();
    assert.equal(await page.locator('[name="current"]').inputValue(), 'keep-current');
    assert.equal(await page.locator('[name="new"]').inputValue(), 'keep-new-password');
    assert.equal(await page.locator('[name="confirm"]').inputValue(), 'keep-new-password');
  }
  assert.equal(attempts, invalidReplies.length, 'transport uncertainty never retries automatically');
  assert.equal(await page.locator('img').count(), 0, 'unsafe server text is never rendered');
  await context.close();
}

async function verifyReadFailuresAndAccessNavigation(browser) {
  const loadingContext = await browser.newContext({ viewport: { width: 1024, height: 900 } });
  await addCookie(loadingContext, 'sid', adminCookie);
  const loadingPage = await loadingContext.newPage();
  await loadingPage.clock.install();
  let delayed;
  let mode = 'delayed';
  await loadingPage.route('**/api/v1/admin/settings', route => {
    if (mode === 'delayed') delayed = route;
    else route.continue();
  });
  await goto(loadingPage, '/__react/admin/settings');
  await loadingPage.getByRole('status', { name: '正在加载设置…' }).waitFor();
  assert.equal(await loadingPage.getByText('正在加载设置…', { exact: true }).count(), 0, 'loading state has no visible text node');
  assert.equal(await loadingPage.locator('form[action="/admin/change-password"]').count(), 0, 'loading state has no guessed-limit form');
  await loadingPage.clock.fastForward(10_000);
  await loadingPage.getByText('加载失败：请求超时，请重试', { exact: true }).waitFor();
  mode = 'real';
  await loadingPage.getByRole('button', { name: '重试' }).click();
  await loadingPage.getByText('admin', { exact: true }).waitFor();
  if (delayed) await delayed.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ username: 'stale-admin', password_min_length: 8, password_max_length: 256 }) }).catch(() => {});
  assert.equal(await loadingPage.getByText('stale-admin', { exact: true }).count(), 0, 'timed-out stale read cannot overwrite retry');
  await loadingContext.close();

  for (const [code, destination] of [['forbidden', '/login'], ['disabled', '/user/panel'], ['expired', '/user/panel']]) {
    const context = await browser.newContext({ viewport: { width: 1024, height: 900 } });
    await addCookie(context, 'usid', passwordUserCookie);
    const page = await context.newPage();
    await page.route('**/api/v1/user/password', route => route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ error: code }) }));
    await goto(page, '/__react/user/change-password');
    await page.waitForURL(`**${destination}`);
    await context.close();
  }

  const anonymous = await browser.newPage({ viewport: { width: 1024, height: 900 } });
  await goto(anonymous, '/__react/admin/settings');
  await anonymous.waitForURL('**/login');
  await anonymous.close();

  const unknownContext = await browser.newContext({ viewport: { width: 1024, height: 900 } });
  await addCookie(unknownContext, 'usid', passwordUserCookie);
  const unknownPage = await unknownContext.newPage();
  let unknown = true;
  await unknownPage.route('**/api/v1/user/password', route => {
    if (unknown) route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ error: '<script>alert(1)</script>' }) });
    else route.continue();
  });
  await goto(unknownPage, '/__react/user/change-password');
  await unknownPage.getByText('加载失败：HTTP 403', { exact: true }).waitFor();
  assert.equal(await unknownPage.locator('script').evaluateAll(nodes => nodes.some(node => node.textContent.includes('alert(1)'))), false);
  unknown = false;
  await unknownPage.getByRole('button', { name: '重试' }).click();
  await unknownPage.getByText('demo_alex · preview.invalid', { exact: true }).waitFor();
  await unknownContext.close();
}

async function verifyMutationTimeoutNetworkAndStaleCallbacks(browser) {
  const networkContext = await browser.newContext({ viewport: { width: 1024, height: 900 } });
  await addCookie(networkContext, 'usid', passwordUserCookie);
  const networkPage = await networkContext.newPage();
  await networkPage.route('**/api/v1/user/change-password', route => route.abort());
  await goto(networkPage, '/__react/user/change-password');
  await networkPage.getByText('demo_alex · preview.invalid', { exact: true }).waitFor();
  for (const [name, value] of [['current', 'keep-current'], ['new', 'keep-network-password'], ['confirm', 'keep-network-password']]) await networkPage.locator(`[name="${name}"]`).fill(value);
  await networkPage.getByRole('button', { name: '保存新密码' }).click();
  await networkPage.getByText('修改结果未确认，请检查网络或重新登录后再试。', { exact: true }).waitFor();
  assert.equal(await networkPage.locator('[name="new"]').inputValue(), 'keep-network-password');
  await networkContext.close();

  const timeoutContext = await browser.newContext({ viewport: { width: 1024, height: 900 } });
  await addCookie(timeoutContext, 'usid', passwordUserCookie);
  const timeoutPage = await timeoutContext.newPage();
  await timeoutPage.clock.install();
  let held;
  await timeoutPage.route('**/api/v1/user/change-password', route => { held = route; });
  await goto(timeoutPage, '/__react/user/change-password');
  await timeoutPage.getByText('demo_alex · preview.invalid', { exact: true }).waitFor();
  for (const [name, value] of [['current', 'keep-current'], ['new', 'keep-timeout-password'], ['confirm', 'keep-timeout-password']]) await timeoutPage.locator(`[name="${name}"]`).fill(value);
  await timeoutPage.getByRole('button', { name: '保存新密码' }).click();
  await timeoutPage.clock.fastForward(15_000);
  await timeoutPage.getByText('修改结果未确认，请检查网络或重新登录后再试。', { exact: true }).waitFor();
  assert.equal(await timeoutPage.locator('[name="new"]').inputValue(), 'keep-timeout-password');
  assert.equal(await timeoutPage.getByRole('button', { name: '保存新密码' }).isEnabled(), true);
  if (held) await held.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: false, code: 'new password short' }) }).catch(() => {});
  assert.equal(await timeoutPage.getByText('新密码至少需要 8 位', { exact: true }).count(), 0, 'late timeout callback is ignored');
  await timeoutContext.close();

  const expiryContext = await browser.newContext({ viewport: { width: 1024, height: 900 } });
  await addCookie(expiryContext, 'usid', passwordUserCookie);
  const expiryPage = await expiryContext.newPage();
  await goto(expiryPage, '/__react/user/change-password');
  await expiryPage.getByText('demo_alex · preview.invalid', { exact: true }).waitFor();
  await expiryPage.route('**/api/v1/user/change-password', route => route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ error: 'expired' }) }));
  for (const [name, value] of [['current', 'current-password'], ['new', 'new-expired-password'], ['confirm', 'new-expired-password']]) await expiryPage.locator(`[name="${name}"]`).fill(value);
  await expiryPage.getByRole('button', { name: '保存新密码' }).click();
  await expiryPage.waitForURL('**/user/panel');
  await expiryContext.close();

  const staleContext = await browser.newContext({ viewport: { width: 1024, height: 900 } });
  await addCookie(staleContext, 'usid', passwordUserCookie);
  const stalePage = await staleContext.newPage();
  let staleRoute;
  await stalePage.route('**/api/v1/user/change-password', route => { staleRoute = route; });
  await goto(stalePage, '/__react/user/change-password');
  await stalePage.getByText('demo_alex · preview.invalid', { exact: true }).waitFor();
  for (const [name, value] of [['current', 'stale-current'], ['new', 'stale-new-password'], ['confirm', 'stale-new-password']]) await stalePage.locator(`[name="${name}"]`).fill(value);
  await stalePage.getByRole('button', { name: '保存新密码' }).click();
  await stalePage.evaluate(() => {
    window.dispatchEvent(new PageTransitionEvent('pagehide'));
    window.dispatchEvent(new PageTransitionEvent('pageshow'));
  });
  await stalePage.getByRole('button', { name: '保存新密码' }).waitFor();
  if (staleRoute) await staleRoute.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: false, code: 'new password short' }) }).catch(() => {});
  assert.equal(await stalePage.getByText('新密码至少需要 8 位', { exact: true }).count(), 0, 'pre-transition callback is ignored');
  assert.equal(await stalePage.locator('[name="new"]').inputValue(), 'stale-new-password');
  await staleContext.close();
}

async function verifyAdminValidationAndStrictAccessCodes(browser) {
  const context = await browser.newContext({ viewport: { width: 1024, height: 900 } });
  await addCookie(context, 'sid', adminCookie);
  const page = await context.newPage();
  let response = { status: 200, body: { ok: false, code: 'password_long' } };
  await page.route('**/api/v1/admin/change-password', route => route.fulfill({ status: response.status, contentType: 'application/json', body: JSON.stringify(response.body) }));
  await goto(page, '/__react/admin/settings?msg=err:%3Cimg%20src=x%20onerror=alert(1)%3E');
  await page.getByText('admin', { exact: true }).waitFor();
  assert.equal(await page.getByText('<img src=x onerror=alert(1)>', { exact: true }).count(), 1);
  assert.equal(await page.locator('img').count(), 0, 'unknown query text is escaped by React');
  for (const [name, value] of [['current', 'wrong-current'], ['new', 'too-long-password'], ['confirm', 'too-long-password']]) await page.locator(`[name="${name}"]`).fill(value);
  await page.getByRole('button', { name: '更新密码' }).click();
  await page.getByText('密码不能超过 256 位', { exact: true }).waitFor();
  response = { status: 403, body: { error: 'disabled' } };
  for (const [name, value] of [['current', 'keep-current'], ['new', 'keep-admin-password'], ['confirm', 'keep-admin-password']]) await page.locator(`[name="${name}"]`).fill(value);
  await page.getByRole('button', { name: '更新密码' }).click();
  await page.getByText('修改结果未确认，请检查网络或重新登录后再试。', { exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, '/__react/admin/settings', 'user-only lifecycle code cannot navigate admin');
  assert.equal(await page.locator('[name="new"]').inputValue(), 'keep-admin-password');
  await context.close();
}

async function verifyRealPasswordChanges(browser) {
  assert(adminPassword && userPassword && mustChangePassword);
  const adminContext = await browser.newContext({ viewport: { width: 1024, height: 900 } });
  await addCookie(adminContext, 'sid', adminCookie);
  const adminPage = await adminContext.newPage();
  const adminRequests = [];
  adminPage.on('request', request => adminRequests.push(request.url()));
  await goto(adminPage, '/__react/admin/settings');
  await adminPage.getByText('admin', { exact: true }).waitFor();
  await adminPage.locator('[name="current"]').fill(adminPassword);
  await adminPage.locator('[name="new"]').fill('changed-admin-password');
  await adminPage.locator('[name="confirm"]').fill('changed-admin-password');
  await adminPage.getByRole('button', { name: '更新密码' }).click();
  await adminPage.waitForURL('**/admin/settings?msg=password+changed');
  assert(adminRequests.every(url => !url.includes(adminPassword) && !url.includes('changed-admin-password')));
  const oldAdmin = await adminContext.request.get(`${baseUrl}/api/v1/admin/settings`, { headers: { Cookie: `sid=${adminOtherCookie}` } });
  assert.equal(oldAdmin.status(), 401, 'other old admin session is invalidated');
  const oldLogin = await adminContext.request.post(`${baseUrl}/api/v1/login`, { form: { admin_username: 'admin', admin_password: adminPassword } });
  assert.equal((await oldLogin.json()).ok, false);
  const newLogin = await adminContext.request.post(`${baseUrl}/api/v1/login`, { form: { admin_username: 'admin', admin_password: 'changed-admin-password' } });
  assert.equal((await newLogin.json()).redirect_to, '/admin?msg=login+success');
  const replacementAdminCookie = newLogin.headers()['set-cookie'].split(';', 1)[0];
  await adminContext.close();

  const userContext = await browser.newContext({ viewport: { width: 1024, height: 900 } });
  await addCookie(userContext, 'usid', passwordUserCookie);
  const userPage = await userContext.newPage();
  const userRequests = [];
  userPage.on('request', request => userRequests.push(request.url()));
  await goto(userPage, '/__react/user/change-password');
  await userPage.getByText('demo_alex · preview.invalid', { exact: true }).waitFor();
  await userPage.locator('[name="current"]').fill(userPassword);
  await userPage.locator('[name="new"]').fill('changed-user-password');
  await userPage.locator('[name="confirm"]').fill('changed-user-password');
  await userPage.getByRole('button', { name: '保存新密码' }).click();
  await userPage.waitForURL('**/user/panel');
  assert(userRequests.every(url => !url.includes(userPassword) && !url.includes('changed-user-password')));
  assert.equal(await userPage.evaluate(() => Object.values(localStorage).some(value => value.includes('password'))), false);
  const oldUser = await userContext.request.get(`${baseUrl}/api/v1/user/password`, { headers: { Cookie: `usid=${passwordUserOtherCookie}` } });
  assert.equal(oldUser.status(), 401, 'other old password session is invalidated');
  const oldUserLogin = await userContext.request.post(`${baseUrl}/api/v1/login`, { form: { user_username: 'demo_alex', user_password: userPassword } });
  assert.equal((await oldUserLogin.json()).ok, false);
  const newUserLogin = await userContext.request.post(`${baseUrl}/api/v1/login`, { form: { user_username: 'demo_alex', user_password: 'changed-user-password' } });
  assert.equal((await newUserLogin.json()).redirect_to, '/user/panel');
  const otherUser = await userContext.request.get(`${baseUrl}/api/v1/user/password`, { headers: { Cookie: `usid=${mustChangeCookie}` } });
  assert.equal(otherUser.status(), 200, 'another user session is unaffected');
  const adminSession = await userContext.request.get(`${baseUrl}/api/v1/admin/settings`, { headers: { Cookie: replacementAdminCookie } });
  assert.equal(adminSession.status(), 200, 'user password change leaves admin realm unaffected');
  await userContext.close();

  const mustContext = await browser.newContext({ viewport: { width: 1024, height: 900 } });
  await addCookie(mustContext, 'usid', mustChangeCookie);
  const mustPage = await mustContext.newPage();
  await goto(mustPage, '/__react/user/change-password');
  await mustPage.getByText('must_change · preview.invalid', { exact: true }).waitFor();
  await mustPage.locator('[name="current"]').fill(mustChangePassword);
  await mustPage.locator('[name="new"]').fill('changed-required-password');
  await mustPage.locator('[name="confirm"]').fill('changed-required-password');
  await mustPage.getByRole('button', { name: '保存新密码' }).click();
  await mustPage.waitForURL('**/user/panel');
  const mustLogin = await mustContext.request.post(`${baseUrl}/api/v1/login`, { form: { user_username: 'must_change', user_password: 'changed-required-password' } });
  assert.equal((await mustLogin.json()).redirect_to, '/user/panel');
  await mustContext.close();
}

async function snapshot(page, kind) {
  const root = kind === 'settings' ? '.settings-page' : '.auth-scene';
  return {
    text: (await page.locator(root).innerText()).replace(/\s+/g, ' ').trim(),
    form: await formShape(page, kind === 'settings' ? 'form[action="/admin/change-password"]' : 'form[action="/user/change-password"]'),
    links: await page.locator('a').evaluateAll(links => links.map(link => [link.textContent.trim(), link.getAttribute('href')])),
  };
}

async function verifyVisualParity(browser) {
  if (screenshotDir) fs.mkdirSync(screenshotDir, { recursive: true });
  for (const [kind, reactPath, legacyPath, cookieName, cookieValue, root] of [
    ['settings', '/__react/admin/settings', '/admin/settings', 'sid', adminCookie, '.settings-page'],
    ['user-password', '/__react/user/change-password', '/user/change-password', 'usid', passwordUserCookie, '.auth-scene'],
  ]) {
    for (const width of [1920, 1024, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 1080 }, reducedMotion: 'reduce' });
      await addCookie(context, cookieName, cookieValue);
      const reactPage = await context.newPage();
      await goto(reactPage, reactPath);
      await reactPage.locator(root).waitFor();
      const legacyPage = await context.newPage();
      const response = await legacyPage.goto(baseUrl + legacyPath);
      assert.equal(response.status(), 200);
      await legacyPage.locator(root).waitFor();
      assert.deepEqual(await snapshot(reactPage, kind === 'settings' ? 'settings' : 'user'), await snapshot(legacyPage, kind === 'settings' ? 'settings' : 'user'));
      for (const selector of kind === 'settings' ? ['.sidebar', '.topbar', '.content', root] : ['.auth-header', root, '.auth-card']) {
        const actual = await reactPage.locator(selector).boundingBox();
        const expected = await legacyPage.locator(selector).boundingBox();
        assert(actual && expected);
        for (const key of ['x', 'y', 'width', 'height']) assert(Math.abs(actual[key] - expected[key]) <= 2, `${kind} ${selector} ${key} parity at ${width}`);
      }
      assert.equal(await reactPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.equal(await legacyPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      if (screenshotDir) {
        await reactPage.screenshot({ path: path.join(screenshotDir, `react-${kind}-${width}.png`), fullPage: true });
        await legacyPage.screenshot({ path: path.join(screenshotDir, `legacy-${kind}-${width}.png`), fullPage: true });
      }
      await context.close();
    }
  }
}

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    await verifyPrototypeNamedInitialMessages(browser);
    await verifyDocumentsAndPreferences(browser);
    await verifyHeldMutationAndResponseValidation(browser);
    await verifyReadFailuresAndAccessNavigation(browser);
    await verifyMutationTimeoutNetworkAndStaleCallbacks(browser);
    await verifyAdminValidationAndStrictAccessCodes(browser);
    await verifyVisualParity(browser);
    await verifyRealPasswordChanges(browser);
    console.log('PASS: React password pages reads, forms, lifecycle, mutations, isolation, and visual parity');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
