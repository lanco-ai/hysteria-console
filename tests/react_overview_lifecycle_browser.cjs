// Explicit sibling scenarios: controlled HTTP replies exercise the compiled UI and timers.
const assert = require('node:assert/strict');
const { expect } = require('@playwright/test');
const baseUrl = process.env.PREVIEW_BASE_URL;
const boot = '**/api/v1/admin/overview-page';
const pollUrl = '**/api/v1/admin/overview';
const operation = '**/api/v1/admin/operations/*';
const row = page => page.locator('tr[data-user="demo_alex"]');
const copy = value => structuredClone(value);
const reply = (route, value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
const counters = data => ({ ts: '2026-07-18T12:00:00+08:00', total_used: data.cycle.total_used, users: data.users.map(({ user, tx, rx, used, total, percent, online, revision, disabled }) => ({ user, tx, rx, used, total, percent, online, revision, disabled })) });
const successful = (action, user = 'demo_alex') => ({ ok: true, action, user, day: null, disabled_until: '' });
async function setup(browser, baseline, { clock = false } = {}) {
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (response.status() >= 400 && !new URL(response.url()).pathname.startsWith('/api/v1/')) errors.push(`Unexpected resource status: ${response.status()}`);
  });
  await page.addInitScript(() => {
    Math.random = () => 0.5;
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => Boolean(window.__hidden) });
  });
  if (clock) {
    const time = new Date('2026-07-18T04:00:00Z');
    await page.clock.install({ time });
    await page.clock.pauseAt(time);
  }
  let data = copy(baseline), reads = 0;
  await page.route(boot, route => { reads++; return reply(route, data); });
  await page.route(pollUrl, route => reply(route, counters(data)));
  await page.route('**/api/v1/admin/reload-status', route => reply(route, { pending: false, xray: false, tuic: false }));
  await page.goto(`${baseUrl}/__react/admin`);
  await expect(row(page).getByRole('button', { name: '编辑套餐' })).toBeEnabled();
  return { page, context, errors, setData: next => { data = next; }, reads: () => reads };
}
async function clean(fixture) { assert.deepEqual(fixture.errors, []); await fixture.context.close(); }

async function initialStates(browser, baseline) {
  for (const kind of ['503', 'html', 'malformed', 'protocol', 'boolean', 'number', 'array', 'auth']) {
    const fixture = await setup(browser, baseline), { page } = fixture;
    const data = copy(baseline);
    if (kind === 'protocol') data.users[0].panel_url = 'javascript:alert(1)';
    if (kind === 'boolean') data.users[0].metered = 'false';
    if (kind === 'number') data.users[0].online = '3';
    if (kind === 'array') data.cycle = [];
    await page.route(boot, route => kind === '503' ? reply(route, { error: 'state_unavailable' }, 503) : kind === 'auth' ? reply(route, { error: 'login_required' }, 401) : kind === 'html' ? route.fulfill({ contentType: 'text/html', body: '<h1>login</h1>' }) : reply(route, kind === 'malformed' ? {} : data));
    await page.reload();
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(page.locator('.users-table')).toHaveCount(0);
    await expect(page.getByText('暂无用户，使用下方表单创建第一个用户')).toHaveCount(0);
    if (kind === 'auth') await expect(page.getByRole('link', { name: '管理员登录' })).toHaveAttribute('href', '/login');
    else {
      await page.unroute(boot); await page.route(boot, route => reply(route, baseline));
      await page.getByRole('button', { name: '刷新核对' }).click();
      await expect(row(page)).toBeVisible();
    }
    await clean(fixture);
  }
  const fixture = await setup(browser, baseline), { page } = fixture;
  for (const value of ['__proto__', 'constructor', 'toString', '<img src=x onerror=alert(1)>']) {
    await page.goto(`${baseUrl}/__react/admin?msg=${encodeURIComponent(value)}`);
    await expect(page.locator('.flash')).toHaveText(value);
    assert.equal(await page.locator('.flash img, .flash script').count(), 0);
  }
  await page.goto(`${baseUrl}/__react/admin#main-content`);
  await expect(page.locator('#main-content')).toBeFocused();
  const empty = copy(baseline); empty.users = [];
  fixture.setData(empty); await page.reload();
  await expect(page.getByText('暂无用户，使用下方表单创建第一个用户')).toBeVisible();
  await clean(fixture);
}

