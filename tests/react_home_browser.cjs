const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const baseUrl = process.env.PREVIEW_BASE_URL || 'http://127.0.0.1:18765';
const screenshotDir = process.env.REACT_HOME_SCREENSHOT_DIR;
const demos = ['traffic', 'users', 'health'];

function routeOf(url) {
  const parsed = new URL(url);
  return `${parsed.pathname}${parsed.search}`;
}

function collectFailures(page, scenario, { allowedResponses = [], allowedFailures = [] } = {}) {
  const failures = [];
  const apiRequests = [];
  page.on('pageerror', error => failures.push(`${scenario} pageerror: ${error.message}`));
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/')) apiRequests.push(routeOf(request.url()));
  });
  page.on('requestfailed', request => {
    const failure = `${request.method()} ${routeOf(request.url())} ${request.failure()?.errorText || 'unknown network failure'}`;
    if (!allowedFailures.includes(failure)) failures.push(`${scenario} requestfailed: ${failure}`);
  });
  page.on('response', response => {
    if (response.status() < 400) return;
    const failure = `${response.request().method()} ${routeOf(response.url())} ${response.status()}`;
    if (!allowedResponses.includes(failure)) failures.push(`${scenario} response: ${failure}`);
  });
  return { failures, apiRequests };
}

function assertClean(collection) {
  assert.deepEqual(collection.failures, [], 'page must have no unexpected errors or failed requests');
  assert.deepEqual(collection.apiRequests, [], 'public home must not request private APIs');
}

async function gotoHome(page, hash = '') {
  const response = await page.goto(`${baseUrl}/__react/${hash}`);
  assert.equal(response.status(), 200, 'controlled React home entry must be available');
  await page.locator('.site-main').waitFor();
}

async function assertSelected(page, demo) {
  for (const candidate of demos) {
    const selected = candidate === demo;
    assert.equal(await page.locator(`#demo-tab-${candidate}`).getAttribute('aria-selected'), String(selected));
    assert.equal(await page.locator(`#demo-tab-${candidate}`).getAttribute('tabindex'), selected ? '0' : '-1');
    assert.equal(await page.locator(`#demo-${candidate}`).isVisible(), selected);
  }
  assert.equal(await page.locator('.site-demo-panel:visible').count(), 1);
}

async function bounds(page, selector) {
  const box = await page.locator(selector).first().boundingBox();
  assert(box, `${selector} must have visible bounds`);
  return box;
}

async function homeSnapshot(page) {
  const normalizedMarkup = locator => locator.evaluate(element => element.innerHTML.replace(/>\s+</g, '><').trim());
  return {
    headings: await page.locator('h1, h2, h3').allTextContents(),
    links: await page.locator('a:visible').evaluateAll(links => links.map(link => ({
      text: link.textContent.trim(),
      href: link.getAttribute('href'),
    }))),
    examples: await page.locator('.site-demo-stats, .site-demo-user, .site-demo-health, .site-preview-note').allTextContents(),
    meterAttributes: await page.locator('meter').evaluateAll(meters => meters.map(meter => ({
      min: meter.getAttribute('min'),
      max: meter.getAttribute('max'),
      value: meter.getAttribute('value'),
      label: meter.getAttribute('aria-label'),
    }))),
    topologySvg: await normalizedMarkup(page.locator('.site-topology-lines')),
    chartSvg: await normalizedMarkup(page.locator('.site-chart svg')),
    background: await page.evaluate(() => getComputedStyle(document.body).backgroundColor),
  };
}

async function compareBounds(reactPage, legacyPage, width) {
  for (const selector of ['.site-main', '.site-hero', '.site-topology', '.site-feature', '.site-console']) {
    const actual = await bounds(reactPage, selector);
    const expected = await bounds(legacyPage, selector);
    for (const key of ['x', 'y', 'width', 'height']) {
      assert(
        Math.abs(actual[key] - expected[key]) <= 2,
        `${selector} ${key} parity at ${width}px: React ${actual[key]}, legacy ${expected[key]}`,
      );
    }
  }
}

async function capturePair(reactPage, legacyPage, name) {
  if (!screenshotDir) return;
  fs.mkdirSync(screenshotDir, { recursive: true });
  await reactPage.screenshot({ path: path.join(screenshotDir, `react-home-${name}.png`), fullPage: true });
  await legacyPage.screenshot({ path: path.join(screenshotDir, `legacy-home-${name}.png`), fullPage: true });
}

