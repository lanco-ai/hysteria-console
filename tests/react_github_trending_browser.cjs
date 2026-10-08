const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

const item = (rank, name, language, description, total, period, forks, htmlUrl = `https://github.com/${name}`) => ({
  source_rank: rank, full_name: name, html_url: htmlUrl, description, language,
  stars_total: total, stars_period: period, forks_count: forks,
});
const weeklyItems = [
  item(1, 'octocat/hello-world', 'TypeScript', 'A welcoming repository', 1200, 40, 14),
  item(2, 'openai/sample', 'Python', '<script>alert(1)</script> safe text', null, 20, 0),
  item(3, 'danger/example', null, null, 1, null, null, 'https://evil.example/steal'),
  item(4, 'rust/fast', 'Rust', 'Fast tools', 900, 10, 3),
  item(5, 'go/simple', 'Go', 'Simple services', 300, 0, 2),
];
const dailyItems = [item(3, 'daily/today', 'Rust', 'Today only', 12, 3, 1)];
const successAt = new Date(Date.now() - 5 * 60_000).toISOString();
const snapshot = (period, overrides = {}) => ({
  period, source: 'GitHub Trending', source_url: `https://github.com/trending?since=${period}`,
  fetched_at: successAt, last_success_at: successAt,
  is_stale: false, refreshing: false, items: period === 'weekly' ? weeklyItems : dailyItems,
  error: null, status: 'ready', cooldown_seconds: 0, retry_after_seconds: 0,
  ...overrides,
});
const unavailable = { fetched_at: null, last_success_at: null, items: [], status: 'unavailable', error: 'upstream_unavailable' };
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAEUlEQVR4nGMIdL2CFTEMLQkAPBVagW2PDnoAAAAASUVORK5CYII=', 'base64');