async function draftFailures(browser, baseline) {
  const fixture = await setup(browser, baseline), { page } = fixture;
  let posts = 0;
  await page.route('**/api/v1/admin/users/create', route => { posts++; return reply(route, { ok: false, error: 'validation_error', code: 'err:username_invalid', field_id: 'create-user' }, 422); });
  await page.locator('.create-toggle').click();
  await page.locator('#create-user').fill('bad user');
  await page.locator('#create-note').fill('retain me');
  await page.locator('#create-panel-password').fill('secret-to-clear');
  await page.getByRole('button', { name: '创建', exact: true }).click();
  await expect(page.locator('#create-user')).toBeFocused();
  await expect(page.locator('#create-panel-password')).toHaveValue('');
  await expect(page.locator('#create-note')).toHaveValue('retain me');
  assert.equal(posts, 1);
  await page.locator('.create-toggle').click();
  await row(page).getByRole('button', { name: '编辑套餐' }).click();
  await page.locator('#edit-note').fill('edit retained');
  await page.locator('#edit-panel-password').fill('secret-to-clear');
  let originalRevision;
  await page.route('**/api/v1/admin/users/update', route => {
    posts++; originalRevision = new URLSearchParams(route.request().postData()).get('user_revision');
    return reply(route, { ok: false, error: 'revision_conflict' }, 409);
  });
  await page.getByRole('button', { name: '保存更改' }).click();
  await expect(page.locator('#edit-error')).toContainText('配置已更改');
  await expect(page.locator('#edit-panel-password')).toHaveValue('');
  await expect(page.locator('#edit-note')).toHaveValue('edit retained');
  assert.equal(originalRevision, baseline.users.find(item => item.user === 'demo_alex').revision);
  const changed = copy(baseline); changed.users.find(item => item.user === 'demo_alex').revision = 'external-revision';
  fixture.setData(changed);
  await page.getByRole('dialog').getByRole('button', { name: '刷新核对' }).click();
  await expect(page.getByText('此用户配置已更改；草稿保留原版本，保存时可能冲突')).toBeVisible();
  await expect(page.locator('#edit-user-revision')).toHaveValue(originalRevision);
  await expect(page.locator('#edit-note')).toHaveValue('edit retained');
  await page.keyboard.press('Escape');
  await expect(row(page).getByRole('button', { name: '编辑套餐' })).toBeFocused();
  assert.equal(posts, 2, 'recovery never repeats mutation');
  await clean(fixture);
}