async function verifyPublicDocumentAndTabs(browser) {
  const context = await browser.newContext({ viewport: { width: 1024, height: 900 }, reducedMotion: 'reduce' });
  await context.addInitScript(() => {
    localStorage.setItem('hy2.sidebar', 'collapsed');
    localStorage.setItem('hy2.sidebar-motion', 'enabled');
  });
  for (const [hash, selected] of [
    ['', 'traffic'],
    ['#demo-traffic', 'traffic'],
    ['#demo-users', 'users'],
    ['#demo-health', 'health'],
    ['#unknown', 'traffic'],
  ]) {
    const page = await context.newPage();
    const collection = collectFailures(page, `initial hash ${hash || '(absent)'}`);
    await gotoHome(page, hash);
    assert.equal(await page.title(), 'Hysteria · 连接网络，掌控全局');
    assert.equal(await page.locator('body').getAttribute('class'), 'page-home page-site');
    assert.equal(await page.locator('body.has-shell, .app, .sidebar, .badge').count(), 0);
    assert.equal(await page.locator('html.sidebar-pre-collapsed, html.sidebar-motion-enabled').count(), 0);
    assert.equal(await page.locator('.site-demo-label').innerText(), '界面示意 · 非实时数据');
    assert.equal(await page.locator('.site-topology').getAttribute('role'), 'img');
    assert.equal(
      await page.locator('.site-topology').getAttribute('aria-label'),
      '概念示意：Hysteria 控制台连接多协议接入、流量统计和健康监测',
    );
    assert.equal(await page.locator('a[href="/login"]').count(), 3);
    assert.equal(await page.locator('script[src*="home.js"], script[src*="ui-core.js"], script[src*="shell.js"], script[src*="shell-preferences.js"]').count(), 0);
    await assertSelected(page, selected);
    assertClean(collection);
    await page.close();
  }

  const page = await context.newPage();
  const collection = collectFailures(page, 'tab interaction');
  await gotoHome(page, '#demo-users');
  await page.locator('#demo-tab-traffic').scrollIntoViewIfNeeded();
  const initialUrl = page.url();
  const initialScroll = await page.evaluate(() => scrollY);
  await page.locator('#demo-tab-traffic').click();
  await assertSelected(page, 'traffic');
  assert.equal(page.url(), initialUrl, 'click activation must not change the hash');
  assert.equal(await page.evaluate(() => scrollY), initialScroll, 'click activation must not jump the page');

  await page.locator('#demo-tab-users').focus();
  await page.keyboard.press('ArrowRight');
  await assertSelected(page, 'health');
  assert.equal(await page.locator('#demo-tab-health').evaluate(element => element === document.activeElement), true);
  await page.keyboard.press('ArrowRight');
  await assertSelected(page, 'traffic');
  await page.keyboard.press('ArrowLeft');
  await assertSelected(page, 'health');
  await page.keyboard.press('Home');
  await assertSelected(page, 'traffic');
  await page.keyboard.press('End');
  await assertSelected(page, 'health');
  await page.keyboard.press('Space');
  await assertSelected(page, 'health');
  assert.equal(await page.locator('#demo-tab-health').evaluate(element => element === document.activeElement), true);
  assertClean(collection);
  await context.close();
}

async function fragmentState(page) {
  return page.evaluate(() => ({
    scrollY,
    activeId: document.activeElement?.id || '',
    activeTag: document.activeElement?.tagName || '',
  }));
}

async function verifyInitialFragmentNavigation(browser) {
  const context = await browser.newContext({ viewport: { width: 1024, height: 768 }, reducedMotion: 'reduce' });
  for (const fragment of ['demo-traffic', 'demo-users', 'demo-health', 'services', 'console-preview', 'unknown', '']) {
    const suffix = fragment ? `#${fragment}` : '';
    const reactPage = await context.newPage();
    const reactCollection = collectFailures(reactPage, `React initial fragment ${suffix || '(absent)'}`);
    await gotoHome(reactPage, suffix);
    await reactPage.waitForFunction(() => document.readyState === 'complete');

    const legacyPage = await context.newPage();
    const legacyCollection = collectFailures(legacyPage, `legacy initial fragment ${suffix || '(absent)'}`);
    const legacyResponse = await legacyPage.goto(`${baseUrl}/${suffix}`);
    assert.equal(legacyResponse.status(), 200);
    await legacyPage.locator('.site-main').waitFor();
    await legacyPage.waitForFunction(() => document.readyState === 'complete');

    const actual = await fragmentState(reactPage);
    const expected = await fragmentState(legacyPage);
    assert(
      Math.abs(actual.scrollY - expected.scrollY) <= 2,
      `initial ${suffix || '(absent)'} scroll parity: React ${actual.scrollY}, legacy ${expected.scrollY}`,
    );
    assert.equal(actual.activeId, expected.activeId, `initial ${suffix || '(absent)'} active element id parity`);
    assert.equal(actual.activeTag, expected.activeTag, `initial ${suffix || '(absent)'} active element tag parity`);
    assertClean(reactCollection);
    assertClean(legacyCollection);
    await reactPage.close();
    await legacyPage.close();
  }
  await context.close();
}

