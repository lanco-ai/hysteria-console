const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

// Two views on /admin/plans, each two columns side by side on wide pages:
// 今日计划 beside 行程, and 时间线 beside 本周回顾.
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const base = process.env.PREVIEW_BASE_URL;
    const context = await browser.newContext({ viewport: { width: 1920, height: 1000 }, timezoneId: 'America/Los_Angeles' });
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const page = await context.newPage();
    await page.route('**/api/plans/reminders', route => route.fulfill({ json: { items: [] } }));
    await page.goto(`${base}/admin/plans`);
    const nav = page.getByRole('navigation', { name: '今日页面内容' });
    const plansLink = nav.getByRole('link', { name: '计划与行程' });
    const journalLink = nav.getByRole('link', { name: '时间线与回顾' });
    const plan = page.locator('.daily-plans-section');
    const schedule = page.locator('.daily-schedule-section');
    const timeline = page.locator('.daily-timeline-section');
    const review = page.locator('.daily-review-section');
    const entry = page.getByLabel('记录内容');
    const recap = page.getByLabel('本周回顾内容', { exact: true });
    await expect(nav.getByRole('link')).toHaveText(['计划与行程', '时间线与回顾']);
    await expect(plansLink).toHaveAttribute('aria-current', 'page');
    await expect(page.locator('#daily-journal-drawer, .schedule-strip')).toHaveCount(0);
    await expect(page.locator('.plans-quadrant')).toHaveCount(4);
    await expect(page.locator('.schedule-timeline')).toBeVisible();
    await expect(timeline).toBeHidden();
    await expect(review).toBeHidden();

    async function boxes(left, right) {
      const a = await left.boundingBox();
      const b = await right.boundingBox();
      assert(a && b, 'both columns render');
      return { a, b, sideBySide: a.x + a.width <= b.x && Math.abs(a.y - b.y) < 4, stacked: b.y >= a.y + a.height };
    }
    async function screenshot(name, width) {
      await page.evaluate(() => document.fonts.ready);
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      if (process.env.REACT_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, `daily-${name}-${width}.png`), fullPage: true });
      }
    }

    // Wide pages put the columns side by side, narrower ones stack them, plan first.
    for (const [width, split] of [[1920, true], [1440, true], [1280, false], [768, false], [390, false]]) {
      await page.setViewportSize({ width, height: 1000 });
      if (width < 1100) await expect.poll(() => page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().right)).toBeLessThan(1);
      await page.evaluate(() => scrollTo(0, 0));
      if (await journalLink.getAttribute('aria-current')) await plansLink.click();
      await expect(plan).toBeVisible();
      const plans = await boxes(plan, schedule);
      assert(split ? plans.sideBySide : plans.stacked, `plan and schedule should be ${split ? 'side by side' : 'stacked'} at ${width}px`);
      await screenshot('plans', width);
      await journalLink.click();
      await expect(page).toHaveURL(/#daily-timeline$/);
      await expect(journalLink).toHaveAttribute('aria-current', 'page');
      await expect(plan).toBeHidden();
      await expect(page.getByRole('button', { name: 'AI 建议' })).toBeHidden();
      await expect(entry).toBeVisible();
      await expect(recap).toBeVisible();
      const journal = await boxes(timeline, review);
      assert(split ? journal.sideBySide : journal.stacked, `timeline and review should be ${split ? 'side by side' : 'stacked'} at ${width}px`);
      await screenshot('journal', width);
    }

    // On a wide page the shorter column stays in view while the page scrolls the other.
    await page.setViewportSize({ width: 1920, height: 760 });
    await plansLink.click();
    await expect(plan).toBeVisible();
    await page.evaluate(() => scrollTo(0, 600));
    await expect.poll(async () => Math.round((await plan.boundingBox()).y)).toBe(84);
    await page.evaluate(() => scrollTo(0, 0));

    // Each column keeps its own draft, and a draft survives switching views.
    await page.setViewportSize({ width: 1440, height: 900 });
    await journalLink.click();
    await entry.fill('Timeline draft');
    await recap.fill('Week draft');
    await plansLink.click();
    await expect(entry).toBeHidden();
    await journalLink.click();
    await expect(entry).toHaveValue('Timeline draft');
    await expect(recap).toHaveValue('Week draft');
    await expect(page.locator('#journal-autosave-state')).toHaveText('已自动保存');
    await expect(page.locator('#journal-review-autosave-state')).toHaveText('已自动保存');
    const saved = (await (await context.request.get(`${base}/api/journal?q=draft`)).json()).items;
    assert.deepEqual(saved.map(item => [item.kind, item.body]).sort(), [['life', 'Timeline draft'], ['weekly_review', 'Week draft']]);
    // The open records are shown in their writing boxes, not repeated in the list.
    await expect(page.locator('.journal-timeline')).toHaveCount(0);
    await page.getByRole('button', { name: '另记一条' }).click();
    await expect(page.locator('.journal-timeline')).toContainText('Timeline draft');
    await expect(page.locator('.journal-timeline')).not.toContainText('Week draft');
    await expect(entry).toHaveValue('');
    await expect(page.locator('.journal-counts')).toContainText('每周回顾：1');

    // Back and forward switch views without losing either view's unsaved text.
    await plansLink.click();
    // A past week, so the weekly writer starts a new record there whatever day this runs.
    await page.getByLabel('计划日期').fill('2026-09-16');
    await page.getByLabel('计划标题').fill('Keep plan draft');
    await journalLink.click();
    await expect(page.locator('.journal-week-nav strong')).toHaveText('2026-09-14 — 2026-09-20');
    await page.route('**/api/journal', async route => {
      if (route.request().method() === 'POST') await route.fulfill({ status: 503, json: { error: 'journal_unavailable' } });
      else await route.continue();
    });
    await recap.fill('Keep review draft');
    await recap.press('Control+Enter');
    await expect(page.locator('#journal-review-autosave-state')).toHaveText('未保存，文字已保留');
    await page.goBack();
    await expect(page.getByLabel('计划标题')).toHaveValue('Keep plan draft');
    await expect(recap).toBeHidden();
    await page.goForward();
    await expect(recap).toHaveValue('Keep review draft');
    await expect(page.getByLabel('计划标题')).toBeHidden();
    await expect(page).toHaveURL(/#daily-timeline$/);
    await context.close();

    // Older links to 行程 or 回顾 open the view holding them; stacked, they scroll it into view.
    const direct = await browser.newContext({ viewport: { width: 390, height: 840 }, timezoneId: 'America/Los_Angeles', reducedMotion: 'reduce' });
    await direct.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const deepLink = await direct.newPage();
    await deepLink.goto(`${base}/admin/plans#daily-review`);
    await expect(deepLink.getByLabel('本周回顾内容', { exact: true })).toBeVisible();
    await expect(deepLink.getByRole('navigation', { name: '今日页面内容' }).getByRole('link', { name: '时间线与回顾' })).toHaveAttribute('aria-current', 'page');
    await expect.poll(() => deepLink.locator('.daily-review-section').evaluate(element => Math.round(element.getBoundingClientRect().top))).toBeLessThan(200);
    const scheduleLink = await direct.newPage();
    await scheduleLink.goto(`${base}/admin/plans#daily-schedule`);
    await expect(scheduleLink.locator('.schedule-timeline')).toBeVisible();
    await expect(scheduleLink.getByRole('navigation', { name: '今日页面内容' }).getByRole('link', { name: '计划与行程' })).toHaveAttribute('aria-current', 'page');
    await expect.poll(() => scheduleLink.locator('.daily-schedule-section').evaluate(element => Math.round(element.getBoundingClientRect().top))).toBeLessThan(200);
    await direct.close();

    // Without the plan service the page still offers the records for the chosen day.
    const planFailure = await browser.newContext({ viewport: { width: 768, height: 900 }, timezoneId: 'America/Los_Angeles' });
    await planFailure.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const degraded = await planFailure.newPage();
    await degraded.route('**/api/plans', route => route.fulfill({ status: 503, json: { error: 'plans_unavailable' } }));
    await degraded.goto(`${base}/admin/plans`);
    await expect(degraded.locator('.plans-save-status')).toHaveText('计划读取失败');
    await degraded.getByLabel('计划日期').fill('2026-09-29');
    await degraded.getByRole('button', { name: '后一天' }).click();
    await degraded.getByRole('navigation', { name: '今日页面内容' }).getByRole('link', { name: '时间线与回顾' }).click();
    await expect(degraded.locator('.journal-week-nav strong')).toHaveText('2026-09-28 — 2026-10-04');
    await expect(degraded.getByLabel('记录内容')).toBeEnabled();
    await planFailure.close();
    console.log('PASS: two split views, responsive stacking, sticky column, separate drafts, history, deep links and degraded plans');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