function fixture() {
  const state = {
    weekly: snapshot('weekly'), daily: snapshot('daily'), refresh: snapshot('weekly'),
    getStatus: 200, postStatus: 200, holdWeekly: false, release: undefined, heldProcessed: 0, requests: [], posts: [],
  };
  const handler = async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/v1/github-trending/avatar/')) return route.fulfill({ contentType: 'image/png', body: PNG });
    state.requests.push(`${request.method()} ${url.pathname}${url.search}`);
    if (request.method() === 'POST') {
      state.posts.push(request.postDataJSON());
      return route.fulfill({ status: state.postStatus, contentType: 'application/json', body: JSON.stringify(state.refresh) });
    }
    if (state.getStatus !== 200) return route.fulfill({ status: state.getStatus, contentType: 'application/json', body: '{"error":"storage_unavailable"}' });
    const period = url.searchParams.get('period') || 'weekly';
    const payload = period === 'weekly' ? state.weekly : state.daily;
    if (period === 'weekly' && state.holdWeekly) {
      await new Promise(resolve => { state.release = resolve; });
      try { await route.fulfill({ contentType: 'application/json', body: JSON.stringify(payload) }); } catch { /* aborted by a newer request */ } finally { state.heldProcessed += 1; }
      return;
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(payload) });
  };
  return { state, handler };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const base = process.env.PREVIEW_BASE_URL;
  const errors = [];
  try {
    // ---- Anonymous visitors read the public board. ----
    const visitorContext = await browser.newContext({ viewport: { width: 1440, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await visitorContext.newPage();
    page.on('pageerror', error => errors.push(error.message));
    const { state, handler } = fixture();
    await page.route('**/api/v1/github-trending**', handler);
    await page.clock.install();
    await page.goto(`${base}/__react/?view=trending`);
    await expect(page.getByRole('heading', { name: 'GitHub 热榜', level: 1 })).toBeVisible();
    await expect(page.locator('nav[aria-label="主导航"] a[aria-current="page"]')).toHaveText('开源发现');
    await expect(page.locator('.trending-period a[aria-current="page"]')).toHaveText('周榜');
    await expect(page.getByText('5 分钟前更新')).toBeVisible();
    await expect(page.getByRole('button', { name: '刷新榜单' })).toHaveCount(0);
    await expect(page.locator('.trending-featured .trending-entry')).toHaveCount(3);
    await expect(page.locator('.trending-list .trending-entry')).toHaveCount(2);
    await expect(page.locator('.trending-entry').first().locator('img')).toHaveAttribute('src', '/api/v1/github-trending/avatar/octocat');
    const ratios = await page.locator('.trending-entry').evaluateAll(entries => entries.map(entry => entry.querySelector('.trending-progress-fill')?.dataset.ratio ?? null));
    assert.deepEqual(ratios, ['1', '0.5', null, '0.25', '0']);
    await expect(page.locator('.trending-entry').nth(1)).toContainText('<script>alert(1)</script> safe text');
    assert.equal(await page.locator('.trending-entry script').count(), 0);
    await expect(page.locator('.trending-entry').nth(1)).toContainText('总 Star —');
    const repoLink = page.getByRole('link', { name: 'octocat/hello-world', exact: true });
    await expect(repoLink).toHaveAttribute('href', 'https://github.com/octocat/hello-world');
    await expect(repoLink).toHaveAttribute('target', '_blank');
    await expect(repoLink).toHaveAttribute('rel', 'noopener noreferrer');
    await expect(page.locator('.trending-entry').nth(2).locator('h2 a')).toHaveCount(0);
    await expect(page.locator('.trending-entry').nth(2)).toContainText('链接不可用');
    await page.getByRole('button', { name: '复制链接：octocat/hello-world' }).click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'https://github.com/octocat/hello-world');
    await expect(page.getByRole('status').filter({ hasText: '链接已复制' })).toBeVisible();

    // Filters stay local, keep their value across periods, and can be cleared.
    const beforeFilter = state.requests.length;
    await page.getByRole('searchbox', { name: '搜索仓库或简介' }).fill('fast');
    await expect(page.locator('.trending-featured')).toHaveCount(0);
    await expect(page.locator('.trending-list .trending-entry')).toHaveCount(1);
    await expect(page.locator('.trending-count')).toHaveText('1 / 5 个项目');
    await page.getByRole('searchbox', { name: '搜索仓库或简介' }).fill('');
    await page.getByLabel('按语言筛选').selectOption('Python');
    await expect(page.locator('.trending-entry')).toHaveCount(1);
    await expect(page.locator('.trending-entry').first()).toContainText('openai/sample');
    await page.getByLabel('按语言筛选').selectOption('');
    await page.getByRole('searchbox', { name: '搜索仓库或简介' }).fill('today');
    await expect(page.getByText('没有符合筛选条件的项目')).toBeVisible();
    assert.equal(state.requests.length, beforeFilter, 'local filters must not issue API requests');
    await page.getByRole('link', { name: '日榜', exact: true }).click();
    await expect(page).toHaveURL(`${base}/__react/?view=trending&period=daily`);
    await expect(page.locator('.trending-period a[aria-current="page"]')).toHaveText('日榜');
    await expect(page.getByRole('searchbox', { name: '搜索仓库或简介' })).toHaveValue('today');
    await expect(page.locator('.trending-entry')).toHaveCount(1);
    await expect(page.locator('.trending-entry').first()).toContainText('daily/today');
    await page.goBack();
    await expect(page.locator('.trending-period a[aria-current="page"]')).toHaveText('周榜');
    await expect(page.getByText('没有符合筛选条件的项目')).toBeVisible();
    await page.getByRole('button', { name: '清除筛选' }).click();
    await expect(page.getByRole('searchbox', { name: '搜索仓库或简介' })).toHaveValue('');
    await expect(page.locator('.trending-entry')).toHaveCount(5);
    await page.goto(`${base}/__react/?view=trending&period=daily`);
    await expect(page.locator('.trending-period a[aria-current="page"]')).toHaveText('日榜');
    await expect(page.locator('.trending-entry').first()).toContainText('daily/today');
    await page.goto(`${base}/__react/?view=trending`);

    // Operational details stay with administrators.
    state.weekly = snapshot('weekly', { error: 'rate_limited', retry_after_seconds: 30, cooldown_seconds: 10 });
    await page.reload();
    await expect(page.locator('.trending-entry')).toHaveCount(5);
    await expect(page.getByText('GitHub 上游限流')).toHaveCount(0);
    await expect(page.getByText(/上游重试等待/)).toHaveCount(0);
    await expect(page.getByText(/冷却/)).toHaveCount(0);

    // Cache, refresh and failure states.
    state.weekly = snapshot('weekly', { is_stale: true });
    await page.reload();
    await expect(page.getByText(/榜单暂未更新到最新/)).toBeVisible();
    state.weekly = snapshot('weekly', { refreshing: true });
    await page.reload();
    await expect(page.getByText('正在更新榜单…')).toBeVisible();
    state.weekly = snapshot('weekly', { items: [item(4, 'fresh/after-refresh', 'Go', 'Refreshed', 5, 2, 1)] });
    await page.clock.fastForward(3_000);
    await expect(page.locator('.trending-entry').first()).toContainText('fresh/after-refresh');
    await expect(page.getByText('正在更新榜单…')).toHaveCount(0);
    state.weekly = snapshot('weekly', unavailable);
    await page.reload();
    await expect(page.getByText('暂时无法获取榜单，请稍后再试。')).toBeVisible();
    state.weekly = snapshot('weekly', { ...unavailable, status: 'loading', error: null });
    await page.reload();
    await expect(page.getByText(/首次获取/)).toBeVisible();
    state.weekly = snapshot('weekly', { items: [] });
    await page.reload();
    await expect(page.getByText('本期榜单暂无项目。')).toBeVisible();
    state.weekly = snapshot('weekly');
    state.getStatus = 503;
    await page.reload();
    await expect(page.getByRole('alert')).toContainText('榜单请求失败');
    state.getStatus = 200;
    await page.getByRole('button', { name: '重试' }).click();
    await expect(page.locator('.trending-entry')).toHaveCount(5);
    await expect(page.getByRole('alert')).toHaveCount(0);

    // The board follows the server cache once a minute.
    const beforeSync = state.requests.length;
    state.weekly = snapshot('weekly', { items: [item(4, 'fresh/synced', 'Go', 'New server cache', 5, 2, 1)] });
    await page.clock.fastForward(60_000);
    await expect.poll(() => state.requests.length).toBeGreaterThan(beforeSync);
    await expect(page.locator('.trending-entry').first()).toContainText('fresh/synced');

    // A slow older response never replaces the board the visitor switched to.
    state.weekly = snapshot('weekly');
    await page.reload();
    await expect(page.locator('.trending-entry')).toHaveCount(5);
    state.holdWeekly = true;
    await page.getByRole('link', { name: '日榜', exact: true }).click();
    await expect(page.locator('.trending-entry').first()).toContainText('daily/today');
    await page.getByRole('link', { name: '周榜', exact: true }).click();
    await expect.poll(() => typeof state.release).toBe('function');
    await page.getByRole('link', { name: '日榜', exact: true }).click();
    state.release();
    await expect.poll(() => state.heldProcessed).toBe(1);
    await expect(page.locator('.trending-entry')).toHaveCount(1);
    await expect(page.locator('.trending-entry').first()).toContainText('daily/today');
    state.holdWeekly = false;

    // Responsive layout: no page overflow; the top three swipe sideways on phones.
    const screenshots = process.env.REACT_TRENDING_SCREENSHOT_DIR;
    if (screenshots) fs.mkdirSync(screenshots, { recursive: true });
    await page.goto(`${base}/__react/?view=trending`);
    for (const width of [390, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(page.locator('.trending-featured .trending-entry')).toHaveCount(3);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${width}px must not overflow`);
      const featured = await page.locator('.trending-featured').evaluate(list => ({
        scrolls: list.scrollWidth > list.clientWidth,
        tops: Array.from(list.children, entry => Math.round(entry.getBoundingClientRect().top)),
      }));
      if (width === 390) assert.equal(featured.scrolls, true, 'phones swipe through the top three');
      if (width === 1440) assert.equal(new Set(featured.tops).size, 1, 'desktop shows the top three in one row');
      if (screenshots) await page.screenshot({ path: path.join(screenshots, `github-trending-${width}.png`), fullPage: true });
    }
    await visitorContext.close();

    // ---- Administrators also get the manual refresh. ----
    const adminContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await adminContext.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const admin = await adminContext.newPage();
    admin.on('pageerror', error => errors.push(error.message));
    const managed = fixture();
    await admin.route('**/api/v1/github-trending**', managed.handler);
    await admin.clock.install();
    managed.state.weekly = snapshot('weekly', { error: 'upstream_unavailable', is_stale: true });
    await admin.goto(`${base}/__react/?view=trending`);
    const refreshButton = admin.getByRole('button', { name: '刷新榜单' });
    await expect(refreshButton).toBeEnabled();
    await expect(admin.getByText('GitHub 上游暂不可用')).toBeVisible();
    managed.state.refresh = snapshot('weekly', { refreshing: true });
    await refreshButton.click();
    await expect(admin.getByRole('status').filter({ hasText: '刷新已开始' })).toBeVisible();
    assert.deepEqual(managed.state.posts, [{ period: 'weekly' }]);
    managed.state.weekly = snapshot('weekly', { items: [item(2, 'poll/completed', 'Go', 'Completed refresh', 10, 2, 1)] });
    await admin.clock.fastForward(3_000);
    await expect(admin.locator('.trending-entry').first()).toContainText('poll/completed');
    await expect(admin.getByText('刷新已开始，正在获取 GitHub 榜单。')).toHaveCount(0);
    managed.state.refresh = snapshot('weekly', { cooldown_seconds: 2 });
    await refreshButton.click();
    await expect(admin.getByText(/手动刷新冷却剩余/)).toBeVisible();
    await expect(refreshButton).toBeDisabled();
    await admin.clock.fastForward(2_500);
    await expect(refreshButton).toBeEnabled();
    await admin.getByRole('link', { name: '日榜', exact: true }).click();
    await expect(admin.locator('.trending-entry').first()).toContainText('daily/today');
    managed.state.postStatus = 401;
    await refreshButton.click();
    assert.deepEqual(managed.state.posts.at(-1), { period: 'daily' });
    await expect(admin.getByRole('alert')).toContainText('管理员登录已失效');
    await expect(admin.locator('.trending-entry').first()).toContainText('daily/today');
    await adminContext.close();

    assert.deepEqual(errors, []);
    console.log('PASS: public GitHub trending board, local filters, period links, cache states, polling, race safety, admin refresh and 390/768/1440 layouts');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
