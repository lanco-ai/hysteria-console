/* global document, window, getComputedStyle */

// Run against a built React preview.  The assertions intentionally inspect
// rendered geometry so the workbench cannot regress into the former landing
// page or a second, narrow chat rail.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const baseUrl = process.env.PREVIEW_BASE_URL || 'http://127.0.0.1:18765';
const screenshotDir = process.env.REACT_SCREENSHOT_DIR;
const fixturePassword = process.env.REACT_PREVIEW_LOGIN_PASSWORD || 'preview-only-password';

function save(page, name) {
  if (!screenshotDir) return Promise.resolve();
  fs.mkdirSync(screenshotDir, {recursive: true});
  return page.screenshot({path: path.join(screenshotDir, `${name}.png`), fullPage: true});
}

async function assertNoOverflow(page, label) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  assert.equal(overflow, false, `${label} must not have horizontal overflow`);
}

(async () => {
  const browser = await chromium.launch({args: ['--no-sandbox', '--disable-dev-shm-usage']});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 960}});
    await page.goto(`${baseUrl}/`, {waitUntil: 'domcontentloaded'});
    await page.locator('.app').waitFor();
    await page.locator('.login-modal[role="dialog"]').waitFor();
    assert.equal(await page.getByText('加速你的网络', {exact: false}).count(), 0, 'root must not render landing-page marketing');
    assert.equal(await page.locator('.sidebar:visible').count(), 1, 'desktop has one visible navigation rail');
    assert.equal(await page.locator('.app').evaluate(node => getComputedStyle(node).gridTemplateColumns.split(' ')[0]), '240px', 'desktop rail is 240px');
    const desktopComposer = await page.locator('.chat-composer').evaluate(node => {
      const box = node.getBoundingClientRect();
      return {bottom: box.bottom, left: box.left, right: box.right, viewport: window.innerHeight};
    });
    assert(desktopComposer.bottom <= desktopComposer.viewport - 12, 'composer stays inside the viewport');
    assert(desktopComposer.right > desktopComposer.left, 'composer has usable desktop bounds');
    const modalBackdrop = await page.locator('.login-modal-backdrop').evaluate(node => {
      const style = getComputedStyle(node);
      return {
        color: style.backgroundColor,
        blur: style.backdropFilter === 'none'
          ? style.getPropertyValue('-webkit-backdrop-filter')
          : style.backdropFilter,
      };
    });
    assert.notEqual(modalBackdrop.color, 'rgba(0, 0, 0, 0)', 'login modal must dim the workbench');
    assert.match(modalBackdrop.blur, /blur/, 'login modal must blur the workbench behind it');
    await assertNoOverflow(page, 'desktop workbench');
    await save(page, 'workbench-desktop-1440');

    await page.setViewportSize({width: 1024, height: 900});
    await page.reload({waitUntil: 'domcontentloaded'});
    await page.locator('.login-modal[role="dialog"]').waitFor();
    assert.equal(await page.locator('.sidebar:visible').count(), 1, 'tablet has one visible navigation rail');
    await assertNoOverflow(page, 'tablet workbench');
    await save(page, 'workbench-tablet-1024');

    await page.setViewportSize({width: 390, height: 844});
    await page.goto(`${baseUrl}/login`, {waitUntil: 'domcontentloaded'});
    await page.locator('#login-modal-username').fill('admin');
    await page.locator('#login-modal-password').fill(fixturePassword);
    await page.getByRole('button', {name: '登录', exact: true}).click();
    await page.locator('.login-modal').waitFor({state: 'detached'});
    await page.locator('#sidebar-toggle').click();
    await page.locator('.sidebar.open').waitFor();
    assert.equal(await page.locator('.scrim').evaluate(node => getComputedStyle(node).display), 'block', 'mobile drawer has a scrim');
    assert.equal(await page.locator('.sidebar.open').count(), 1, 'mobile exposes one drawer');
    const mobileComposer = await page.locator('.chat-composer').evaluate(node => {
      const box = node.getBoundingClientRect();
      return {left: box.left, right: box.right, bottom: box.bottom, width: window.innerWidth, height: window.innerHeight};
    });
    assert(mobileComposer.left >= 0 && mobileComposer.right <= mobileComposer.width, 'mobile composer fits its viewport');
    assert(mobileComposer.bottom <= mobileComposer.height, 'mobile composer stays within its viewport');
    await assertNoOverflow(page, 'mobile workbench');
    await save(page, 'workbench-mobile-390');
    console.log('PASS: responsive workbench visual contract');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