async function verifyLegacyParity(browser) {
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, reducedMotion: 'reduce' });
  for (const width of [1920, 1024, 390]) {
    const reactPage = await context.newPage();
    const reactCollection = collectFailures(reactPage, `React comparison ${width}`);
    await reactPage.setViewportSize({ width, height: 1080 });
    await gotoHome(reactPage, '#demo-traffic');

    const legacyPage = await context.newPage();
    const legacyCollection = collectFailures(legacyPage, `legacy comparison ${width}`);
    await legacyPage.setViewportSize({ width, height: 1080 });
    const legacyResponse = await legacyPage.goto(`${baseUrl}/#demo-traffic`);
    assert.equal(legacyResponse.status(), 200);
    await legacyPage.locator('.site-main').waitFor();
    await legacyPage.locator('#demo-tab-traffic[aria-selected="true"]').waitFor();
    await reactPage.evaluate(() => scrollTo(0, 0));
    await legacyPage.evaluate(() => scrollTo(0, 0));

    assert.deepEqual(await homeSnapshot(reactPage), await homeSnapshot(legacyPage), `legacy/React content parity at ${width}px`);
    await compareBounds(reactPage, legacyPage, width);
    assert.equal(await reactPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(await legacyPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await capturePair(reactPage, legacyPage, `traffic-${width}`);
    assertClean(reactCollection);
    assertClean(legacyCollection);
    await reactPage.close();
    await legacyPage.close();
  }

  for (const demo of ['users', 'health']) {
    const reactPage = await context.newPage();
    const reactCollection = collectFailures(reactPage, `React ${demo} desktop`);
    await gotoHome(reactPage, `#demo-${demo}`);
    const legacyPage = await context.newPage();
    const legacyCollection = collectFailures(legacyPage, `legacy ${demo} desktop`);
    const legacyResponse = await legacyPage.goto(`${baseUrl}/#demo-${demo}`);
    assert.equal(legacyResponse.status(), 200);
    await legacyPage.locator(`#demo-tab-${demo}[aria-selected="true"]`).waitFor();
    await reactPage.evaluate(() => scrollTo(0, 0));
    await legacyPage.evaluate(() => scrollTo(0, 0));
    assert.deepEqual(await homeSnapshot(reactPage), await homeSnapshot(legacyPage), `legacy/React ${demo} content parity`);
    await compareBounds(reactPage, legacyPage, 1920);
    await capturePair(reactPage, legacyPage, `${demo}-1920`);
    assertClean(reactCollection);
    assertClean(legacyCollection);
    await reactPage.close();
    await legacyPage.close();
  }
  await context.close();
}

async function verifyRevealFallbacks(browser) {
  const reduced = await browser.newContext({ viewport: { width: 1024, height: 768 }, reducedMotion: 'reduce' });
  const reducedPage = await reduced.newPage();
  const reducedCollection = collectFailures(reducedPage, 'reduced motion');
  await gotoHome(reducedPage);
  assert.equal(await reducedPage.locator('.site-feature:visible').count(), 3);
  assert.equal(await reducedPage.locator('.site-console').isVisible(), true);
  assert.equal(await reducedPage.locator('.site-reveal').count(), 0);
  assertClean(reducedCollection);
  await reduced.close();

  const unsupported = await browser.newContext({ viewport: { width: 1024, height: 768 } });
  await unsupported.addInitScript(() => { delete window.IntersectionObserver; });
  const unsupportedPage = await unsupported.newPage();
  const unsupportedCollection = collectFailures(unsupportedPage, 'missing IntersectionObserver');
  await gotoHome(unsupportedPage);
  assert.equal(await unsupportedPage.locator('.site-feature:visible').count(), 3);
  assert.equal(await unsupportedPage.locator('.site-console').isVisible(), true);
  assert.equal(await unsupportedPage.locator('.site-reveal').count(), 0);
  assertClean(unsupportedCollection);
  await unsupported.close();

  const normal = await browser.newContext({ viewport: { width: 1024, height: 600 } });
  const normalPage = await normal.newPage();
  const normalCollection = collectFailures(normalPage, 'normal reveal');
  await gotoHome(normalPage);
  const consolePreview = normalPage.locator('.site-console');
  await consolePreview.scrollIntoViewIfNeeded();
  await consolePreview.evaluate(element => new Promise(resolve => {
    if (element.classList.contains('site-reveal')) resolve();
    const observer = new MutationObserver(() => {
      if (element.classList.contains('site-reveal')) {
        observer.disconnect();
        resolve();
      }
    });
    observer.observe(element, { attributes: true, attributeFilter: ['class'] });
  }));
  assert.equal(await consolePreview.evaluate(element => element.classList.contains('site-reveal')), true);
  assertClean(normalCollection);
  await normal.close();
}

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    await verifyPublicDocumentAndTabs(browser);
    await verifyInitialFragmentNavigation(browser);
    await verifyLegacyParity(browser);
    await verifyRevealFallbacks(browser);
    console.log('PASS: React public home content, tabs, visual parity, isolation, and reveal behavior');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