async function writeOutcomes(browser, baseline) {
  for (const kind of ['unknown500', 'html200', 'malformed200', 'wrong-action', 'missing', 'auth', 'saved-stale', 'err:rotated_retry', 'err:rotated_pending', 'err:rotated_static_pending', 'err:deleted_retry']) {
    const fixture = await setup(browser, baseline), { page } = fixture;
    page.on('dialog', dialog => dialog.accept());
    let posts = 0;
    const credential = kind.startsWith('err:');
    await page.route(operation, route => {
      posts++;
      if (kind === 'html200') return route.fulfill({ contentType: 'text/html', body: 'login' });
      if (kind === 'unknown500') return reply(route, { error: 'state_unavailable' }, 503);
      if (kind === 'malformed200') return reply(route, { ok: true });
      if (kind === 'wrong-action') return reply(route, successful('refresh-usage'));
      if (kind === 'missing') return reply(route, { ok: false, error: 'user_not_found' }, 404);
      if (kind === 'auth') return reply(route, { error: 'login_required' }, 401);
      if (credential) return reply(route, { ok: true, action: kind === 'err:deleted_retry' ? 'delete' : 'rotate-token', user: 'demo_alex', code: kind });
      return reply(route, successful('reset-usage'));
    });
    if (kind === 'saved-stale') await page.route(boot, route => reply(route, { error: 'state_unavailable' }, 503));
    await row(page).getByRole('button', { name: credential ? kind === 'err:deleted_retry' ? '删除' : '重置订阅' : '清流量', exact: true }).click();
    if (credential) {
      const phrases = new Map([['err:rotated_retry', '未能确认所有旧连接'], ['err:rotated_pending', 'Hysteria 连接断开请求将延迟复核'], ['err:rotated_static_pending', '受影响的静态代理'], ['err:deleted_retry', '删除请求已安全记录']]);
      await expect(page.locator('.flash').first()).toContainText(phrases.get(kind));
      await expect(row(page)).toBeVisible();
      await expect(row(page).getByRole('button', { name: '清流量', exact: true })).toBeEnabled();
      assert(fixture.reads() >= 2, 'pending uses canonical snapshot');
    } else if (kind === 'auth') {
      await expect(page.locator('.users-table')).toHaveCount(0);
    } else {
      await expect(row(page).getByRole('button', { name: '清流量', exact: true })).toBeDisabled();
      await expect(row(page).getByRole('button', { name: '复制 demo_alex 的专属面板链接', exact: true })).toBeDisabled();
      assert.equal(await row(page).locator('.link-row a').first().getAttribute('href'), null);
      if (kind === 'saved-stale') await expect(page.locator('.flash').first()).toContainText('已清除用户');
      else if (kind !== 'missing') await expect(page.locator('.flash').first()).toContainText('操作结果尚未确认，请刷新核对后再操作');
      await page.unroute(boot); await page.route(boot, route => reply(route, baseline));
      await page.getByRole('button', { name: '刷新核对' }).click();
      await expect(row(page).getByRole('button', { name: '清流量', exact: true })).toBeEnabled();
    }
    assert.equal(posts, 1);
    await clean(fixture);
  }
}

