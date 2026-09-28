const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const baseUrl = process.env.PREVIEW_BASE_URL || 'http://127.0.0.1:18764';
const adminCookie = process.env.REACT_PREVIEW_ADMIN_COOKIE || 'missing-admin-session';
const userCookie = process.env.REACT_PREVIEW_USER_COOKIE || 'missing-user-session';
const screenshotDir = process.env.REACT_SCREENSHOT_DIR;

const columns = ['时间', '操作人', 'IP', '操作', '目标', '日期', '流量变化'];
const navigation = [
  ['工作台', null],
  ['用户', '/admin'],
  ['今日计划', '/admin/plans'],
  ['AI 工具', null],
  ['AI 对话', '/admin/chat'],
  ['AI 视频', '/admin/video'],
  ['网络管理', null],
  ['流量分析', '/admin/usage'],
  ['模板与路由', '/admin/config'],
  ['家宽出口', '/admin/landing-egresses'],
  ['运维管理', null],
  ['运维', '/admin/health'],
  ['服务接入', null],
  ['服务中心', '/admin/services'],
  ['设置', '/admin/settings'],
];

function routeOf(url) {
  const parsed = new URL(url);
  return `${parsed.pathname}${parsed.search}`;
}

function collectFailures(page, scenario, { allowedResponses = [], allowedFailures = [] } = {}) {
  const failures = [];
  page.on('pageerror', error => failures.push(`${scenario} pageerror: ${error.message}`));
  page.on('requestfailed', request => {
    const failure = `${request.method()} ${routeOf(request.url())} ${request.failure()?.errorText || 'unknown network failure'}`;
    if (!allowedFailures.includes(failure)) failures.push(`${scenario} requestfailed: ${failure}`);
  });
  page.on('response', response => {
    if (response.status() < 400) return;
    const failure = `${response.request().method()} ${routeOf(response.url())} ${response.status()}`;
    if (!allowedResponses.includes(failure)) failures.push(`${scenario} response: ${failure}`);
  });
  return failures;
}

function assertClean(failures) {
  assert.deepEqual(failures, [], 'scenario must have no unexpected errors or failed requests');
}

async function addCookie(context, name, value) {
  await context.addCookies([{ name, value, url: baseUrl }]);
}

async function gotoReact(page) {
  const response = await page.goto(`${baseUrl}/__react/admin/logs`);
  assert.equal(response.status(), 200, 'controlled React entry must be available');
}

async function snapshot(page) {
  return {
    title: await page.locator('.page-title').innerText(),
    heading: await page.locator('.admin-section-title').innerText(),
    columns: await page.locator('.data-table th').allTextContents(),
    cells: await page.locator('.data-table tbody tr').first().locator('td').allTextContents(),
    links: await page.locator('.sidebar-link').evaluateAll(links => links.map(link => ({
      text: link.textContent.trim(),
      href: new URL(link.href).pathname,
    }))),
  };
}

async function verifyAuthenticatedLogs(browser) {
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  await addCookie(context, 'sid', adminCookie);
  const page = await context.newPage();
  const failures = collectFailures(page, 'authenticated logs');
  await page.route('**/api/v1/admin/logs', async route => {
    await new Promise(resolve => setTimeout(resolve, 200));
    await route.continue();
  });
  await gotoReact(page);
  await page.getByText('preview-admin', { exact: true }).waitFor();

  assert.equal(await page.title(), '运维');
  assert.equal(await page.locator('.page-title').innerText(), '运维');
  assert.equal(await page.locator('.badge').innerText(), 'preview.invalid');
  assert.equal(await page.locator('.app').count(), 1, 'the page must have one shell frame');
  assert.equal(await page.locator('.sidebar').count(), 1, 'the page must have one sidebar');
  assert.equal(await page.locator('.main').count(), 1, 'the page must have one main region');
  assert.deepEqual(await page.locator('.sidebar-section').allTextContents(), navigation.filter(([, href]) => !href).map(([label]) => label));
  assert.deepEqual(await page.locator('.sidebar-link').evaluateAll(links => links.map(link => [link.textContent.trim(), new URL(link.href).pathname])), navigation.filter(([, href]) => href));
  assert.equal(await page.locator('.sidebar-link[aria-current="page"]').count(), 1);
  assert.equal(await page.locator('.sidebar-link[aria-current="page"]').innerText(), '运维');
  assert.deepEqual(await page.locator('.data-table th').allTextContents(), columns);
  assert.deepEqual(await page.locator('.data-table tbody tr').first().locator('td').allTextContents(), [
    '2026-07-18T12:00:00+08:00',
    'preview-admin',
    '192.0.2.10',
    'reset_user',
    'demo_alex',
    '2026.7.18',
    '1.00 GB → 0.00 B',
  ]);
  const stylesheet = await page.locator('link[rel="stylesheet"]').getAttribute('href');
  assert.match(stylesheet, /^\/static\/react\/assets\/[^/]+\.css$/);
  assert(!stylesheet.includes('style.css'), 'React must not load the legacy stylesheet');
  assert.equal(await page.locator('script[src*="shell.js"], script[src*="shell-preferences.js"]').count(), 0);
  assert.equal(await page.locator('form[action="/logout"][method="post"]').count(), 1);

  for (const width of [1920, 1024, 390]) {
    await page.setViewportSize({ width, height: 1080 });
    await gotoReact(page);
    await page.getByText('preview-admin', { exact: true }).waitFor();
    const current = await snapshot(page);
    assert.equal(current.title, '运维');
    for (const selector of ['.sidebar', '.topbar', '.content', '.admin-section']) {
      const box = await page.locator(selector).boundingBox();
      assert(box && box.width > 0 && box.height > 0, `${selector} must have visible bounds at ${width}px`);
    }
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `no page overflow at ${width}px`);
    if (screenshotDir) {
      fs.mkdirSync(screenshotDir, { recursive: true });
      await page.screenshot({ path: path.join(screenshotDir, `react-logs-${width}.png`), fullPage: true });
    }
  }

  assertClean(failures);
  await context.close();
}

