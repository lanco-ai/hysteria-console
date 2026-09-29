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
    const desktopLayout = await page.evaluate(() => {
      const pageBox = document.querySelector('.trending-page').getBoundingClientRect();
      const contentBox = document.querySelector('.content').getBoundingClientRect();
      const title = document.querySelector('.trending-intro h2');
      const row = document.querySelector('.trending-repo').getBoundingClientRect();
      const action = document.querySelector('.trending-actions a').getBoundingClientRect();
      return { left: pageBox.left, right: pageBox.right, width: pageBox.width, contentLeft: contentBox.left, contentRight: contentBox.right, titleSize: parseFloat(getComputedStyle(title).fontSize), rowHeight: row.height, actionHeight: action.height };
    });
    assert(desktopLayout.width <= 1200 && Math.abs((desktopLayout.left - desktopLayout.contentLeft) - (desktopLayout.contentRight - desktopLayout.right)) <= 2, 'desktop content should be centered and readable');
    assert(desktopLayout.titleSize >= 36, 'page heading should establish clear hierarchy');
    assert(desktopLayout.rowHeight <= 125, 'desktop repository rows should remain compact');
    assert(desktopLayout.actionHeight >= 36, 'repository actions should offer generous click targets');
    await expect(page.locator('.trending-repo').first()).toContainText('#2');
    await expect(page.locator('.trending-repo').first()).toContainText('1,200');
    await expect(page.locator('.trending-repo').first()).toContainText('0');
    await expect(page.locator('.trending-repo').first().locator('.trending-metrics dd').nth(1)).toHaveText('0');
    await expect(page.locator('.trending-repo').nth(1)).toContainText('—');
    await expect(page.locator('.trending-repo').nth(1)).toContainText('<script>alert(1)</script> safe text');
    assert.equal(await page.locator('.trending-repo script').count(), 0);
    await expect(page.locator('.trending-repo').nth(2).getByRole('link', { name: '在 GitHub 查看' })).toHaveCount(0);
    await expect(page.locator('.trending-repo').first().getByRole('link', { name: '在 GitHub 查看' })).toHaveAttribute('rel', 'noopener noreferrer');
    await page.locator('.trending-repo').first().getByRole('button', { name: '复制链接' }).click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'https://github.com/octocat/hello-world');
    await expect(page.getByRole('status')).toContainText('链接已复制');

    const beforeFilter = requests.length;
    await page.getByRole('searchbox', { name: '搜索仓库或简介' }).fill('safe text');
    await expect(page.locator('.trending-repo')).toHaveCount(1);
    await page.getByRole('searchbox', { name: '搜索仓库或简介' }).fill('');
    await page.getByLabel('语言（本榜内筛选）').selectOption('TypeScript');
    await expect(page.locator('.trending-repo')).toHaveCount(1);
    await expect(page.locator('.trending-filters small')).toContainText('本榜内筛选');
    assert.equal(requests.length, beforeFilter, 'local filters must not issue API requests');
    await page.getByRole('searchbox', { name: '搜索仓库或简介' }).fill('nothing-matches');
    await expect(page.getByText('本榜内没有符合筛选条件的仓库')).toBeVisible();
    await page.getByRole('button', { name: '清除筛选' }).click();
    await expect(page.getByRole('searchbox', { name: '搜索仓库或简介' })).toHaveValue('');
    await expect(page.getByLabel('语言（本榜内筛选）')).toHaveValue('');
    await expect(page.locator('.trending-repo')).toHaveCount(3);
    assert.equal(requests.length, beforeFilter, 'clearing both filters must stay local');
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
    await page.setViewportSize({ width: 390, height: 900 });
    await expect.poll(() => page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().right)).toBeLessThanOrEqual(0);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), '390px stress content must not overflow');
    await expect(page.locator('.trending-repo').nth(1).locator('.trending-metrics dd').first()).toHaveText('—');
    const stressRow = page.locator('.trending-repo').nth(1);
    await expect(page.locator('.trending-repo').first().getByRole('button', { name: /展开详情/ })).toHaveCount(0);
    const disclosure = stressRow.getByRole('button', { name: /展开详情/ });
    await expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    await disclosure.focus();
    await page.keyboard.press('Enter');
    await expect(stressRow.getByRole('button', { name: /收起详情/ })).toHaveAttribute('aria-expanded', 'true');
    const fullTextVisible = await stressRow.evaluate(element => {
      const main = element.querySelector('.trending-repo-main');
      return [...main.querySelectorAll('h2, p, .trending-language')].every(node => {
        const box = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return box.height > 0 && node.scrollHeight <= node.clientHeight + 1 && node.scrollWidth <= node.clientWidth + 1 && style.webkitLineClamp === 'none' && style.textOverflow !== 'ellipsis';
      });
    });
    assert(fullTextVisible, 'expanded repository name, description, and language must render without clipping');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'expanded 390px content must not overflow');
    await page.screenshot({ path: path.join(screenshotDir, 'github-trending-expanded-390.png'), fullPage: true });
    await page.keyboard.press('Space');
    await expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    await disclosure.click();
    await expect(stressRow.getByRole('button', { name: /收起详情/ })).toHaveAttribute('aria-expanded', 'true');
    await page.keyboard.press('Space');
    await expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    assert(await page.locator('.trending-repo').nth(1).evaluate(element => element.getBoundingClientRect().height) < 260, 'unusually long repository values should not dominate a mobile page');
    await page.screenshot({ path: path.join(screenshotDir, 'github-trending-stress-390.png'), fullPage: true });
    weekly = snapshot('weekly');
    await page.reload();
    await expect(page.locator('.trending-repo')).toHaveCount(3);
    for (const width of [390, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      if (width < 880) await expect.poll(() => page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().right)).toBeLessThanOrEqual(0);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${width}px page must not overflow`);
      const targets = await page.locator('.trending-repo').first().locator('.trending-actions a, .trending-actions button').evaluateAll(elements => elements.map(element => element.getBoundingClientRect().height));
      assert(targets.every(height => height >= (width === 390 ? 44 : 36)), `${width}px repository action targets should be comfortably tappable`);
      if (width === 390) assert(await page.locator('.trending-repo').first().evaluate(element => element.getBoundingClientRect().top) < 460, 'mobile toolbar should expose the first repository without excess header height');
      if (width === 768) {
        const rowLayout = await page.locator('.trending-repo').first().evaluate(element => ({
          height: element.getBoundingClientRect().height,
          mainRight: element.querySelector('.trending-repo-main').getBoundingClientRect().right,
          metricsLeft: element.querySelector('.trending-metrics').getBoundingClientRect().left,
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