async function pollingAndRaces(browser, baseline) {
  const fixture = await setup(browser, baseline, { clock: true }), { page } = fixture;
  let polls = 0, fail = false, payload = counters(baseline);
  await page.route(pollUrl, route => { polls++; return reply(route, fail ? {} : payload, fail ? 503 : 200); });
  await page.clock.runFor(29_999); assert.equal(polls, 0);
  await page.clock.runFor(1); await expect.poll(() => polls).toBe(1);
  await expect(page.locator('[data-role="admin-poll-status"]')).toHaveText('自动更新 · 30 s');
  const spark = await row(page).locator('svg.spark').innerHTML(), link = await row(page).locator('.link-row a').first().getAttribute('href');
  payload.users.find(item => item.user === 'demo_alex').used = 123;
  await page.clock.runFor(30_000); await expect.poll(() => polls).toBe(2);
  await expect(row(page).locator('[data-role="used"]')).toHaveText('123.00 B');
  assert.equal(await row(page).locator('svg.spark').innerHTML(), spark);
  assert.equal(await row(page).locator('.link-row a').first().getAttribute('href'), link);
  fail = true;
  await page.clock.runFor(30_000); await expect.poll(() => polls).toBe(3);
  await expect(page.locator('[data-role="admin-poll-status"]')).toHaveText('更新失败 · 点击重试');
  for (const delay of [62_000, 122_000, 240_000]) {
    const count = polls;
    await page.clock.runFor(delay - 1); assert.equal(polls, count, 'backoff lower bound');
    await page.clock.runFor(1); await expect.poll(() => polls).toBe(count + 1);
    await expect(page.locator('[data-role="admin-poll-status"]')).toHaveText('更新失败 · 点击重试');
  }
  fail = false;
  await page.locator('[data-role="admin-poll-status"]').click(); await expect.poll(() => polls).toBe(7);
  await expect(page.locator('[data-role="admin-poll-status"]')).toHaveText('自动更新 · 30 s');
  await page.evaluate(() => { window.__hidden = true; document.dispatchEvent(new Event('visibilitychange')); });
  await page.clock.runFor(300_000); assert.equal(polls, 7);
  await page.evaluate(() => { window.__hidden = false; document.dispatchEvent(new Event('visibilitychange')); });
  await expect.poll(() => polls).toBe(8);
  await expect(page.locator('[data-role="admin-poll-status"]')).toHaveText('自动更新 · 30 s');
  await page.locator('.create-toggle').click(); await page.locator('#create-note').fill('poll must keep draft'); await page.locator('#cycle-day').fill('9');
  payload.users.find(item => item.user === 'demo_alex').revision = 'external-change';
  await page.clock.runFor(30_000);
  await expect(page.getByRole('alert')).toContainText('用户列表或配置已更改');
  await expect(page.locator('#create-note')).toHaveValue('poll must keep draft'); await expect(page.locator('#cycle-day')).toHaveValue('9');
  const count = polls; await page.clock.runFor(300_000); assert.equal(polls, count);
  const bootstrapReads = fixture.reads();
  await page.evaluate(() => { window.__hidden = true; document.dispatchEvent(new Event('visibilitychange')); });
  await page.evaluate(() => { window.__hidden = false; document.dispatchEvent(new Event('visibilitychange')); });
  assert.equal(fixture.reads(), bootstrapReads, 'visibility cannot automatically recover external configuration conflicts');
  await expect(row(page).getByRole('button', { name: '清流量', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '刷新核对' }).click();
  await expect(row(page).getByRole('button', { name: '清流量', exact: true })).toBeEnabled();
  await expect(page.locator('#cycle-day')).toHaveValue('9');
  payload = counters(baseline); payload.users.pop();
  await page.locator('[data-role="admin-poll-status"]').click(); await expect(page.getByRole('alert')).toContainText('用户列表或配置已更改');
  await clean(fixture);

  const race = await setup(browser, baseline, { clock: true }), racePage = race.page;
  // Force transport to deliver a late reply even after abort to test epoch ownership.
  await racePage.evaluate(() => { const original = window.fetch; window.fetch = (input, options) => original(input, String(input).endsWith('/overview') ? { ...options, signal: undefined } : options); });
  let delayed;
  await racePage.route(pollUrl, route => { delayed = route; });
  await racePage.clock.runFor(30_000); await expect.poll(() => Boolean(delayed)).toBe(true);
  const canonical = copy(baseline); canonical.users.find(item => item.user === 'demo_alex').used = 321; race.setData(canonical);
  let posts = 0, release;
  await racePage.route(operation, route => { posts++; release = route; });
  racePage.on('dialog', dialog => dialog.accept());
  await racePage.evaluate(() => {
    window.confirm = () => true;
    const buttons = [...document.querySelectorAll('tr[data-user="demo_alex"] .user-action')];
    buttons[0].click(); buttons[1].click(); document.querySelector('.reset-all-button').click();
    document.querySelector('.cycle-config-form').requestSubmit();
  });
  await expect.poll(() => posts).toBe(1);
  await expect(racePage.locator('.reset-all-button')).toBeDisabled();
  await expect(racePage.locator('.cycle-config-form button')).toBeDisabled();
  await expect(racePage.locator('.create-form button[type="submit"]')).toBeDisabled();
  await reply(release, successful('reset-usage'));
  await expect(row(racePage).locator('[data-role="used"]')).toHaveText('321.00 B');
  await reply(delayed, counters(baseline));
  await expect(row(racePage).locator('[data-role="used"]')).toHaveText('321.00 B');
  assert.equal(posts, 1);
  await clean(race);
}

async function lifecycle(browser, baseline) {
  const fixture = await setup(browser, baseline, { clock: true }), { page } = fixture;
  let posts = 0, watcherReads = 0;
  await page.route(operation, route => { posts++; return reply(route, successful('reset-usage')); });
  await page.route('**/api/v1/admin/reload-status', route => { watcherReads++; return reply(route, { pending: true, xray: true, tuic: false }); });
  page.on('dialog', dialog => dialog.accept());
  await row(page).getByRole('button', { name: '清流量', exact: true }).click();
  await expect(row(page).getByRole('button', { name: '清流量', exact: true })).toBeEnabled();
  await page.clock.runFor(599); assert.equal(watcherReads, 0);
  await page.clock.runFor(1); await expect.poll(() => watcherReads).toBe(1);
  await expect(page.getByText('配置已保存，正在等待服务重载…')).toBeVisible();
  await page.clock.runFor(849); assert.equal(watcherReads, 1);
  await page.clock.runFor(1); await expect.poll(() => watcherReads).toBe(2);
  await page.clock.runFor(9_000); await expect(page.getByText('配置已保存，服务重载仍待确认')).toBeVisible();
  const reads = watcherReads; await page.clock.runFor(20_000); assert.equal(watcherReads, reads);
  assert.equal(posts, 1, 'watcher is read only');
  let pending;
  await page.route(operation, route => { posts++; pending = route; });
  await row(page).getByRole('button', { name: '清流量', exact: true }).click(); await expect.poll(() => Boolean(pending)).toBe(true);
  await page.clock.runFor(10_000);
  await expect(page.locator('.flash').first()).toContainText('操作结果尚未确认');
  await page.getByRole('button', { name: '刷新核对' }).click();
  await expect(row(page).getByRole('button', { name: '清流量', exact: true })).toBeEnabled();
  await reply(pending, successful('reset-usage')).catch(() => {});
  assert.equal(posts, 2);
  pending = undefined;
  await row(page).getByRole('button', { name: '清流量', exact: true }).click(); await expect.poll(() => Boolean(pending)).toBe(true);
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })));
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
  await expect(row(page).getByRole('button', { name: '清流量', exact: true })).toBeEnabled();
  await reply(pending, successful('reset-usage')).catch(() => {});
  await expect(page.locator('.flash').first()).toContainText('操作结果尚未确认');
  assert.equal(posts, 3, 'BFCache never replays a POST');
  pending = undefined;
  await row(page).getByRole('button', { name: '清流量', exact: true }).click();
  await expect.poll(() => Boolean(pending)).toBe(true);
  await page.goto(`${baseUrl}/__react/admin/logs`);
  await reply(pending, successful('reset-usage')).catch(() => {});
  await page.clock.runFor(300_000);
  assert.equal(posts, 4);
  await clean(fixture);
}

