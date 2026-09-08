// Run after: python3 tests/workspace_preview_server.py
// PLAYWRIGHT_MODULE selects an existing Playwright installation, no npm install needed.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const baseUrl = process.env.PREVIEW_BASE_URL || 'http://127.0.0.1:18764';
(async () => {
  const browser = await chromium.launch({args: ['--no-sandbox', '--disable-dev-shm-usage']});
  try {
    const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
    const failures = [];
    page.on('pageerror', error => failures.push(error.message));
    page.on('requestfailed', request => {
      const reason = request.failure()?.errorText || 'unknown network failure';
      // Navigation intentionally cancels page-owned fetches, never static assets.
      if (reason.includes('ERR_ABORTED') && ['fetch', 'xhr'].includes(request.resourceType())) return;
      failures.push(`${reason} ${request.url()}`);
    });
    page.on('response', response => {
      if (response.status() >= 400) failures.push(`${response.status()} ${response.url()}`);
    });
    await page.goto(baseUrl + '/login');
    await page.addScriptTag({url: await page.locator('script[src*="/static/login.js"]').getAttribute('src')});
    await page.locator('#login-password-toggle').click();
    assert.equal(await page.locator('#admin-password').getAttribute('type'), 'text');
    await page.locator('#login-password-toggle').click();
    assert.equal(await page.locator('#admin-password').getAttribute('type'), 'password');
    await page.goto(baseUrl + '/admin');
    assert.deepEqual(await page.locator('.users-table th').allTextContents(), ['用户','趋势','用量','操作','链接']);
    for (const name of ['编辑套餐','清流量','刷新流量','重置订阅','暂停','删除','复制 demo_alex 的专属面板链接']) {
      assert(await page.getByRole('button', {name, exact: true}).first().isVisible(), name);
    }
    // Five-column management must use the available desktop width, not a narrow landing-page container.
    assert(await page.locator('.content').evaluate(e => e.clientWidth) > 1400, 'Desktop workspace must use available width');
    assert.equal(await page.locator('#cycle-day').evaluate(e => getComputedStyle(e).borderTopStyle), 'solid', 'Cycle inputs must not retain native inset borders');
    await page.locator('.edit-user').first().click();
    assert(await page.locator('#user-edit-dialog').isVisible(), 'Edit plan dialog opens');
    await page.keyboard.press('Escape');
    await page.locator('#sidebar-collapse').click();
    assert(await page.locator('.app').evaluate(e => e.classList.contains('sidebar-collapsed')));
    await page.locator('#sidebar-collapse').click();
    for (const width of [1920, 1024, 390]) {
      await page.setViewportSize({width, height: 1080});
      for (const route of ['/admin', '/user/panel', '/admin/usage', '/admin/settings', '/admin/config', '/admin/rules', '/history']) {
        await page.goto(baseUrl + route);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${route}: overflow at ${width}`);
        if (route === '/history') {
          assert.equal(await page.locator('.daily-table-collapsed tbody th').first().evaluate(e => getComputedStyle(e).position), 'sticky');
          assert.equal(await page.locator('.daily-table-collapsed tbody td').first().evaluate(e => getComputedStyle(e).whiteSpace), 'nowrap');
        }
        if (route === '/admin/config') {
          await page.locator('#configEditor').fill('{invalid');
          await page.locator('#cfgFormat').click();
          assert.equal(await page.locator('#configEditor').getAttribute('aria-invalid'), 'true');
          await page.locator('#configEditor').fill('{"preview":true}');
          await page.locator('#cfgFormat').click();
          assert.deepEqual(JSON.parse(await page.locator('#configEditor').inputValue()), {preview: true});
          assert.equal(await page.locator('#configEditor').getAttribute('aria-invalid'), null);
        }
        if (route === '/admin/rules') {
          let submissions = 0;
          const countSubmit = request => { if (request.method() === 'POST') submissions++; };
          page.on('request', countSubmit);
          const dialogResult = page.waitForEvent('dialog').then(async dialog => {
            assert.equal(dialog.message(), '确认删除此规则？');
            await dialog.dismiss();
          });
          await page.locator('form[data-action="delete-rule"] button[type="submit"]').first().click();
          await dialogResult;
          assert.equal(submissions, 0, 'Cancel must prevent deletion');
          page.off('request', countSubmit);
        }
        if (route === '/admin/usage') {
          const heat = page.locator('#heatmap-host svg.heatmap');
          const before = await heat.boundingBox();
          await page.locator('.heatmap-data-details summary').click();
          const after = await heat.boundingBox();
          assert(Math.abs(before.width - after.width) < 2, 'Opening hourly table must not resize heatmap');
          assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Expanded hourly table overflow');
          assert(await page.locator('#top-n-host .top-row').count() > 0, 'Nonempty ranking fixture');
          const spark = await page.locator('.top-spark svg').first().boundingBox();
          assert(spark.height <= 32 && spark.width <= 124, 'Ranking sparkline must remain compact');
          const scroller = page.locator('.heatmap-data-scroll');
          assert(await scroller.evaluate(e => e.scrollWidth > e.clientWidth), 'Wide hourly table should scroll internally');
        }
        if (route === '/user/panel' && width >= 1024) {
          const a = await page.locator('.connection-section').boundingBox();
          const b = await page.locator('.plan-section').boundingBox();
          assert(Math.abs(a.y - b.y) < 2 && b.x > a.x, 'Desktop connection and plan panels should share a row');
        }
        if (process.env.SCREENSHOT_DIR && ['/admin','/user/panel'].includes(route)) await page.screenshot({path: `${process.env.SCREENSHOT_DIR}/${route==='/admin'?'admin':'user'}-${width}.png`, fullPage: true});
      }
    }
    assert.deepEqual(failures, [], 'No script errors or failed HTTP responses');
    console.log('PASS: five columns, all seven management actions, wide desktop layout, desktop user grid, responsive overflow checks');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
