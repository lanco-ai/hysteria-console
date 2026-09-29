const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');
const path = require('node:path');
const fs = require('node:fs');

const item = (rank, name, language, description, total, period, forks, htmlUrl = `https://github.com/${name}`) => ({
  source_rank: rank, full_name: name, html_url: htmlUrl, description, language,
  stars_total: total, stars_period: period, forks_count: forks,
});
const weeklyItems = [
  item(2, 'octocat/hello-world', 'TypeScript', 'A welcoming repository', 1200, 0, 14),
  item(5, 'openai/sample', 'Python', '<script>alert(1)</script> safe text', null, 42, 0),
  item(8, 'danger/example', null, null, 1, null, null, 'https://evil.example/steal'),
];
const dailyItems = [item(3, 'daily/today', 'Rust', 'Today only', 12, 3, 1)];
const snapshot = (period, overrides = {}) => ({
  period, source: 'GitHub Trending', source_url: `https://github.com/trending?since=${period}`,
  fetched_at: '2026-09-28T13:34:36Z', last_success_at: '2026-09-28T13:34:36Z',
  is_stale: false, refreshing: false, items: period === 'weekly' ? weeklyItems : dailyItems,
  error: null, status: 'ready', cooldown_seconds: 0, retry_after_seconds: 0,
  ...overrides,
});

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const base = process.env.PREVIEW_BASE_URL;
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] });
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const page = await context.newPage();
    const errors = [];
    const requests = [];
    let weekly = snapshot('weekly');
    let daily = snapshot('daily');
    let refresh = snapshot('weekly', { cooldown_seconds: 2 });
    let getStatus = 200;
    let postStatus = 200;
    let failNextWeeklyGet = false;
    let holdWeekly = false;
    let releaseWeekly;
    let heldProcessed = 0;
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/v1/github-trending**', async route => {
      const request = route.request();
      if (new URL(request.url()).pathname.startsWith('/api/v1/github-trending/avatar/')) {
        if (request.url().endsWith('/fallback')) return route.fulfill({ status: 502, contentType: 'application/json', body: '{}' });
        return route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAEUlEQVR4nGMIdL2CFTEMLQkAPBVagW2PDnoAAAAASUVORK5CYII=', 'base64') });
      }
      requests.push(`${request.method()} ${new URL(request.url()).pathname}${new URL(request.url()).search}`);
      if (request.method() === 'POST') {
        assert.deepEqual(request.postDataJSON(), { period: 'weekly' });
        return route.fulfill({ status: postStatus, contentType: 'application/json', body: JSON.stringify(refresh) });
      }
      if (getStatus !== 200) return route.fulfill({ status: getStatus, contentType: 'application/json', body: JSON.stringify({ error: 'login_required' }) });
      const period = new URL(request.url()).searchParams.get('period') || 'weekly';
      if (period === 'weekly' && failNextWeeklyGet) {
        failNextWeeklyGet = false;
        return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'storage_unavailable' }) });
      }
      const payload = period === 'weekly' ? weekly : daily;
      if (period === 'weekly' && holdWeekly) {
        await new Promise(resolve => { releaseWeekly = resolve; });
        try { await route.fulfill({ contentType: 'application/json', body: JSON.stringify(payload) }); }
        catch { /* An aborted older request may close before the fixture is released. */ }
        finally { heldProcessed += 1; }
        return;
      }
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(payload) });
    });

    await page.clock.install();
    await page.goto(`${base}/admin/github-trending`);
    await expect(page.getByRole('heading', { name: 'GitHub 热榜', exact: true }).last()).toBeVisible();
    await expect(page.getByRole('link', { name: '开源发现' })).toHaveAttribute('href', '/admin/github-trending');
    await expect(page.getByRole('button', { name: '周榜' })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('.trending-repo')).toHaveCount(3);
    await expect(page.locator('.trending-top-card')).toHaveCount(1);
    await expect(page.locator('.trending-top-card').first()).toContainText('#2');
    await expect(page.locator('.trending-repo-row')).toHaveCount(2);
    await expect(page.locator('.trending-top-card img')).toHaveAttribute('src', '/api/v1/github-trending/avatar/octocat');
    await expect(page.locator('.trending-top-card .trending-progress-fill')).toHaveAttribute('data-ratio', '0');
    await expect(page.locator('.trending-repo-row').first().locator('.trending-progress-fill')).toHaveAttribute('data-ratio', '1');
    await expect(page.locator('.trending-repo-row').last().locator('.trending-progress-fill')).toHaveCount(0);
    await page.locator('.trending-top-card .trending-entry-toggle').first().focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('.trending-top-card .trending-entry-toggle').first()).toHaveAttribute('aria-expanded', 'true');
    await page.keyboard.press('Space');
    await expect(page.locator('.trending-top-card .trending-entry-toggle').first()).toHaveAttribute('aria-expanded', 'false');
    const touchContext = await browser.newContext({ viewport: { width: 719, height: 900 }, hasTouch: true, permissions: ['clipboard-read', 'clipboard-write'] });
    try {
      await touchContext.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
      const touchPage = await touchContext.newPage();
      await touchPage.route('**/api/v1/github-trending**', route => {
        if (new URL(route.request().url()).pathname.startsWith('/api/v1/github-trending/avatar/')) return route.fulfill({ status: 502, contentType: 'application/json', body: '{}' });
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(snapshot('weekly')) });
      });
      await touchPage.goto(`${base}/admin/github-trending`);
      const touchRow = touchPage.locator('.trending-repo-row').first();
      await expect(touchRow).toContainText('openai/sample');
      const hiddenLink = touchRow.locator('.trending-actions a');
      const hiddenCopy = touchRow.locator('.trending-actions button');
      const linkBox = await hiddenLink.boundingBox();
      const copyBox = await hiddenCopy.boundingBox();
      assert(linkBox && copyBox, 'hidden action target boxes should exist for hit testing');
      const hitTargets = await touchPage.evaluate(({ link, copy }) => [link, copy].map(box => document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)?.className), { link: linkBox, copy: copyBox });
      let popupCount = 0;
      touchPage.on('popup', popup => { popupCount += 1; void popup.close(); });
      await touchPage.touchscreen.tap(linkBox.x + linkBox.width / 2, linkBox.y + linkBox.height / 2);
      await expect(touchRow.locator('.trending-entry-toggle')).toHaveAttribute('aria-expanded', 'true');
      const tapExpanded = await touchRow.locator('.trending-entry-toggle').getAttribute('aria-expanded');
      const touchActionSizes = await touchRow.locator('.trending-actions a, .trending-actions button').evaluateAll(elements => elements.map(element => ({ width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height })));
      const touchTargetsComfortable = touchActionSizes.every(size => size.width >= 44 && size.height >= 44);
      const untouchedClipboard = await touchPage.evaluate(() => navigator.clipboard.readText());
      await touchRow.getByRole('button', { name: /复制链接/ }).tap();
      const copiedAfterExpansion = await touchPage.evaluate(() => navigator.clipboard.readText());
      const actionScreenshots = process.env.REACT_TRENDING_SCREENSHOT_DIR || path.join(process.cwd(), '.astra-luna/screenshots');
      fs.mkdirSync(actionScreenshots, { recursive: true });
      await touchPage.screenshot({ path: path.join(actionScreenshots, 'github-trending-actions-719.png'), fullPage: true });
      await touchPage.setViewportSize({ width: 390, height: 900 });
      await touchPage.reload();
      const compactRow = touchPage.locator('.trending-repo-row').first();
      await compactRow.locator('.trending-entry-toggle').focus();
      const focusedActionDisplay = await compactRow.locator('.trending-actions').evaluate(element => getComputedStyle(element).display);
      await touchPage.screenshot({ path: path.join(actionScreenshots, 'github-trending-actions-390.png'), fullPage: true });
      assert.deepEqual({ hitTargets, tapExpanded, popupCount, untouchedClipboard, copiedAfterExpansion, focusedActionDisplay, touchTargetsComfortable }, {
        hitTargets: ['trending-entry-toggle', 'trending-entry-toggle'], tapExpanded: 'true', popupCount: 0, untouchedClipboard: '', copiedAfterExpansion: 'https://github.com/openai/sample', focusedActionDisplay: 'flex', touchTargetsComfortable: true,
      }, 'hidden actions must pass touch hits to disclosure, while focused compact rows expose actions');
      await compactRow.getByRole('button', { name: /复制链接/ }).focus();
      await touchPage.keyboard.press('Enter');
      assert.equal(await touchPage.evaluate(() => navigator.clipboard.readText()), 'https://github.com/openai/sample');
    } finally { await touchContext.close(); }
    const desktopLayout = await page.evaluate(() => {
      const pageBox = document.querySelector('.trending-page').getBoundingClientRect();
      const contentBox = document.querySelector('.content').getBoundingClientRect();
      const title = document.querySelector('.trending-intro h2');
      const row = document.querySelector('.trending-repo-row').getBoundingClientRect();
      const action = document.querySelector('.trending-actions a').getBoundingClientRect();
      const rank = document.querySelector('.trending-top-card .trending-rank');
      const accent = getComputedStyle(document.querySelector('.trending-page')).getPropertyValue('--trending-accent').trim();
      const shellSage = getComputedStyle(document.body).getPropertyValue('--data').trim();
      return { left: pageBox.left, right: pageBox.right, width: pageBox.width, contentLeft: contentBox.left, contentRight: contentBox.right, titleSize: parseFloat(getComputedStyle(title).fontSize), rowHeight: row.height, actionHeight: action.height, rankSize: parseFloat(getComputedStyle(rank).fontSize), accent, shellSage };
    });
    assert(desktopLayout.width <= 1280 && Math.abs((desktopLayout.left - desktopLayout.contentLeft) - (desktopLayout.contentRight - desktopLayout.right)) <= 2, 'desktop content should share the centered page width');
    assert(desktopLayout.titleSize >= 28 && desktopLayout.titleSize <= 34, 'trending heading should align with the other page headings');
    assert(desktopLayout.rankSize <= 18, 'rank labels should stay legible without giant watermarks');
    assert.equal(desktopLayout.accent, desktopLayout.shellSage, 'trending accents should use the shell sage token');
    assert(desktopLayout.rowHeight <= 125, 'desktop repository rows should remain compact');
    assert(desktopLayout.actionHeight >= 36, 'repository actions should offer generous click targets');
    await expect(page.locator('.trending-repo').first()).toContainText('#2');
    await expect(page.locator('.trending-repo').first()).toContainText('1,200');
    await expect(page.locator('.trending-repo').first()).toContainText('0');
    await expect(page.locator('.trending-repo').first().locator('.trending-period-stat strong')).toHaveText('0');
    await expect(page.locator('.trending-repo').nth(1)).toContainText('—');
    await expect(page.locator('.trending-repo').nth(1)).toContainText('<script>alert(1)</script> safe text');
    assert.equal(await page.locator('.trending-repo script').count(), 0);
    await expect(page.locator('.trending-repo').nth(2).getByRole('link', { name: /在 GitHub 查看/ })).toHaveCount(0);
    await expect(page.locator('.trending-repo').first().getByRole('link', { name: /在 GitHub 查看/ })).toHaveAttribute('rel', 'noopener noreferrer');
    await page.locator('.trending-repo').first().getByRole('button', { name: /复制链接/ }).click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'https://github.com/octocat/hello-world');
    await expect(page.getByRole('status')).toContainText('链接已复制');

    const beforeFilter = requests.length;
    await page.getByRole('searchbox', { name: '搜索仓库或简介' }).fill('safe text');
    await expect(page.locator('.trending-repo')).toHaveCount(1);
    await expect(page.locator('.trending-top-card')).toHaveCount(0);
    await expect(page.locator('.trending-repo-row .trending-progress-fill')).toHaveAttribute('data-ratio', '1');
    await page.getByRole('searchbox', { name: '搜索仓库或简介' }).fill('');
    await page.getByLabel('语言（本榜内筛选）').selectOption('TypeScript');
    await expect(page.locator('.trending-repo')).toHaveCount(1);
    await expect(page.locator('.trending-top-card')).toHaveCount(0);
    await expect(page.locator('.trending-filters small')).toContainText('本榜内筛选');
    assert.equal(requests.length, beforeFilter, 'local filters must not issue API requests');
    await page.getByRole('searchbox', { name: '搜索仓库或简介' }).fill('nothing-matches');
    await expect(page.getByText('本榜内没有符合筛选条件的仓库')).toBeVisible();
    await page.getByRole('button', { name: '清除筛选' }).click();
    await expect(page.getByRole('searchbox', { name: '搜索仓库或简介' })).toHaveValue('');
    await expect(page.getByLabel('语言（本榜内筛选）')).toHaveValue('');
    await expect(page.locator('.trending-repo')).toHaveCount(3);
    assert.equal(requests.length, beforeFilter, 'clearing both filters must stay local');
    weekly = snapshot('weekly', { fetched_at: '2026-09-29T00:00:00Z', items: [
      item(1, 'fallback/first', 'Go', 'Fallback avatar', 100, 20, 4),
      item(2, 'octocat/hello-world', 'TypeScript', 'A welcoming repository', 1200, 0, 14),
      item(3, 'openai/sample', 'Python', 'Another focus card', 500, 40, 12),
      item(4, 'other/fourth', 'Rust', 'First compact row', 90, 10, 2),
    ] });
    await page.reload();
    await expect(page.locator('.trending-top-card')).toHaveCount(3);
    await expect(page.locator('.trending-repo-row')).toHaveCount(1);
    await expect(page.locator('.trending-top-card').first().locator('.trending-avatar')).toContainText('F');
    await expect(page.locator('.trending-top-card').nth(2).locator('.trending-progress-fill')).toHaveAttribute('data-ratio', '1');
    const focusBoxes = await page.locator('.trending-top-card').evaluateAll(elements => elements.map(element => ({ x: element.getBoundingClientRect().x, y: element.getBoundingClientRect().y })));
    assert(focusBoxes.every(box => box.y === focusBoxes[0].y) && focusBoxes[0].x < focusBoxes[1].x && focusBoxes[1].x < focusBoxes[2].x, 'top ranks should occupy three desktop columns');
    const focusScreenshots = process.env.REACT_TRENDING_SCREENSHOT_DIR || path.join(process.cwd(), '.astra-luna/screenshots');
    fs.mkdirSync(focusScreenshots, { recursive: true });
    await page.clock.runFor(700);
    await expect(page.locator('.trending-top-card').nth(2).locator('.trending-period-stat strong')).toHaveText('40');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const reducedCard = page.locator('.trending-top-card').first();
    const glowBox = await reducedCard.boundingBox();
    assert(glowBox, 'focus card must have a box for hover');
    await page.mouse.move(glowBox.x + 10, glowBox.y + 10);
    await page.mouse.move(glowBox.x + glowBox.width - 10, glowBox.y + glowBox.height - 10);
    const glowAfter = await reducedCard.evaluate(element => ({ content: getComputedStyle(element, '::before').content, x: element.style.getPropertyValue('--pointer-x'), y: element.style.getPropertyValue('--pointer-y') }));
    assert.deepEqual(glowAfter, { content: 'none', x: '', y: '' }, 'focus cards should not render or track pointer glow');
    const reducedContentOpacity = await page.evaluate(() => ({
      card: getComputedStyle(document.querySelector('.trending-top-card')).opacity,
      row: getComputedStyle(document.querySelector('.trending-repo-row')).opacity,
      periodIndicator: getComputedStyle(document.querySelector('.trending-period-indicator')).opacity,
    }));
    assert.deepEqual(reducedContentOpacity, { card: '1', row: '1', periodIndicator: '1' }, 'reduced motion must keep cards, rows, and controls visible');
    await page.mouse.move(0, 0);
    await page.screenshot({ path: path.join(focusScreenshots, 'github-trending-focus-1440.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 900 });
    const mobileBoxes = await page.locator('.trending-top-card').evaluateAll(elements => elements.map(element => element.getBoundingClientRect().y));
    assert(mobileBoxes[0] < mobileBoxes[1] && mobileBoxes[1] < mobileBoxes[2], 'top ranks should stack on mobile');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'three focus cards should not overflow at 390px');
    await page.screenshot({ path: path.join(focusScreenshots, 'github-trending-focus-390.png'), fullPage: true });
    await page.setViewportSize({ width: 720, height: 900 });
    const narrowDesktopBoxes = await page.locator('.trending-top-card').evaluateAll(elements => elements.map(element => ({ x: element.getBoundingClientRect().x, y: element.getBoundingClientRect().y })));
    assert(narrowDesktopBoxes.every(box => box.y === narrowDesktopBoxes[0].y) && narrowDesktopBoxes[0].x < narrowDesktopBoxes[1].x && narrowDesktopBoxes[1].x < narrowDesktopBoxes[2].x, '720px should retain three focus columns');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'three focus cards should not overflow at 720px');
    await page.screenshot({ path: path.join(focusScreenshots, 'github-trending-focus-720.png'), fullPage: true });
    weekly = snapshot('weekly', { ...weekly, fetched_at: '2026-09-29T00:01:00Z' });
    await page.reload();
    await expect(page.locator('.trending-top-card').first().locator('.trending-period-stat strong')).toHaveText('20');
    assert.equal(await page.locator('.trending-top-card .trending-progress-fill').first().evaluate(element => getComputedStyle(element).animationName), 'none', 'reduced motion should show final bars without animation');
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.setViewportSize({ width: 1440, height: 900 });
    weekly = snapshot('weekly');
    await page.reload();
    await page.getByRole('button', { name: '日榜' }).click();
    await expect(page.locator('.trending-repo')).toHaveCount(1);
    await expect(page.locator('.trending-repo').first()).toContainText('daily/today');
    await expect(page.getByLabel('语言（本榜内筛选）')).toHaveValue('');
    await expect(page.getByRole('searchbox', { name: '搜索仓库或简介' })).toHaveValue('');
    await page.getByRole('link', { name: '今日计划' }).click();
    await expect(page).toHaveURL(/\/admin\/plans$/);
    await page.goBack();
    await expect(page.getByRole('heading', { name: 'GitHub 热榜', exact: true }).last()).toBeVisible();
    await page.goForward();
    await expect(page).toHaveURL(/\/admin\/plans$/);
    await page.goBack();
    await expect(page.getByRole('heading', { name: 'GitHub 热榜', exact: true }).last()).toBeVisible();
    await page.reload();
    await expect(page.getByRole('button', { name: '周榜' })).toHaveAttribute('aria-pressed', 'true');

    await page.getByRole('button', { name: '手动刷新' }).click();
    await expect(page.getByRole('status')).toContainText('冷却');
    await expect(page.getByRole('button', { name: '手动刷新' })).toBeDisabled();
    await expect(page.getByRole('button', { name: '手动刷新' })).toBeEnabled({ timeout: 5000 });
    refresh = snapshot('weekly');
    postStatus = 503;
    await page.reload();
    await page.getByRole('button', { name: '手动刷新' }).click();
    await expect(page.getByRole('alert')).toContainText('榜单请求失败');
    await expect(page.locator('.trending-repo')).toHaveCount(3);
    postStatus = 401;
    await page.getByRole('button', { name: '手动刷新' }).click();
    await expect(page.getByText('管理员登录已失效。')).toBeVisible();
    await expect(page.locator('.trending-repo')).toHaveCount(0);
    postStatus = 200;
    weekly = snapshot('weekly', { is_stale: true, error: 'upstream_unavailable', retry_after_seconds: 90 });
    await page.reload();
    await expect(page.getByText(/缓存/)).toBeVisible();
    await expect(page.getByText('GitHub 上游暂不可用')).toBeVisible();
    await expect(page.getByText(/上游重试等待剩余/)).toBeVisible();
    weekly = snapshot('weekly', { status: 'unavailable', items: [], last_success_at: null, fetched_at: null, is_stale: true, error: 'upstream_unavailable' });
    await page.reload();
    await expect(page.getByText(/暂时无法获取榜单/)).toBeVisible();
    await expect(page.getByText('正在显示上次成功缓存')).toHaveCount(0);
    weekly = snapshot('weekly', { status: 'loading', items: [], last_success_at: null, fetched_at: null, is_stale: true, refreshing: true });
    await page.reload();
    await expect(page.getByText(/首次获取/)).toBeVisible();
    await expect(page.getByText('正在显示上次成功缓存')).toHaveCount(0);
    weekly = snapshot('weekly', { status: 'loading', items: [] });
    await page.reload();
    await expect(page.getByText(/本期榜单暂无项目/)).toBeVisible();
    getStatus = 401;
    await page.reload();
    await expect(page.getByText('管理员登录已失效。')).toBeVisible();
    await expect(page.getByRole('link', { name: '前往登录' })).toBeVisible();
    getStatus = 403;
    await page.reload();
    await expect(page.getByText('需要管理员权限。')).toBeVisible();
    getStatus = 200;

    weekly = snapshot('weekly');
    await page.reload();
    holdWeekly = true;
    await page.getByRole('button', { name: '日榜' }).click();
    await page.getByRole('button', { name: '周榜' }).click();
    await page.getByRole('button', { name: '日榜' }).click();
    if (releaseWeekly) releaseWeekly();
    await expect(page.locator('.trending-repo')).toHaveCount(1);
    await expect(page.locator('.trending-repo').first()).toContainText('daily/today');
    holdWeekly = false;
    await page.getByRole('button', { name: '周榜' }).click();
    await expect(page.locator('.trending-repo')).toHaveCount(3);

    const beforeSync = requests.length;
    weekly = snapshot('weekly', { items: [item(4, 'fresh/synced', 'Go', 'New server cache', 5, 2, 1)] });
    await page.clock.fastForward(60_000);
    await expect.poll(() => requests.length).toBeGreaterThan(beforeSync);
    await expect(page.locator('.trending-repo').first()).toContainText('fresh/synced');
    await expect(page.locator('.trending-repo')).toHaveCount(1);

    holdWeekly = true;
    releaseWeekly = undefined;
    heldProcessed = 0;
    weekly = snapshot('weekly', { items: [item(7, 'older/snapshot', 'Go', 'Older cache', 4, 1, 1)] });
    await page.clock.fastForward(60_000);
    await expect.poll(() => typeof releaseWeekly).toBe('function');
    refresh = snapshot('weekly', { items: [
      item(1, 'manual/newer', 'Rust', 'Manual result', 9, 8, 2),
      item(2, `owner/${'x'.repeat(150)}`, 'VeryLongLanguage'.repeat(30), 'A long repository description about useful open source work. '.repeat(7), Number.MAX_SAFE_INTEGER + 1, 0, 123456789012345),
    ] });
    await page.getByRole('button', { name: '手动刷新' }).click();
    await expect(page.locator('.trending-repo').first()).toContainText('manual/newer');
    releaseWeekly();
    await expect.poll(() => heldProcessed).toBe(1);
    await expect(page.locator('.trending-repo').first()).toContainText('manual/newer');
    holdWeekly = false;

    const screenshotDir = process.env.REACT_TRENDING_SCREENSHOT_DIR || path.join(process.cwd(), '.astra-luna/screenshots');
    fs.mkdirSync(screenshotDir, { recursive: true });
    const assertSectionFits = async width => {
      const section = await page.locator('.trending-page .admin-section').evaluate(element => ({
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
        scrollLeft: element.scrollLeft,
      }));
      assert(section.scrollWidth <= section.clientWidth + 1 && section.scrollLeft === 0,
        `expanded ${width}px section must not scroll horizontally (${JSON.stringify(section)})`);
    };
    await page.setViewportSize({ width: 390, height: 900 });
    await expect.poll(() => page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().right)).toBeLessThanOrEqual(0);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), '390px stress content must not overflow');
    await expect(page.locator('.trending-repo').nth(1).locator('.trending-secondary b').first()).toHaveText('—');
    const stressRow = page.locator('.trending-repo').nth(1);
    const disclosure = stressRow.locator('.trending-entry-toggle');
    await expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    await disclosure.focus();
    await page.keyboard.press('Enter');
    await expect(disclosure).toHaveAttribute('aria-expanded', 'true');
    const fullTextVisible = await stressRow.evaluate(element => {
      const details = element.querySelector('.trending-details');
      return [...details.querySelectorAll('strong, p, dd')].every(node => {
        const box = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return box.height > 0 && node.scrollHeight <= node.clientHeight + 1 && node.scrollWidth <= node.clientWidth + 1 && style.textOverflow !== 'ellipsis';
      });
    });
    assert(fullTextVisible, 'expanded repository name, description, and language must render without clipping');
    const topCardActionsClearName = await stressRow.evaluate(element => {
      const name = element.querySelector('.trending-repo-main h2').getBoundingClientRect();
      const actions = [...element.querySelectorAll('.trending-actions a, .trending-actions button')].map(node => node.getBoundingClientRect());
      return actions.every(box => box.width >= 44 && box.height >= 44 && (box.right <= name.left || box.left >= name.right || box.bottom <= name.top || box.top >= name.bottom));
    });
    assert(topCardActionsClearName, 'expanded 390px top-card actions must be usable without covering the repository name');
    await stressRow.getByRole('button', { name: /复制链接/ }).click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), `https://github.com/owner/${'x'.repeat(150)}`);
    await assertSectionFits(390);
    await disclosure.focus();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'expanded 390px content must not overflow');
    await page.screenshot({ path: path.join(screenshotDir, 'github-trending-expanded-390.png'), fullPage: true });
    await page.keyboard.press('Space');
    await expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    await stressRow.click();
    await expect(disclosure).toHaveAttribute('aria-expanded', 'true');
    await page.keyboard.press('Space');
    await expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    await disclosure.evaluate(element => element.blur());
    await page.mouse.move(0, 0);
    const collapsedHeight = await stressRow.evaluate(element => element.getBoundingClientRect().height);
    assert(collapsedHeight < 320, `unusually long repository values should not dominate a mobile page (${collapsedHeight}px)`);
    await page.screenshot({ path: path.join(screenshotDir, 'github-trending-stress-390.png'), fullPage: true });
    for (const width of [461, 719, 720, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await disclosure.focus();
      await page.keyboard.press('Enter');
      await expect(disclosure).toHaveAttribute('aria-expanded', 'true');
      const actionLayout = await stressRow.evaluate(element => {
        const name = element.querySelector('.trending-repo-main h2').getBoundingClientRect();
        const actions = [...element.querySelectorAll('.trending-actions a, .trending-actions button')].map(node => node.getBoundingClientRect());
        const card = element.getBoundingClientRect();
        return {
          clear: actions.every(box => box.right <= name.left || box.left >= name.right || box.bottom <= name.top || box.top >= name.bottom),
          tappable: actions.every(box => box.width >= 44 && box.height >= 44),
          rightGap: card.right - actions.at(-1).right,
          actionsAboveName: actions[0].top < name.top,
        };
      });
      assert(actionLayout.clear, `expanded ${width}px top-card actions must not cover the repository name`);
      assert(actionLayout.tappable, `expanded ${width}px top-card actions must retain 44px targets`);
      if (width >= 720) assert(actionLayout.rightGap > 0 && actionLayout.rightGap < 26 && actionLayout.actionsAboveName, `${width}px desktop actions should reveal at the top right`);
      await stressRow.getByRole('button', { name: /复制链接/ }).click();
      assert.equal(await page.evaluate(() => navigator.clipboard.readText()), `https://github.com/owner/${'x'.repeat(150)}`);
      await assertSectionFits(width);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `expanded ${width}px content must not overflow`);
      await page.evaluate(() => window.scrollTo(0, 0));
      const firstCardLeft = await page.locator('.trending-top-card').first().evaluate(element => element.getBoundingClientRect().left);
      assert(firstCardLeft >= 0, `expanded ${width}px layout must remain in the viewport (left: ${firstCardLeft}px)`);
      await page.screenshot({ path: path.join(screenshotDir, `github-trending-expanded-${width}.png`), fullPage: true });
      await disclosure.focus();
      await page.keyboard.press('Space');
      await expect(disclosure).toHaveAttribute('aria-expanded', 'false');
      await disclosure.evaluate(element => element.blur());
      await page.mouse.move(0, 0);
      if (width < 720) assert.equal(await stressRow.locator('.trending-actions').evaluate(element => getComputedStyle(element).display), 'none', `${width}px collapsed actions should leave no blank row`);
      else {
        const actionRow = stressRow.locator('.trending-actions');
        await expect.poll(() => actionRow.evaluate(element => getComputedStyle(element).opacity)).toBe('0');
        assert.equal(await actionRow.evaluate(element => getComputedStyle(element).transitionProperty), 'opacity', `${width}px desktop actions should fade`);
      }
    }
    weekly = snapshot('weekly');
    await page.reload();
    await expect(page.locator('.trending-repo')).toHaveCount(3);
    for (const width of [390, 719, 720, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      if (width < 880) await expect.poll(() => page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().right)).toBeLessThanOrEqual(0);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${width}px page must not overflow`);
      const firstToggle = page.locator('.trending-repo').first().locator('.trending-entry-toggle');
      await firstToggle.focus();
      const targets = await page.locator('.trending-repo').first().locator('.trending-actions a, .trending-actions button').evaluateAll(elements => elements.map(element => element.getBoundingClientRect().height));
      assert(targets.every(height => height >= (width === 390 ? 44 : 36)), `${width}px repository action targets should be comfortably tappable`);
      await firstToggle.evaluate(element => element.blur());
      if (width === 768) {
        const rowLayout = await page.locator('.trending-repo-row').first().evaluate(element => ({
          height: element.getBoundingClientRect().height,
          mainRight: element.querySelector('.trending-repo-main').getBoundingClientRect().right,
          metricsLeft: element.querySelector('.trending-row-period').getBoundingClientRect().left,
        }));
        assert(rowLayout.height < 145 && rowLayout.metricsLeft >= rowLayout.mainRight, '768px repository metrics should sit beside the text in a compact row');
      }
      await page.screenshot({ path: path.join(screenshotDir, `github-trending-${width}.png`), fullPage: true });
    }
    weekly = snapshot('weekly', { refreshing: true });
    await page.reload();
    await expect(page.getByText('后台刷新中…')).toBeVisible();
    failNextWeeklyGet = true;
    const beforeFailedPoll = requests.length;
    await page.clock.fastForward(3_000);
    await expect.poll(() => requests.length).toBeGreaterThan(beforeFailedPoll);
    await expect(page.getByRole('alert')).toContainText('榜单请求失败');
    weekly = snapshot('weekly', { items: [item(4, 'recovered/cache', 'Go', 'Recovered data', 5, 2, 1)] });
    await page.clock.fastForward(60_000);
    await expect(page.locator('.trending-repo').first()).toContainText('recovered/cache');

    failNextWeeklyGet = true;
    await page.clock.fastForward(60_000);
    await expect(page.getByRole('alert')).toContainText('榜单请求失败');
    refresh = snapshot('weekly', { items: [item(1, 'manual/cleared-error', 'Rust', 'Current data', 9, 8, 2)] });
    await page.getByRole('button', { name: '手动刷新' }).click();
    await expect(page.locator('.trending-repo').first()).toContainText('manual/cleared-error');
    await expect(page.getByRole('alert')).toHaveCount(0);
    refresh = snapshot('weekly', { refreshing: true });
    await page.getByRole('button', { name: '手动刷新' }).click();
    await expect(page.getByRole('status')).toContainText('刷新已开始');
    weekly = snapshot('weekly', { items: [item(2, 'poll/completed', 'Go', 'Completed refresh', 10, 2, 1)] });
    await page.clock.fastForward(3_000);
    await expect(page.locator('.trending-repo').first()).toContainText('poll/completed');
    await expect(page.getByText('刷新已开始，正在获取 GitHub 榜单。')).toHaveCount(0);
    const unknown = await page.goto(`${base}/admin/github-trending/unknown`);
    assert.equal(unknown.status(), 404);
    assert.deepEqual(errors, []);
    console.log('PASS: GitHub trending route, states, local filters, cache sync, race, safety and 390/768/1440 layouts');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