async function lateReadsAndClipboard(browser, baseline) {
  const fixture = await setup(browser, baseline, { clock: true }), { page } = fixture;
  let delayed, polls = 0;
  await page.route(pollUrl, route => { polls++; delayed = route; });
  await page.clock.runFor(30_000); await expect.poll(() => polls).toBe(1);
  await page.locator('[data-role="admin-poll-status"]').click();
  await page.clock.runFor(9_999); assert.equal(polls, 1, 'only one read is in flight');
  await page.clock.runFor(1);
  await expect(page.locator('[data-role="admin-poll-status"]')).toHaveText('更新失败 · 点击重试');
  await reply(delayed, counters(baseline)).catch(() => {});
  await page.unroute(pollUrl);
  await page.route(pollUrl, route => reply(route, { ts: '', total_used: 1, users: 'bad' }));
  await page.locator('[data-role="admin-poll-status"]').click();
  await expect(page.locator('[data-role="admin-poll-status"]')).toHaveText('更新失败 · 点击重试');
  await expect(row(page)).toBeVisible();
  await page.route(boot, route => { delayed = route; });
  await page.reload();
  await expect(page.getByRole('status', { name: '正在加载总览…' })).toBeVisible();
  assert.equal(await page.getByText('正在加载总览…', { exact: true }).count(), 0, 'loading state has no visible text node');
  await page.clock.runFor(10_000);
  await expect(page.getByRole('alert')).toContainText('总览加载失败');
  await reply(delayed, baseline).catch(() => {});
  await expect(page.locator('.users-table')).toHaveCount(0);
  await page.unroute(boot); await page.route(boot, route => reply(route, baseline));
  await page.getByRole('button', { name: '刷新核对' }).click();
  await expect(row(page)).toBeVisible();
  // Late bootstrap during a BFCache restore cannot beat its newer snapshot.
  await page.evaluate(() => { const original = window.fetch; window.fetch = (input, options) => original(input, String(input).endsWith('/overview-page') ? { ...options, signal: undefined } : options); });
  delayed = undefined;
  await page.route(boot, route => { delayed = route; });
  await page.evaluate(() => { window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })); window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })); });
  await expect.poll(() => Boolean(delayed)).toBe(true);
  const old = delayed;
  const next = copy(baseline); next.users.find(item => item.user === 'demo_alex').used = 555;
  await page.unroute(boot); await page.route(boot, route => reply(route, next));
  await page.evaluate(() => { window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })); window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })); });
  await expect(row(page).locator('[data-role="used"]')).toHaveText('555.00 B');
  await reply(old, baseline).catch(() => {});
  await expect(row(page).locator('[data-role="used"]')).toHaveText('555.00 B');
  await page.evaluate(() => {
    navigator.clipboard.writeText = async () => { throw new Error('denied'); };
    document.execCommand = () => true;
  });
  await row(page).getByRole('button', { name: '复制 demo_alex 的订阅链接', exact: true }).click();
  await expect(row(page).getByRole('status')).toHaveText('已复制');
  assert.equal(await page.locator('body > textarea').count(), 0);
  await page.evaluate(() => { document.execCommand = () => false; });
  await row(page).getByRole('button', { name: '复制 demo_alex 的订阅链接', exact: true }).click();
  await expect(row(page).getByRole('status')).toContainText('复制失败');
  await row(page).getByRole('button', { name: '编辑套餐' }).click();
  await page.getByRole('button', { name: '保存更改' }).focus();
  await page.keyboard.press('Tab');
  assert(await page.getByRole('dialog').evaluate(node => node.contains(document.activeElement)), 'native dialog traps keyboard focus');
  await page.keyboard.press('Escape');
  await expect(row(page).getByRole('button', { name: '编辑套餐' })).toBeFocused();
  await clean(fixture);
}