async function verifyAuthenticationBoundaries(browser) {
  const anonymous = await browser.newPage({ viewport: { width: 1024, height: 768 } });
  const anonymousFailures = collectFailures(anonymous, 'anonymous logs', {
    allowedResponses: ['GET /api/v1/session 401'],
  });
  await gotoReact(anonymous);
  await anonymous.getByRole('dialog', { name: '登录控制台' }).waitFor();
  assert.equal(await anonymous.locator('.app').count(), 1, 'anonymous admin routes remain inside the shared workbench shell');
  assert.equal(await anonymous.locator('.sidebar').count(), 1, 'anonymous admin routes retain the shared workbench sidebar');
  assert.equal(await anonymous.locator('.main').count(), 1, 'anonymous admin routes retain the shared workbench main region');
  assert.equal(await anonymous.locator('text=preview-admin').count(), 0);
  assert.equal(await anonymous.locator('.data-table').count(), 0);
  assertClean(anonymousFailures);
  await anonymous.close();

  const userContext = await browser.newContext({ viewport: { width: 1024, height: 768 } });
  await addCookie(userContext, 'usid', userCookie);
  const userPage = await userContext.newPage();
  const userFailures = collectFailures(userPage, 'user-session logs');
  await gotoReact(userPage);
  await userPage.getByRole('dialog', { name: '登录控制台' }).waitFor();
  assert.equal(await userPage.locator('.app').count(), 1, 'a non-admin session remains inside the shared workbench shell');
  assert.equal(await userPage.locator('text=preview-admin').count(), 0);
  assert.equal(await userPage.locator('.data-table').count(), 0);
  assertClean(userFailures);
  await userContext.close();
}

async function verifyEmptyAndRecovery(browser) {
  const context = await browser.newContext({ viewport: { width: 1024, height: 768 } });
  await addCookie(context, 'sid', adminCookie);
  const page = await context.newPage();
  const failures = collectFailures(page, 'logs recovery', {
    allowedResponses: ['GET /api/v1/admin/logs 503'],
    allowedFailures: ['GET /api/v1/admin/logs net::ERR_ABORTED'],
  });
  let responseMode = 'empty';
  let delayedRequest;
  await page.route('**/api/v1/admin/logs', async route => {
    if (responseMode === 'empty') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ limit: 300, rows: [] }) });
    } else if (responseMode === 'http-error') {
      await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'state_unavailable' }) });
    } else if (responseMode === 'invalid') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ limit: 300, rows: [{ time: 'missing fields' }] }) });
    } else if (responseMode === 'malformed') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{invalid' });
    } else if (responseMode === 'delayed') {
      delayedRequest = route;
    } else {
      await route.continue();
    }
  });
  await gotoReact(page);
  await page.getByText('暂无日志记录', { exact: true }).waitFor();

  responseMode = 'http-error';
  await page.reload();
  await page.getByRole('alert').waitFor();
  assert.match(await page.getByRole('alert').innerText(), /HTTP 503/);
  assert.equal(await page.getByText('暂无日志记录', { exact: true }).count(), 0, 'HTTP failure is not empty data');

  responseMode = 'invalid';
  await page.reload();
  await page.getByRole('alert').waitFor();
  assert.match(await page.getByRole('alert').innerText(), /响应数据格式无效/);
  assert.equal(await page.getByText('暂无日志记录', { exact: true }).count(), 0, 'schema failure is not empty data');

  responseMode = 'malformed';
  await page.reload();
  await page.getByRole('alert').waitFor();
  assert.match(await page.getByRole('alert').innerText(), /响应不是有效的 JSON/);
  assert.equal(await page.getByText('暂无日志记录', { exact: true }).count(), 0, 'malformed JSON is not empty data');

  responseMode = 'real';
  await page.getByRole('button', { name: '重试' }).click();
  await page.getByText('preview-admin', { exact: true }).waitFor();

  responseMode = 'delayed';
  await page.clock.install();
  const delayedStarted = page.waitForRequest('**/api/v1/admin/logs');
  await page.reload();
  await delayedStarted;
  await page.clock.fastForward(10_000);
  await page.getByRole('alert').waitFor({ timeout: 5000 });
  assert.match(await page.getByRole('alert').innerText(), /超时/);
  responseMode = 'real';
  await page.getByRole('button', { name: '重试' }).click();
  await page.getByText('preview-admin', { exact: true }).waitFor();
  if (delayedRequest) {
    await delayedRequest.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ limit: 300, rows: [{ time: 'STALE', actor: 'stale', ip: '', action: '', target: '', month: '', detail: '' }] }),
    }).catch(() => {});
  }
  assert.equal(await page.getByText('stale', { exact: true }).count(), 0, 'aborted stale response cannot overwrite retry');
  assertClean(failures);
  await context.close();
}

