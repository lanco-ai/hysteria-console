const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

// Two views on /admin/plans. 计划与行程 is a day board: today at a glance, then the plan, the time
// axis and the side cards in three panes (two when medium, one when narrow). 时间线与回顾 has a week bar
// over the day's records and the weekly review.
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
    const side = page.locator('.day-side');
    const summary = page.locator('.day-summary');
    const week = page.locator('.journal-week-nav');
    const timeline = page.locator('.daily-timeline-section');
    const review = page.locator('.daily-review-section');
    const entry = page.getByLabel('记录内容');
    await expect(nav.getByRole('link')).toHaveText(['计划与行程', '时间线与回顾']);
    await expect(plansLink).toHaveAttribute('aria-current', 'page');
    await expect(page.locator('#daily-journal-drawer, .schedule-strip')).toHaveCount(0);
    await expect(page.locator('.plans-quadrant')).toHaveCount(4);
    await expect(page.locator('.schedule-timeline')).toBeVisible();
    await expect(timeline).toBeHidden();
    await expect(review).toBeHidden();

    async function box(locator) {
      const value = await locator.boundingBox();
      assert(value, 'the part renders');
      return value;
    }
    const beside = (a, b) => a.x + a.width <= b.x && Math.abs(a.y - b.y) < 4;
    const below = (a, b) => b.y >= a.y + a.height;
    async function screenshot(name, width) {
      await page.evaluate(() => document.fonts.ready);
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      if (process.env.REACT_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, `daily-${name}-${width}.png`), fullPage: true });
      }
    }

    // Three panes when wide, two when medium (side cards under the plan), one column when narrow.
    for (const [width, panes] of [[1920, 3], [1440, 2], [1280, 2], [768, 1], [390, 1]]) {
      await page.setViewportSize({ width, height: 1000 });
      if (width < 1100) await expect.poll(() => page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().right)).toBeLessThan(1);
      await page.evaluate(() => scrollTo(0, 0));
      if (await journalLink.getAttribute('aria-current')) await plansLink.click();
      await expect(plan).toBeVisible();
      const [top, tasks, timeAxis, cards] = [await box(summary), await box(plan), await box(schedule), await box(side)];
      assert(below(top, tasks) && below(top, timeAxis), `today at a glance comes first at ${width}px`);
      if (panes === 3) assert(beside(tasks, timeAxis) && beside(timeAxis, cards), `plan, time axis and side cards sit side by side at ${width}px`);
      else if (panes === 2) assert(beside(tasks, timeAxis) && below(tasks, cards) && cards.x < timeAxis.x, `side cards follow the plan beside the time axis at ${width}px`);
      else assert(below(tasks, timeAxis) && below(timeAxis, cards), `plan, time axis and side cards stack at ${width}px`);
      await screenshot('plans', width);
      await journalLink.click();
      await expect(page).toHaveURL(/#daily-timeline$/);
      await expect(journalLink).toHaveAttribute('aria-current', 'page');
      await expect(plan).toBeHidden();
      await expect(page.getByRole('button', { name: 'AI 建议' })).toBeHidden();
      await expect(entry).toBeVisible();
      await expect(review).toBeVisible();
      await expect(page.getByLabel('本周回顾内容')).toHaveCount(0);
      const [days, records, recapBox] = [await box(week), await box(timeline), await box(review)];
      assert(below(days, records), `the week bar leads the records at ${width}px`);
      assert(panes > 1 ? beside(records, recapBox) : below(records, recapBox), `day records and weekly review ${panes > 1 ? 'side by side' : 'stacked'} at ${width}px`);
      await screenshot('journal', width);
    }

    // On a wide page the time axis stays in view, as tall as the window, while the page scrolls.
    await page.setViewportSize({ width: 1920, height: 760 });
    await plansLink.click();
    await expect(plan).toBeVisible();
    await page.evaluate(() => scrollTo(0, 400));
    await expect.poll(async () => Math.round((await schedule.boundingBox()).y)).toBe(84);
    assert.equal(Math.round((await schedule.boundingBox()).height), 660, 'the time axis fills the window below the top bar');
    await page.evaluate(() => scrollTo(0, 0));

    // A draft survives switching views.
    await page.setViewportSize({ width: 1440, height: 900 });
    await journalLink.click();
    await entry.fill('Timeline draft');
    await plansLink.click();
    await expect(entry).toBeHidden();
    await journalLink.click();
    await expect(entry).toHaveValue('Timeline draft');
    await expect(page.locator('#journal-autosave-state')).toHaveText('已自动保存');
    // The open record is shown in the writing box, not repeated in the list.
    await expect(page.locator('.journal-timeline')).toHaveCount(0);
    await page.getByRole('button', { name: '另记一条' }).click();
    await expect(page.locator('.journal-timeline')).toContainText('Timeline draft');
    await expect(entry).toHaveValue('');
    // A weekly review is a record with the 每周回顾 category; the week column shows it in full.
    await review.getByRole('button', { name: '写本周回顾' }).click();
    await expect(page.getByLabel('筛选分类')).toHaveValue('weekly_review');
    await expect(entry).toBeFocused();
    await entry.fill('Week draft with **progress**');
    await entry.press('Control+Enter');
    await expect(page.locator('#journal-autosave-state')).toHaveText('已自动保存');
    const saved = (await (await context.request.get(`${base}/api/journal?q=draft`)).json()).items;
    assert.deepEqual(saved.map(item => [item.kind, item.body]).sort(), [['life', 'Timeline draft'], ['weekly_review', 'Week draft with **progress**']]);
    await expect(review.locator('.journal-week-review')).toContainText('Week draft with progress');
    await expect(review.locator('.journal-week-review strong')).toHaveText('progress');
    await expect(page.locator('.journal-counts')).toContainText('每周回顾：1');
    await page.getByLabel('筛选分类').selectOption('');

    // Back and forward switch views without losing either view's unsaved text.
    await plansLink.click();
    // A past day, so the writing box starts a new record there whatever day this runs.
    await page.getByLabel('计划日期').fill('2026-09-16');
    await page.getByLabel('计划标题').fill('Keep plan draft');
    await journalLink.click();
    await expect(page.locator('.journal-week-nav strong')).toHaveText('2026-09-14 — 2026-09-20');
    await page.route('**/api/journal', async route => {
      if (route.request().method() === 'POST') await route.fulfill({ status: 503, json: { error: 'journal_unavailable' } });
      else await route.continue();
    });
    await entry.fill('Keep record draft');
    await entry.press('Control+Enter');
    await expect(page.locator('#journal-autosave-state')).toHaveText('未保存，文字已保留');
    await page.goBack();
    await expect(page.getByLabel('计划标题')).toHaveValue('Keep plan draft');
    await expect(entry).toBeHidden();
    await page.goForward();
    await expect(entry).toHaveValue('Keep record draft');
    await expect(page.getByLabel('计划标题')).toBeHidden();
    await expect(page).toHaveURL(/#daily-timeline$/);
    await context.close();

    // Older links to 行程 or 回顾 open the view holding them; stacked, they scroll it into view.
    const direct = await browser.newContext({ viewport: { width: 390, height: 840 }, timezoneId: 'America/Los_Angeles', reducedMotion: 'reduce' });
    await direct.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const deepLink = await direct.newPage();
    await deepLink.goto(`${base}/admin/plans#daily-review`);
    await expect(deepLink.locator('.daily-review-section')).toContainText('这一周');
    await expect(deepLink.getByRole('navigation', { name: '今日页面内容' }).getByRole('link', { name: '时间线与回顾' })).toHaveAttribute('aria-current', 'page');
    await expect(deepLink.locator('.daily-review-section')).toBeInViewport();
    await expect.poll(() => deepLink.evaluate(() => scrollY)).toBeGreaterThan(0);
    const scheduleLink = await direct.newPage();
    await scheduleLink.goto(`${base}/admin/plans#daily-schedule`);
    await expect(scheduleLink.locator('.schedule-timeline')).toBeVisible();
    await expect(scheduleLink.getByRole('navigation', { name: '今日页面内容' }).getByRole('link', { name: '计划与行程' })).toHaveAttribute('aria-current', 'page');
    await expect(scheduleLink.locator('.daily-schedule-section')).toBeInViewport();
    await expect.poll(() => scheduleLink.evaluate(() => scrollY)).toBeGreaterThan(0);
    await direct.close();

    // Past midnight the page moves on to the new day, unless unsaved work keeps it where it was.
    const night = await browser.newContext({ viewport: { width: 1440, height: 900 }, timezoneId: 'Asia/Shanghai' });
    await night.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const late = await night.newPage();
    await late.route('**/api/plans/reminders', route => route.fulfill({ json: { items: [] } }));
    await late.clock.install({ time: new Date('2026-10-05T15:59:00Z') });
    await late.goto(`${base}/admin/plans`);
    const lateDate = late.getByLabel('计划日期');
    await expect(lateDate).toHaveValue('2026-10-05');
    await late.clock.fastForward('01:30');
    await expect(lateDate).toHaveValue('2026-10-06');
    await expect(late.getByRole('button', { name: '回到今天' })).toHaveCount(0);
    // Suggestions in an open AI dialog were made for its day, so the page waits.
    await late.getByRole('button', { name: 'AI 建议' }).click();
    await late.clock.fastForward('24:00:00');
    await late.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click();
    await expect(lateDate).toHaveValue('2026-10-06');
    await late.getByRole('button', { name: '回到今天' }).click();
    await expect(lateDate).toHaveValue('2026-10-07');
    await late.getByLabel('计划标题').fill('Unsaved before midnight');
    await late.clock.fastForward('24:00:00');
    await expect(late.getByRole('button', { name: '回到今天' })).toBeVisible();
    await expect(lateDate).toHaveValue('2026-10-07');
    await expect(late.getByLabel('计划标题')).toHaveValue('Unsaved before midnight');
    await night.close();

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
    console.log('PASS: day board panes and journal week view across widths, sticky time axis, separate drafts, history, deep links, midnight rollover and degraded plans');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