async function pagehidePreservesConfirmedWrite(browser, baseline) {
  // Run the unconfirmed control first, then the confirmed-write/refetch race.
  // The latter must fail if pagehide treats every pending read as an unknown POST.
  for (const confirmed of [false, true]) {
    const fixture = await setup(browser, baseline, { clock: true }), { page } = fixture;
    try {
      const posts = [];
      let pendingPost, pendingCanonical, bootstrapReads = 0;
      page.on('request', request => {
        if (request.method() === 'POST') posts.push(new URL(request.url()).pathname);
      });
      await page.route(operation, route => { pendingPost = route; });
      const recovered = copy(baseline);
      recovered.users.find(item => item.user === 'demo_alex').used = 777;
      await page.route(boot, route => {
        bootstrapReads++;
        if (confirmed && bootstrapReads === 1) {
          pendingCanonical = route;
          return;
        }
        return reply(route, recovered);
      });
      page.on('dialog', dialog => dialog.accept());
      await row(page).getByRole('button', { name: '清流量', exact: true }).click();
      await expect.poll(() => Boolean(pendingPost)).toBe(true);
      if (confirmed) {
        await reply(pendingPost, successful('reset-usage'));
        await expect.poll(() => Boolean(pendingCanonical)).toBe(true);
        await expect(page.locator('.flash').first()).toHaveText('已清除用户本周期已用流量：demo_alex');
      } else {
        await expect(page.locator('.flash').first()).toHaveText('正在提交…');
      }
      await expect(row(page).getByRole('button', { name: '清流量', exact: true })).toBeDisabled();
      assert.equal(bootstrapReads, confirmed ? 1 : 0, 'only confirmed writes start canonical refetch');

      await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })));
      const expectedFeedback = confirmed
        ? '已清除用户本周期已用流量：demo_alex'
        : '操作结果尚未确认，请刷新核对后再操作';
      await expect(page.locator('.flash').first()).toHaveText(expectedFeedback);
      assert.equal(bootstrapReads, confirmed ? 1 : 0, 'pagehide must not start a recovery request');

      await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
      await expect.poll(() => bootstrapReads).toBe(confirmed ? 2 : 1);
      await expect(row(page).locator('[data-role="used"]')).toHaveText('777.00 B');
      await expect(row(page).getByRole('button', { name: '清流量', exact: true })).toBeEnabled();
      await expect(page.locator('.flash').first()).toHaveText(expectedFeedback);

      // Old replies after recovery neither replace its snapshot nor replay a write.
      if (confirmed) await reply(pendingCanonical, baseline).catch(() => {});
      else await reply(pendingPost, successful('reset-usage')).catch(() => {});
      await page.clock.runFor(10_000);
      await expect(row(page).locator('[data-role="used"]')).toHaveText('777.00 B');
      await expect(page.locator('.flash').first()).toHaveText(expectedFeedback);
      assert.equal(bootstrapReads, confirmed ? 2 : 1);
      assert.deepEqual(posts, ['/api/v1/admin/operations/reset-usage'], 'BFCache recovery only reads; it never resubmits POST');
      assert.deepEqual(fixture.errors, []);
    } finally {
      await fixture.context.close();
    }
  }
}