async function verifyShellKeyboardAndPreferences(browser) {
  const desktop = await browser.newContext({ viewport: { width: 1024, height: 768 } });
  await addCookie(desktop, 'sid', adminCookie);
  await desktop.addInitScript(() => {
    localStorage.setItem('hy2.sidebar', 'collapsed');
    localStorage.setItem('hy2.sidebar-motion', 'enabled');
  });
  const desktopPage = await desktop.newPage();
  const desktopFailures = collectFailures(desktopPage, 'desktop shell keyboard');
  await gotoReact(desktopPage);
  assert(await desktopPage.locator('.app').evaluate(node => node.classList.contains('sidebar-collapsed')));
  assert(await desktopPage.locator('html').evaluate(node => node.classList.contains('sidebar-motion-enabled')));
  assert.equal(await desktopPage.locator('#sidebar-collapse').getAttribute('aria-pressed'), 'true');
  await desktopPage.locator('#sidebar-collapse').click();
  assert.equal(await desktopPage.evaluate(() => localStorage.getItem('hy2.sidebar')), 'expanded');
  assertClean(desktopFailures);
  await desktop.close();

  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
  await addCookie(mobile, 'sid', adminCookie);
  const mobilePage = await mobile.newPage();
  const mobileFailures = collectFailures(mobilePage, 'mobile shell keyboard');
  await gotoReact(mobilePage);
  const toggle = mobilePage.locator('#sidebar-toggle');
  await toggle.click();
  assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
  assert.equal(await mobilePage.locator('.main').getAttribute('inert'), '');
  assert.equal(await mobilePage.locator('.skip-link').getAttribute('inert'), '');
  assert.equal(await mobilePage.evaluate(() => document.activeElement?.id), 'sidebar-close');
  await mobilePage.locator('#sidebar-close').click();
  await mobilePage.waitForFunction(() => document.activeElement?.id === 'sidebar-toggle');
  await toggle.click();
  await mobilePage.locator('#scrim').click({ position: { x: 380, y: 400 } });
  await mobilePage.waitForFunction(() => document.activeElement?.id === 'sidebar-toggle');
  await toggle.click();
  await mobilePage.waitForFunction(() => document.activeElement?.id === 'sidebar-close');
  await mobilePage.keyboard.press('Shift+Tab');
  assert.equal(await mobilePage.evaluate(() => document.activeElement?.getAttribute('aria-label')), '退出登录');
  await mobilePage.keyboard.press('Tab');
  assert.equal(await mobilePage.evaluate(() => document.activeElement?.id), 'sidebar-close');
  await mobilePage.keyboard.press('Escape');
  assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
  await mobilePage.waitForFunction(() => document.activeElement?.id === 'sidebar-toggle');
  assert.equal(await mobilePage.evaluate(() => document.activeElement?.id), 'sidebar-toggle');
  assert.equal(await mobilePage.locator('#sidebar').getAttribute('inert'), '');
  assert.equal(await mobilePage.locator('.main').getAttribute('inert'), null);
  assert.equal(await mobilePage.locator('.app').evaluate(node => getComputedStyle(node).transitionDuration), '1e-05s');
  await mobilePage.setViewportSize({ width: 880, height: 844 });
  assert.equal(await mobilePage.locator('#sidebar').getAttribute('inert'), '');
  await mobilePage.setViewportSize({ width: 881, height: 844 });
  await mobilePage.waitForFunction(() => !document.querySelector('#sidebar').hasAttribute('inert'));
  assert.equal(await mobilePage.locator('#sidebar').getAttribute('inert'), null);
  assertClean(mobileFailures);
  await mobile.close();
}

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    await verifyAuthenticatedLogs(browser);
    await verifyAuthenticationBoundaries(browser);
    await verifyEmptyAndRecovery(browser);
    await verifyShellKeyboardAndPreferences(browser);
    console.log('PASS: React logs real API, auth boundaries, recovery, shell parity, mobile focus, and preferences');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
