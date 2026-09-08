const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const baseUrl = process.env.PREVIEW_BASE_URL;

(async () => {
  const browser = await chromium.launch({args: ['--no-sandbox']});
  try {
    const page = await browser.newPage();
    const errors = [];
    let polls = 0;
    let failPoll = false;
    await page.clock.install();
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'clipboard', {value: {
        writeText: async text => { window.copiedSubscription = text; },
      }});
    });
    await page.route('**/user/panel.json', route => {
      polls++;
      if (failPoll) return route.fulfill({status: 503, json: {error: 'temporary'}});
      return route.fulfill({json: {
      used_bytes: 1073741824, remain_bytes: 2147483648, total_bytes: 3221225472,
      online: 2, max_devices: 3, percent: 33.33, tx_bytes: 536870912, rx_bytes: 536870912,
      }});
    });
    await page.route('**/qr.svg?*', route => route.fulfill({contentType: 'image/svg+xml',
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'}));
    await page.goto(baseUrl + '/user/panel');
    await page.addScriptTag({url: await page.locator('script[src*="/static/user-panel.js"]').getAttribute('src')});
    await page.waitForFunction(() => document.querySelector('[data-role="used"]').textContent === '1.00 GB');
    const option = page.locator('[data-profile-option][data-profile="game"]');
    const expected = await option.getAttribute('data-profile-url');
    await option.click();
    assert.equal(await page.locator('#sub').textContent(), expected);
    assert.equal(await page.locator('#profile-open').getAttribute('href'), expected);
    await page.locator('#profile-copy').click();
    await page.waitForFunction(expected => window.copiedSubscription === expected, expected);
    await page.locator('#profile-show-qr').click();
    assert.equal(await page.locator('#profile-show-qr').getAttribute('aria-expanded'), 'true');
    await page.waitForFunction(() => document.querySelector('#profile-qr-image').naturalWidth > 0);
    await page.locator('#profile-show-qr').click();
    assert.equal(await page.locator('#profile-qr-image').getAttribute('src'), null);
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', {configurable: true, value: true});
      document.dispatchEvent(new Event('visibilitychange'));
    });
    const pausedCount = polls;
    await page.clock.fastForward(120000);
    assert.equal(polls, pausedCount, 'Hidden pages must stop polling');
    failPoll = true;
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', {configurable: true, value: false});
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await page.waitForFunction(() => document.querySelector('[data-role="poll-status"]').classList.contains('is-error'));
    failPoll = false;
    await page.clock.fastForward(65000);
    await page.waitForFunction(() => document.querySelector('[data-role="poll-status"]').textContent.startsWith('更新于'));
    assert(polls > pausedCount + 1, 'Failed refresh must retry and recover');
    const activeCount = polls;
    await page.goto(baseUrl + '/user/panel?state=disabled');
    assert.equal(await page.locator('script[src*="/static/user-poll.js"]').count(), 0);
    await page.clock.fastForward(300000);
    assert.equal(polls, activeCount, 'Inactive account must not poll');
    assert.deepEqual(errors, []);
    console.log('PASS user metrics, subscription, copy, QR, hidden pause and retry recovery');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