async function bfcacheRecoveryLocksStaleSnapshot(browser, baseline) {
  for (const outcome of ['success', 'auth', 'failure', 'hidden-success']) {
    const fixture = await setup(browser, baseline, { clock: true }), { page } = fixture;
    try {
      let pendingBootstrap, posts = 0, bootstrapReads = 0;
      await page.route(operation, route => { posts++; return reply(route, successful('reset-usage')); });
      await page.route(boot, route => { bootstrapReads++; pendingBootstrap = route; });

      await page.evaluate(() => {
        window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));
        window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
      });
      await expect.poll(() => Boolean(pendingBootstrap)).toBe(true);

      const account = row(page);
      await expect(account.getByRole('button', { name: '清流量', exact: true })).toBeDisabled();
      await expect(page.locator('.reset-all-button')).toBeDisabled();
      await expect(page.locator('.cycle-config-form button')).toBeDisabled();
      await expect(account.getByRole('button', { name: '复制 demo_alex 的专属面板链接', exact: true })).toBeDisabled();
      assert.equal(await account.locator('.link-row a').first().getAttribute('href'), null);
      await account.getByRole('button', { name: '清流量', exact: true }).click({ force: true });
      await page.locator('.reset-all-button').click({ force: true });
      assert.equal(posts, 0, 'BFCache recovery gate blocks writes before canonical data arrives');

      if (outcome === 'hidden-success') {
        await page.evaluate(() => { window.__hidden = true; document.dispatchEvent(new Event('visibilitychange')); });
        await page.evaluate(() => { window.__hidden = false; document.dispatchEvent(new Event('visibilitychange')); });
        assert.equal(bootstrapReads, 1, 'visibility changes do not duplicate canonical recovery reads');
      }
      if (outcome === 'success' || outcome === 'hidden-success') {
        const recovered = copy(baseline);
        recovered.users.find(item => item.user === 'demo_alex').used = 888;
        await reply(pendingBootstrap, recovered).catch(() => {});
        await expect(account.getByRole('button', { name: '清流量', exact: true })).toBeEnabled();
        await expect(account.locator('[data-role="used"]')).toHaveText('888.00 B');
        assert.equal(bootstrapReads, 1, 'canonical recovery completes without duplicate reads');
        await expect(account.getByRole('link', { name: '面板', exact: true })).toHaveAttribute('href', baseline.users.find(item => item.user === 'demo_alex').panel_url);
      } else if (outcome === 'auth') {
        await reply(pendingBootstrap, { error: 'login_required' }, 401);
        await expect(page.locator('.users-table')).toHaveCount(0);
        await expect(page.getByRole('link', { name: '管理员登录' })).toHaveAttribute('href', '/login');
      } else {
        await reply(pendingBootstrap, { error: 'state_unavailable' }, 503);
        await expect(page.getByRole('alert')).toContainText('数据尚未更新');
        await expect(account.getByRole('button', { name: '清流量', exact: true })).toBeDisabled();
        assert.equal(await account.locator('.link-row a').first().getAttribute('href'), null);
      }
      assert.equal(posts, 0);
      assert.deepEqual(fixture.errors, []);
    } finally {
      await fixture.context.close();
    }
  }
}

module.exports = async function overviewLifecycle(browser, baseline) {
  await initialStates(browser, baseline);
  console.log('Overview initial failure and feedback states passed');
  await draftFailures(browser, baseline);
  console.log('Overview draft conflict recovery passed');
  await writeOutcomes(browser, baseline);
  console.log('Overview mutation outcomes passed');
  await pollingAndRaces(browser, baseline);
  console.log('Overview polling and mutation races passed');
  await lifecycle(browser, baseline);
  await pagehidePreservesConfirmedWrite(browser, baseline);
  await bfcacheRecoveryLocksStaleSnapshot(browser, baseline);
  await lateReadsAndClipboard(browser, baseline);
};
