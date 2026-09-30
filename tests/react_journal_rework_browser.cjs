const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const base = process.env.PREVIEW_BASE_URL;
  const failures = [];
  async function scenario(name, run, date = '2026-09-29T17:00:00Z') {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, timezoneId: 'America/Los_Angeles' });
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const page = await context.newPage();
    await page.clock.install({ time: new Date(date) });
    try { await run(page, context); console.log(`PASS: ${name}`); }
    catch (error) { failures.push(name); console.error(`FAIL: ${name}: ${error.stack}`); }
    finally { await context.close(); }
  }
  async function saved(page, text) {
    await page.getByLabel('记录内容').fill(text);
    await page.getByLabel('记录内容').press('Control+Enter');
    await expect(page.locator('#journal-autosave-state')).toHaveText('已自动保存');
  }
  async function records(context, q) { return (await (await context.request.get(`${base}/api/journal?q=${encodeURIComponent(q)}`)).json()).items; }
  try {
    await scenario('failed autosave protects text and navigation while closed', async page => {
      await page.goto(`${base}/admin/services`);
      await page.getByRole('link', { name: '今日计划' }).click();
      await page.getByRole('navigation', { name: '今日页面内容' }).getByRole('link', { name: '时间线' }).click();
      let writes = 0;
      await page.route('**/api/journal', async route => {
        if (route.request().method() === 'POST') { writes++; await route.fulfill({ status: 401, json: { error: 'login_required' } }); }
        else await route.continue();
      });
      await page.getByLabel('记录内容').fill('Keep failed autosave draft');
      await page.clock.runFor(1300);
      await expect(page.getByRole('alert')).toContainText('登录已失效');
      await page.clock.runFor(20_000); assert.equal(writes, 1, 'failure must stop automatic retries');
      assert.equal(await page.evaluate(() => { const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented; }), true);
      await page.getByRole('button', { name: '关闭记录面板' }).click();
      page.once('dialog', dialog => dialog.dismiss());
      await page.getByRole('link', { name: '服务中心' }).first().click();
      await expect(page).toHaveURL(/\/admin\/plans/);
      await page.getByRole('navigation', { name: '今日页面内容' }).getByRole('link', { name: '时间线' }).click();
      await expect(page.getByLabel('记录内容')).toHaveValue('Keep failed autosave draft');
      await expect(page.locator('#journal-autosave-state')).toHaveText('未保存，文字已保留');
    });

    await scenario('typing during a pending create is serialized into the same entry', async (page, context) => {
      await page.goto(`${base}/admin/plans#daily-timeline`);
      let release; let posts = 0; let puts = 0;
      await page.route('**/api/journal', async route => {
        if (route.request().method() !== 'POST') return route.continue();
        posts++;
        await new Promise(resolve => { release = resolve; });
        await route.continue();
      });
      await page.route('**/api/journal/*', async route => { if (route.request().method() === 'PUT') puts++; await route.continue(); });
      await page.getByLabel('记录内容').fill('Pending autosave first part');
      await page.getByLabel('记录内容').press('Control+Enter');
      await expect(page.locator('#journal-autosave-state')).toHaveText('正在保存…');
      await expect(page.getByLabel('记录内容')).toBeEnabled();
      await page.getByLabel('记录内容').fill('Pending autosave latest part');
      await page.getByLabel('记录内容').press('Control+Enter');
      try { assert.equal(posts, 1); release(); await expect(page.locator('#journal-autosave-state')).toHaveText('等待自动保存…'); await page.clock.runFor(1300); await expect(page.locator('#journal-autosave-state')).toHaveText('已自动保存'); }
      finally { if (release) release(); }
      const items = await records(context, 'Pending autosave');
      assert.equal(items.length, 1); assert.equal(items[0].body, 'Pending autosave latest part'); assert.equal(posts, 1); assert.equal(puts, 1);
    });

    await scenario('lost create response retries its key without duplicate records', async (page, context) => {
      await page.goto(`${base}/admin/plans#daily-timeline`);
      let dropped = false; const keys = [];
      await page.route('**/api/journal', async route => {
        if (route.request().method() !== 'POST') return route.continue();
        keys.push(route.request().headers()['idempotency-key']);
        if (!dropped) { dropped = true; await route.fetch(); await route.abort('failed'); }
        else await route.continue();
      });
      await page.getByLabel('记录内容').fill('Lost response original text');
      await page.getByLabel('记录内容').press('Control+Enter');
      await expect(page.getByRole('alert')).toBeVisible();
      await page.getByLabel('记录内容').fill('Lost response continued text');
      await page.getByRole('button', { name: '重试保存' }).click();
      await expect(page.locator('#journal-autosave-state')).toHaveText('等待自动保存…');
      await page.clock.runFor(1300);
      await expect(page.locator('#journal-autosave-state')).toHaveText('已自动保存');
      const items = await records(context, 'Lost response');
      assert.equal(items.length, 1); assert.equal(items[0].body, 'Lost response continued text'); assert.equal(keys.length, 2); assert(keys[0]); assert.equal(keys[0], keys[1]);
    });

    await scenario('lost update response is reconciled without overwriting a newer revision', async (page, context) => {
      await page.goto(`${base}/admin/plans#daily-timeline`);
      await saved(page, 'Reconciled initial text');
      let dropped = false;
      await page.route('**/api/journal/*', async route => {
        if (route.request().method() === 'PUT' && !dropped) { dropped = true; await route.fetch(); await route.abort('failed'); }
        else await route.continue();
      });
      await saved(page, 'Reconciled updated text');
      const [record] = await records(context, 'Reconciled');
      assert.equal(record.revision, 2);
      const { id, revision, local_date: _date, created_at: _created, updated_at: _updated, chat_source: _source, ...draft } = record;
      assert((await context.request.put(`${base}/api/journal/${id}`, { data: { ...draft, body: 'Other device edit', revision } })).ok());
      await page.getByLabel('记录内容').fill('Reconciled conflicting draft');
      await page.getByLabel('记录内容').press('Control+Enter');
      await expect(page.getByRole('alert')).toContainText('其他设备修改');
      await expect(page.getByLabel('记录内容')).toHaveValue('Reconciled conflicting draft');
      assert.equal((await records(context, 'Other device edit'))[0].revision, 3);
      page.once('dialog', dialog => dialog.dismiss());
      await page.getByRole('button', { name: '重新读取', exact: true }).click();
      await expect(page.getByLabel('记录内容')).toHaveValue('Reconciled conflicting draft');
      page.once('dialog', dialog => dialog.accept());
      await page.getByRole('button', { name: '重新读取', exact: true }).click();
      await expect(page.getByLabel('记录内容')).toHaveValue('');
      await expect(page.locator('.journal-timeline')).toContainText('Other device edit');
      await page.locator('.journal-timeline article').filter({ hasText: 'Other device edit' }).getByRole('button', { name: /^编辑 / }).click();
      await saved(page, 'Reconciled after explicit reload');
      assert.equal((await records(context, 'Reconciled after explicit'))[0].revision, 4);
    });

    await scenario('retrying a lost create cannot overwrite another device edit', async (page, context) => {
      await page.goto(`${base}/admin/plans#daily-timeline`);
      let dropped = false;
      await page.route('**/api/journal', async route => {
        if (route.request().method() === 'POST' && !dropped) { dropped = true; await route.fetch(); await route.abort('failed'); }
        else await route.continue();
      });
      await page.getByLabel('记录内容').fill('Create lost before another device edit');
      await page.getByLabel('记录内容').press('Control+Enter');
      await expect(page.getByRole('alert')).toBeVisible();
      const [record] = await records(context, 'Create lost before another');
      const { id, revision, local_date: _date, created_at: _created, updated_at: _updated, chat_source: _source, ...draft } = record;
      assert((await context.request.put(`${base}/api/journal/${id}`, { data: { ...draft, body: 'Another device changed the lost create', revision } })).ok());
      await page.getByRole('button', { name: '重试保存' }).click();
      await expect(page.getByRole('alert')).toContainText('其他设备修改');
      await page.clock.runFor(3000);
      await expect(page.getByLabel('记录内容')).toHaveValue('Create lost before another device edit');
      const [remote] = await records(context, 'Another device changed the lost create');
      assert.equal(remote.id, id); assert.equal(remote.revision, 2);
    });

    await scenario('IME composition and blank input never submit partial entries', async page => {
      await page.goto(`${base}/admin/plans#daily-timeline`);
      let posts = 0;
      await page.route('**/api/journal', async route => { if (route.request().method() === 'POST') posts++; await route.continue(); });
      const box = page.getByLabel('记录内容');
      await box.fill('  '); await page.clock.runFor(3000); assert.equal(posts, 0);
      await box.dispatchEvent('compositionstart'); await box.fill('拼音输入尚未结束');
      await page.clock.runFor(3000); assert.equal(posts, 0);
      await box.dispatchEvent('compositionend'); await page.clock.runFor(1300);
      await expect(page.locator('#journal-autosave-state')).toHaveText('已自动保存'); assert.equal(posts, 1);
    });

    await scenario('blanking a saved entry preserves its stored content', async (page, context) => {
      await page.goto(`${base}/admin/plans#daily-timeline`);
      await saved(page, 'Blanking keeps the stored entry');
      await page.getByLabel('记录内容').fill(''); await page.clock.runFor(1300);
      await expect(page.getByRole('alert')).toContainText('原记录已保留');
      assert.equal((await records(context, 'Blanking keeps'))[0].body, 'Blanking keeps the stored entry');
      await saved(page, 'Blanking replaced with real content');
    });

    await scenario('failed draft keeps its original day and category when filters change', async (page, context) => {
      await page.goto(`${base}/admin/plans#daily-timeline`);
      await page.getByLabel('记录日期').fill('2026-09-21');
      await page.getByLabel('筛选分类').selectOption('paper');
      let failing = true;
      await page.route('**/api/journal', async route => {
        if (failing && route.request().method() === 'POST') await route.fulfill({ status: 503, json: { error: 'journal_unavailable' } });
        else await route.continue();
      });
      await page.getByLabel('记录内容').fill('Bound to original date and category'); await page.getByLabel('记录内容').press('Control+Enter');
      await expect(page.getByRole('alert')).toBeVisible();
      await page.getByLabel('记录日期').fill('2026-09-22'); await page.getByLabel('筛选分类').selectOption('life');
      await expect(page.getByLabel('记录内容')).toHaveValue('Bound to original date and category');
      await expect(page.locator('.journal-inline-state')).toContainText('2026-09-21 · 论文阅读');
      failing = false; await page.getByRole('button', { name: '重试保存' }).click(); await expect(page.locator('#journal-autosave-state')).toHaveText('已自动保存');
      const [record] = await records(context, 'Bound to original'); assert.equal(record.local_date, '2026-09-21'); assert.equal(record.kind, 'paper');
      await page.getByRole('button', { name: '另记一条' }).click(); await saved(page, 'Bound to new selected day');
      assert.equal((await records(context, 'Bound to new'))[0].local_date, '2026-09-22');
    });

    await scenario('past weekly recap belongs to the selected week', async (page, context) => {
      await page.goto(`${base}/admin/plans#daily-review`);
      await page.getByRole('button', { name: '上周' }).click();
      await saved(page, 'Past selected week direct recap');
      const [record] = await records(context, 'Past selected week');
      assert.equal(record.kind, 'weekly_review'); assert.equal(record.local_date, '2026-09-21');
      await expect(page.locator('.journal-counts')).toContainText('每周回顾：1');
      await page.getByRole('button', { name: '上周' }).click(); await expect(page.getByLabel('记录内容')).toHaveValue('');
    });

    await scenario('late old-date reads cannot replace a newer selection', async page => {
      await page.goto(`${base}/admin/plans#daily-timeline`);
      await expect(page.getByText('正在读取记录…')).toHaveCount(0);
      let releaseOld;
      await page.route('**/api/journal?*', async route => {
        if (new URL(route.request().url()).searchParams.get('date') === '2026-11-10') {
          await new Promise(resolve => { releaseOld = resolve; });
          await route.fulfill({ json: { items: [] } }).catch(() => {});
        } else await route.continue();
      });
      await page.getByLabel('记录日期').fill('2026-11-10'); await expect.poll(() => Boolean(releaseOld)).toBe(true);
      await page.getByLabel('记录日期').fill('2026-11-11'); await saved(page, 'New selection survives old read');
      releaseOld(); await page.clock.runFor(100);
      await expect(page.getByLabel('记录日期')).toHaveValue('2026-11-11'); await expect(page.getByLabel('记录内容')).toHaveValue('New selection survives old read');
      await expect(page.locator('#journal-autosave-state')).toHaveText('已自动保存');
    });

    await scenario('failed reads hide stale records and leave direct typing available', async page => {
      await page.goto(`${base}/admin/plans#daily-timeline`); await saved(page, 'Earlier reader entry');
      await page.getByRole('button', { name: '另记一条' }).click(); await expect(page.locator('.journal-timeline')).toContainText('Earlier reader entry');
      await page.route('**/api/journal?*', route => route.fulfill({ status: 503, json: { error: 'journal_unavailable' } }));
      await page.getByLabel('记录日期').fill('2026-11-12');
      await expect(page.getByRole('alert')).toContainText('记录读取失败'); await expect(page.locator('.journal-timeline')).toHaveCount(0);
      await expect(page.getByLabel('记录内容')).toBeEnabled();
      await page.getByLabel('记录内容').fill('Draft during read outage');
      await expect(page.getByLabel('记录内容')).toHaveValue('Draft during read outage');
    });

    await scenario('weekly navigation hides old counts during a delayed failure', async page => {
      await page.goto(`${base}/admin/plans#daily-review`); await saved(page, 'Current week only direct recap');
      await expect(page.locator('.journal-counts')).toContainText('每周回顾：1');
      let release;
      await page.route('**/api/journal/summary?*', async route => {
        if (new URL(route.request().url()).searchParams.get('week_start') !== '2026-12-28') return route.continue();
        await new Promise(resolve => { release = resolve; });
        await route.fulfill({ status: 503, json: { error: 'journal_unavailable' } });
      });
      await page.getByRole('button', { name: '上周' }).click();
      try { await expect(page.getByText('正在读取本周记录…')).toBeVisible(); await expect(page.locator('.journal-counts')).toHaveCount(0); }
      finally { await expect.poll(() => Boolean(release)).toBe(true); release(); }
      await expect(page.getByText('本周记录读取失败')).toBeVisible(); await expect(page.locator('.journal-counts')).toHaveCount(0);
    }, '2027-01-05T17:00:00Z');
  } finally { await browser.close(); }
  assert.deepEqual(failures, []);
})().catch(error => { console.error(error); process.exit(1); });
