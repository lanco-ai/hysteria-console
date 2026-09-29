const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const base = process.env.PREVIEW_BASE_URL;
  const failures = [];
  async function scenario(name, run) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, timezoneId: 'America/Los_Angeles' });
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const page = await context.newPage();
    try { await run(page); console.log(`PASS: ${name}`); }
    catch (error) { failures.push(name); console.error(`FAIL: ${name}: ${error.message}`); }
    finally { await context.close(); }
  }
  try {
    await scenario('failed journal draft guards navigation', async page => {
      await page.goto(`${base}/admin/services`);
      await page.getByRole('link', { name: '今日计划' }).click();
      await expect(page).toHaveURL(/\/admin\/plans$/);
      await page.getByLabel('记录内容').fill('Keep this failed draft');
      await page.route('**/api/journal', async route => {
        if (route.request().method() === 'POST') await route.fulfill({ status: 409, json: { error: 'revision_conflict' } });
        else await route.continue();
      });
      await page.getByRole('button', { name: '保存记录' }).click();
      await expect(page.getByRole('alert')).toContainText('草稿已保留');
      assert.equal(await page.evaluate(() => {
        const event = new Event('beforeunload', { cancelable: true });
        window.dispatchEvent(event);
        return event.defaultPrevented;
      }), true);
      page.once('dialog', dialog => dialog.dismiss());
      await page.getByRole('link', { name: '服务中心' }).first().click();
      await expect(page).toHaveURL(/\/admin\/plans$/);
      await expect(page.getByLabel('记录内容')).toHaveValue('Keep this failed draft');
      page.once('dialog', dialog => dialog.dismiss());
      await page.goBack();
      await expect(page).toHaveURL(/\/admin\/plans$/);
      await expect(page.getByLabel('记录内容')).toHaveValue('Keep this failed draft');
    });

    await scenario('save disables edits while request is pending', async page => {
      await page.goto(`${base}/admin/plans`);
      await page.getByLabel('记录内容').fill('Submitted text');
      let release;
      await page.route('**/api/journal', async route => {
        if (route.request().method() !== 'POST') return route.continue();
        await new Promise(resolve => { release = () => resolve(); });
        await route.continue();
      });
      await page.getByRole('button', { name: '保存记录' }).click();
      try {
        await expect(page.getByLabel('记录内容')).toBeDisabled();
        await expect(page.getByLabel('开始时间')).toBeDisabled();
      } finally { if (release) release(); await expect(page.locator('.journal-message[role="status"]')).toContainText('记录已保存'); }
      await expect(page.locator('.journal-message[role="status"]')).toContainText('记录已保存');
    });

    await scenario('automatic quick-note time refreshes at save', async page => {
      await page.clock.install({ time: new Date('2026-09-29T17:00:00Z') });
      await page.goto(`${base}/admin/plans`);
      await expect(page.getByLabel('开始时间')).toHaveValue('2026-09-29T10:00');
      await page.clock.setFixedTime(new Date('2026-09-30T17:00:00Z'));
      let savedTime = '';
      await page.route('**/api/journal', async route => {
        if (route.request().method() === 'POST') savedTime = route.request().postDataJSON().occurred_at;
        await route.continue();
      });
      await page.getByLabel('记录内容').fill('Note after overnight open');
      await page.getByRole('button', { name: '保存记录' }).click();
      await expect(page.locator('.journal-message[role="status"]')).toContainText('记录已保存');
      assert.equal(savedTime, '2026-09-30T17:00:00.000Z');
    });

    await scenario('past weekly review belongs to selected week', async page => {
      await page.clock.install({ time: new Date('2026-09-29T17:00:00Z') });
      await page.goto(`${base}/admin/plans`);
      await page.getByRole('navigation', { name: '今日页面内容' }).getByRole('link', { name: '回顾' }).click();
      await page.getByRole('button', { name: '上周' }).click();
      await page.getByRole('button', { name: '写每周回顾' }).click();
      await expect(page.getByLabel('开始时间')).toHaveValue(/^2026-09-21T/);
      await page.getByLabel('记录内容').fill('Last week review');
      await page.getByRole('button', { name: '保存记录' }).click();
      await expect(page.locator('.journal-counts')).toContainText('每周回顾：1');
      await page.getByRole('button', { name: '上周' }).click();
      await page.getByRole('button', { name: '写每周回顾' }).click();
      await expect(page.getByLabel('开始时间')).toHaveValue(/^2026-09-14T/);
    });

    await scenario('late old-date read cannot replace saved new-date timeline', async page => {
      await page.clock.install({ time: new Date('2026-11-10T17:00:00Z') });
      await page.goto(`${base}/admin/plans`);
      await expect(page.getByText('正在读取记录…')).toHaveCount(0);
      let releaseOld;
      await page.route('**/api/journal?*', async route => {
        const date = new URL(route.request().url()).searchParams.get('date');
        if (date === '2026-11-10') {
          await new Promise(resolve => { releaseOld = resolve; });
        }
        await route.continue();
      });
      try {
        const newDateResponse = page.waitForResponse(response => response.url().includes('/api/journal?date=2026-11-11'));
        await page.getByLabel('开始时间').fill('2026-11-11T19:00');
        await page.getByLabel('记录内容').fill('New date survives old read');
        await page.getByRole('button', { name: '保存记录' }).click();
        await expect(page.getByLabel('记录日期')).toHaveValue('2026-11-11');
        await newDateResponse;
        await expect.poll(() => Boolean(releaseOld)).toBe(true);
        const oldResponse = page.waitForResponse(response => response.url().includes('/api/journal?date=2026-11-10'));
        releaseOld();
        await oldResponse;
        await expect(page.locator('.journal-timeline')).toContainText('New date survives old read');
        await page.getByLabel('搜索记录').fill('Second same-date note');
        await page.getByLabel('开始时间').fill('2026-11-11T20:00');
        await page.getByLabel('记录内容').fill('Second same-date note');
        await page.getByRole('button', { name: '保存记录' }).click();
        await expect(page.locator('.journal-timeline')).toContainText('Second same-date note');
      } finally { if (releaseOld) releaseOld(); }
    });

    await scenario('automatic time visibly updates after overnight open', async page => {
      await page.clock.install({ time: new Date('2026-09-29T17:00:00Z') });
      await page.goto(`${base}/admin/plans`);
      await expect(page.getByLabel('开始时间')).toHaveValue('2026-09-29T10:00');
      await expect(page.getByText('自动使用保存时的当前时间')).toBeVisible();
      await page.clock.setFixedTime(new Date('2026-09-30T17:00:00Z'));
      await page.clock.runFor(30_000);
      await expect(page.getByLabel('开始时间')).toHaveValue('2026-09-30T10:00');
      await page.getByLabel('开始时间').fill('2026-09-28T08:00');
      await page.clock.setFixedTime(new Date('2026-10-01T17:00:00Z'));
      await page.clock.runFor(30_000);
      await expect(page.getByLabel('开始时间')).toHaveValue('2026-09-28T08:00');
    });

    await scenario('weekly navigation hides old counts during delayed failure', async page => {
      await page.clock.install({ time: new Date('2027-01-05T17:00:00Z') });
      await page.goto(`${base}/admin/plans`);
      await page.getByLabel('记录内容').fill('Current week only');
      await page.getByRole('button', { name: '保存记录' }).click();
      await page.getByRole('navigation', { name: '今日页面内容' }).getByRole('link', { name: '回顾' }).click();
      await expect(page.locator('.journal-counts')).toContainText('生活：1');
      let releaseSummary;
      await page.route('**/api/journal/summary?*', async route => {
        const week = new URL(route.request().url()).searchParams.get('week_start');
        if (week === '2026-12-28') {
          await new Promise(resolve => { releaseSummary = resolve; });
          await route.fulfill({ status: 503, json: { error: 'journal_unavailable' } });
        } else await route.continue();
      });
      await page.getByRole('button', { name: '上周' }).click();
      try {
        await expect(page.locator('.journal-week-nav strong')).toHaveText('2026-12-28 — 2027-01-03');
        await expect(page.getByText('正在读取本周记录…')).toBeVisible();
        await expect(page.locator('.journal-counts')).toHaveCount(0);
      } finally { if (releaseSummary) releaseSummary(); }
      await expect(page.getByText('本周记录读取失败')).toBeVisible();
      await expect(page.locator('.journal-counts')).toHaveCount(0);
      await expect(page.locator('.journal-week-nav strong')).toHaveText('2026-12-28 — 2027-01-03');
    });

    await scenario('failed date and search reads hide stale records and preserve draft', async page => {
      await page.clock.install({ time: new Date('2027-04-10T17:00:00Z') });
      await page.goto(`${base}/admin/plans`);
      await page.getByText('更多记录选项').click();
      await page.getByLabel('记录标题').fill('Old day entry');
      await page.getByLabel('记录内容').fill('Saved on April 10');
      await page.getByRole('button', { name: '保存记录' }).click();
      await expect(page.locator('.journal-timeline')).toContainText('Old day entry');
      await page.getByLabel('记录内容').fill('Unsaved draft must remain');

      let failDate = true;
      let failSearch = false;
      await page.route('**/api/journal?*', async route => {
        const url = new URL(route.request().url());
        if ((failDate && url.searchParams.get('date') === '2027-04-11') ||
            (failSearch && url.searchParams.get('q') === 'missing phrase')) {
          await route.fulfill({ status: 503, json: { error: 'journal_unavailable' } });
        } else await route.continue();
      });
      await page.getByLabel('记录日期').fill('2027-04-11');
      await expect(page.getByRole('alert')).toBeVisible();
      await expect(page.locator('.journal-timeline')).toHaveCount(0);
      await expect(page.getByRole('alert')).toContainText('记录读取失败');
      await expect(page.getByRole('button', { name: '编辑 Old day entry' })).toHaveCount(0);
      await expect(page.getByRole('button', { name: '删除 Old day entry' })).toHaveCount(0);
      await expect(page.getByLabel('记录内容')).toHaveValue('Unsaved draft must remain');

      failDate = false;
      await page.getByRole('button', { name: '重试读取记录' }).click();
      await expect(page.locator('.journal-empty')).toBeVisible();
      await expect(page.getByLabel('记录内容')).toHaveValue('Unsaved draft must remain');
      await page.getByLabel('记录日期').fill('2027-04-10');
      await expect(page.locator('.journal-timeline')).toContainText('Old day entry');

      failSearch = true;
      await page.getByLabel('搜索记录').fill('missing phrase');
      await expect(page.getByRole('alert')).toContainText('记录读取失败');
      await expect(page.locator('.journal-timeline')).toHaveCount(0);
      await expect(page.getByRole('button', { name: '编辑 Old day entry' })).toHaveCount(0);
      failSearch = false;
      await page.getByRole('button', { name: '重试读取记录' }).click();
      await expect(page.locator('.journal-empty')).toBeVisible();
      await expect(page.getByLabel('记录内容')).toHaveValue('Unsaved draft must remain');
    });
  } finally { await browser.close(); }
  if (failures.length) process.exit(1);
})().catch(error => { console.error(error); process.exit(1); });
