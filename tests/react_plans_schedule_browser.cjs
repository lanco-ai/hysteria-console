const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const base = process.env.PREVIEW_BASE_URL;
    const screenshotDir = process.env.REACT_SCREENSHOT_DIR;
    const shot = async (page, name) => {
      if (!screenshotDir) return;
      fs.mkdirSync(screenshotDir, { recursive: true });
      await page.screenshot({ path: path.join(screenshotDir, name), fullPage: true });
    };
    const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, timezoneId: 'Asia/Shanghai' });
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const page = await context.newPage();
    let dialogAnswer = 'accept';
    const prompts = [];
    page.on('dialog', dialog => { prompts.push(dialog.message()); return dialogAnswer === 'accept' ? dialog.accept() : dialog.dismiss(); });
    // Keep the preview server's small request budget for the plan and schedule APIs under test.
    await page.route('**/api/plans/reminders', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [] }) }));
    await page.route('**/api/journal**', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [], week_start: '2026-10-05', week_end: '2026-10-11', total: 0, counts: {} }) }));

    await page.goto(`${base}/admin/plans`);
    // The day board: plan, time axis and side cards together, with today at a glance on top.
    const views = page.getByRole('navigation', { name: '今日页面内容' });
    await expect(views.getByRole('link', { name: '计划与行程' })).toHaveAttribute('aria-current', 'page');
    await expect(page.locator('.plans-grid')).toBeVisible();
    await expect(page.locator('.day-summary .schedule-now')).toBeVisible();
    await expect(page.locator('.schedule-empty')).toContainText('规划你的一天');
    await expect(page.locator('.schedule-strip')).toHaveCount(0);
    const board = page.getByRole('region', { name: '这一天的行程' });
    const agenda = page.getByRole('region', { name: '这一天的安排' });
    const showList = () => board.getByRole('button', { name: '清单', exact: true }).click();
    const showTimeline = () => board.getByRole('button', { name: '时间轴', exact: true }).click();
    const composer = page.getByRole('region', { name: '添加安排' });
    const form = page.locator('.schedule-form');
    // The editor is folded until a block is added or edited.
    await expect(form).toHaveCount(0);
    await expect(composer.getByRole('button', { name: '＋ 新建安排' })).toBeVisible();

    await page.getByRole('button', { name: '套用推荐作息' }).first().click();
    await expect(page.locator('.schedule-message')).toContainText('已套用推荐作息');
    await expect(page.locator('.schedule-block').filter({ hasText: '午餐' })).toBeVisible();
    await expect(page.locator('.schedule-legend')).toContainText('吃饭');
    await showList();
    await expect(agenda).toContainText('早餐');
    await expect(agenda).toContainText('睡觉');
    await showTimeline();

    // A quick pick opens the editor already filled in; cancel folds it again.
    await composer.getByRole('button', { name: '晚餐', exact: true }).click();
    await expect(form.getByLabel('做什么')).toHaveValue('晚餐');
    await form.getByRole('button', { name: '取消' }).click();
    await expect(form).toHaveCount(0);
    await expect(composer.getByRole('button', { name: '＋ 新建安排' })).toBeFocused();

    await composer.getByRole('button', { name: '＋ 新建安排' }).click();
    await expect(form.getByLabel('做什么')).toBeFocused();
    await form.getByLabel('做什么').fill('给妈妈打电话');
    await form.locator('label.schedule-chip', { hasText: '联系' }).click();
    await form.getByLabel('开始时间').fill('20:00');
    await form.getByLabel('结束时间').fill('20:30');
    await expect(form.locator('.schedule-form-warning')).toContainText('运动');
    await form.getByRole('button', { name: '添加' }).click();
    await expect(page.locator('.schedule-message')).toContainText('已加入这一天');
    await expect(form).toHaveCount(0);
    await expect(composer.getByRole('button', { name: '＋ 新建安排' })).toBeFocused();

    // Clicking a block on the time axis opens it, with its state for the day.
    await page.getByRole('button', { name: '修改 给妈妈打电话 20:00–20:30', exact: true }).click();
    await form.getByRole('button', { name: '已完成' }).click();
    await expect(page.locator('.schedule-message')).toContainText('完成：给妈妈打电话');
    await expect(form.getByRole('button', { name: '已完成' })).toHaveAttribute('aria-pressed', 'true');
    await form.getByRole('button', { name: '取消' }).click();

    // The checklist shows the same day with its own done and skip actions.
    await showList();
    await expect(agenda.locator('li', { hasText: '给妈妈打电话' })).toHaveClass(/is-done/);
    await expect(agenda.locator('li', { hasText: '给妈妈打电话' })).toContainText('联系');
    await page.getByRole('button', { name: '跳过 午休' }).click();
    await expect(agenda.locator('li', { hasText: '午休' })).toHaveClass(/is-skipped/);
    await page.getByRole('button', { name: '编辑 学习' }).click();
    await expect(form.getByRole('heading', { name: '修改作息模板' })).toBeVisible();
    await expect(form.getByLabel('重复')).toHaveValue('daily');
    await form.getByLabel('结束时间').fill('22:00');
    await form.getByRole('button', { name: '保存修改' }).click();
    await expect(page.locator('.schedule-message')).toContainText('已更新作息模板');
    await expect(form).toHaveCount(0);
    await showTimeline();
    await expect(page.locator('.schedule-block').filter({ hasText: '午休' })).toHaveCount(0);

    await page.getByRole('textbox', { name: '今日复盘' }).fill('上午精力最好，晚上早点睡。');
    await page.getByRole('button', { name: '保存复盘' }).click();
    await expect(page.locator('.schedule-message')).toContainText('已保存今日复盘');
    await shot(page, 'plans-schedule-desktop.png');

    await page.reload();
    await expect(page.locator('.schedule-timeline')).toBeVisible();
    await showList();
    await expect(agenda.locator('li', { hasText: '给妈妈打电话' })).toHaveClass(/is-done/);
    await expect(agenda.locator('li', { hasText: '午休' })).toHaveClass(/is-skipped/);
    await expect(agenda.locator('li').filter({ has: page.locator('strong', { hasText: /^学习$/ }) })).toContainText('22:00');
    await expect(page.getByRole('textbox', { name: '今日复盘' })).toHaveValue('上午精力最好，晚上早点睡。');
    await showTimeline();

    // The block form is locked while it saves, so what was saved is what it showed.
    let releaseDay;
    let held = false;
    await page.route('**/api/plans/schedule/day', async route => {
      if (!held) { held = true; await new Promise(resolve => { releaseDay = resolve; }); }
      await route.continue();
    });
    await composer.getByRole('button', { name: '＋ 新建安排' }).click();
    await form.getByLabel('做什么').fill('跨夜看书');
    await form.getByLabel('开始时间').fill('23:30');
    await form.getByLabel('结束时间').fill('01:30');
    await expect(form).toContainText('零点后的部分显示在第二天');
    await form.getByRole('button', { name: '添加' }).click();
    await expect.poll(() => held).toBe(true);
    await expect(form.getByLabel('做什么')).toBeDisabled();
    releaseDay();
    await expect(page.locator('.schedule-message')).toContainText('已加入这一天');
    await expect(form).toHaveCount(0);
    await page.unroute('**/api/plans/schedule/day');
    // An overnight block belongs to the day it starts: only its evening part is on this day.
    const lateBlock = page.locator('.schedule-block').filter({ hasText: '跨夜看书' });
    await expect(lateBlock).toHaveCount(1);
    await expect(lateBlock).not.toContainText('（续）');

    // Text typed while 今日复盘 saves is kept and saved right after.
    const note = page.getByRole('textbox', { name: '今日复盘' });
    const noteSaves = [];
    let releaseNote;
    await page.route('**/api/plans/schedule/day', async route => {
      noteSaves.push(JSON.parse(route.request().postData() || '{}').day.note);
      if (noteSaves.length === 1) await new Promise(resolve => { releaseNote = resolve; });
      await route.continue();
    });
    await note.fill('第一版复盘');
    await note.blur();
    await expect.poll(() => noteSaves.length).toBe(1);
    await note.fill('第一版复盘，再补一句');
    await note.blur();
    releaseNote();
    await expect.poll(() => noteSaves).toEqual(['第一版复盘', '第一版复盘，再补一句']);
    await expect(note).toHaveValue('第一版复盘，再补一句');
    await expect(page.locator('.schedule-note')).not.toContainText('未保存');
    await page.unroute('**/api/plans/schedule/day');

    // Switching dates while editing one of this day's blocks asks first; declining keeps the edit.
    await page.getByRole('button', { name: '修改 给妈妈打电话 20:00–20:30', exact: true }).click();
    await form.getByLabel('做什么').fill('给妈妈打视频电话');
    dialogAnswer = 'dismiss';
    const asked = prompts.length;
    await page.getByRole('button', { name: '后一天' }).click();
    await expect.poll(() => prompts.length).toBe(asked + 1);
    assert.match(prompts.at(-1), /切换日期会放弃/);
    await expect(form.getByLabel('做什么')).toHaveValue('给妈妈打视频电话');
    await expect(page.getByRole('button', { name: '回到今天' })).toHaveCount(0);
    dialogAnswer = 'accept';
    await form.getByRole('button', { name: '取消' }).click();

    // The next day keeps the routine but not this day's one-off block or marks.
    await page.getByRole('button', { name: '后一天' }).click();
    // The morning part of last night's block shows here, marked as continued, but is not this day's item.
    await expect(page.locator('.schedule-block').filter({ hasText: '（续）跨夜看书' })).toHaveCount(1);
    await showList();
    await expect(agenda).toContainText('早餐');
    await expect(agenda.locator('li', { hasText: '给妈妈打电话' })).toHaveCount(0);
    await expect(agenda.locator('li.is-skipped')).toHaveCount(0);
    await expect(agenda).not.toContainText('跨夜看书');
    await page.getByRole('button', { name: '回到今天' }).click();

    await page.setViewportSize({ width: 390, height: 840 });
    await expect(agenda).toContainText('给妈妈打电话');
    const mobileWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    assert(mobileWidth <= 390, `schedule should fit 390px (scrollWidth ${mobileWidth}px)`);
    await shot(page, 'plans-schedule-mobile.png');

    // The other view hides the board; coming back keeps it as it was.
    await page.setViewportSize({ width: 1440, height: 960 });
    await views.getByRole('link', { name: '时间线与回顾' }).click();
    await expect(page.locator('.daily-schedule-section')).toBeHidden();
    await views.getByRole('link', { name: '计划与行程' }).click();
    await expect(page.locator('.plans-grid')).toBeVisible();
    await expect(agenda).toContainText('给妈妈打电话');
    await shot(page, 'plans-with-schedule.png');
    console.log('PASS: day board: routine, quick picks, folded editor, done/skip from the time axis, checklist, note and view switching');
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
