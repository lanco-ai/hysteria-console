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
    page.on('dialog', dialog => dialog.accept());
    // Keep the preview server's small request budget for the plan and schedule APIs under test.
    await page.route('**/api/plans/reminders', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [] }) }));
    await page.route('**/api/journal**', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [], week_start: '2026-10-05', week_end: '2026-10-11', total: 0, counts: {} }) }));

    await page.goto(`${base}/admin/plans`);
    const strip = page.locator('.schedule-strip');
    await expect(strip).toContainText('还没有安排这一天');
    await expect(page.locator('.plans-grid')).toBeVisible();
    await strip.click();
    await expect(page).toHaveURL(/#daily-schedule$/);
    const views = page.getByRole('navigation', { name: '今日页面内容' });
    await expect(views.getByRole('link', { name: '行程' })).toHaveAttribute('aria-current', 'page');
    await expect(page.locator('.plans-composer')).toBeHidden();
    await expect(page.locator('.plans-grid')).toBeHidden();
    await expect(page.locator('.schedule-strip')).toHaveCount(0);

    await page.getByRole('button', { name: '套用推荐作息' }).first().click();
    await expect(page.locator('.schedule-message')).toContainText('已套用推荐作息');
    const agenda = page.getByRole('region', { name: '这一天的安排' });
    await expect(agenda).toContainText('早餐');
    await expect(agenda).toContainText('睡觉');
    await expect(page.locator('.schedule-block').filter({ hasText: '午餐' })).toBeVisible();
    await expect(page.locator('.schedule-legend')).toContainText('吃饭');

    const form = page.locator('.schedule-form');
    await form.getByLabel('做什么').fill('给妈妈打电话');
    await form.locator('label.schedule-chip', { hasText: '联系' }).click();
    await form.getByLabel('开始时间').fill('20:00');
    await form.getByLabel('结束时间').fill('20:30');
    await expect(form.locator('.schedule-form-warning')).toContainText('运动');
    await form.getByRole('button', { name: '添加' }).click();
    await expect(page.locator('.schedule-message')).toContainText('已加入这一天');
    await expect(agenda.locator('li', { hasText: '给妈妈打电话' })).toContainText('联系');
    await expect(form.getByLabel('做什么')).toHaveValue('');

    await agenda.locator('li', { hasText: '给妈妈打电话' }).getByRole('checkbox').check();
    await expect(page.locator('.schedule-message')).toContainText('完成：给妈妈打电话');
    await expect(agenda.locator('li', { hasText: '给妈妈打电话' })).toHaveClass(/is-done/);
    await page.getByRole('button', { name: '跳过 午休' }).click();
    await expect(agenda.locator('li', { hasText: '午休' })).toHaveClass(/is-skipped/);
    await expect(page.locator('.schedule-block').filter({ hasText: '午休' })).toHaveCount(0);

    await page.getByRole('button', { name: '编辑 学习' }).click();
    await expect(form.getByRole('heading', { name: '修改作息模板' })).toBeVisible();
    await expect(form.getByLabel('重复')).toHaveValue('daily');
    await form.getByLabel('结束时间').fill('22:00');
    await form.getByRole('button', { name: '保存修改' }).click();
    await expect(page.locator('.schedule-message')).toContainText('已更新作息模板');

    await page.getByRole('textbox', { name: '今日复盘' }).fill('上午精力最好，晚上早点睡。');
    await page.getByRole('button', { name: '保存复盘' }).click();
    await expect(page.locator('.schedule-message')).toContainText('已保存今日复盘');
    await shot(page, 'plans-schedule-desktop.png');

    await page.reload();
    await expect(page.locator('.schedule-timeline')).toBeVisible();
    await expect(agenda.locator('li', { hasText: '给妈妈打电话' })).toHaveClass(/is-done/);
    await expect(agenda.locator('li', { hasText: '午休' })).toHaveClass(/is-skipped/);
    await expect(agenda.locator('li').filter({ has: page.locator('strong', { hasText: /^学习$/ }) })).toContainText('22:00');
    await expect(page.getByRole('textbox', { name: '今日复盘' })).toHaveValue('上午精力最好，晚上早点睡。');

    // The next day keeps the routine but not this day's one-off block or marks.
    await page.getByRole('button', { name: '后一天' }).click();
    await expect(agenda).toContainText('早餐');
    await expect(agenda.locator('li', { hasText: '给妈妈打电话' })).toHaveCount(0);
    await expect(agenda.locator('li.is-skipped')).toHaveCount(0);
    await page.getByRole('button', { name: '回到今天' }).click();

    await page.setViewportSize({ width: 390, height: 840 });
    await expect(agenda).toContainText('给妈妈打电话');
    const mobileWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    assert(mobileWidth <= 390, `schedule should fit 390px (scrollWidth ${mobileWidth}px)`);
    await shot(page, 'plans-schedule-mobile.png');

    await page.setViewportSize({ width: 1440, height: 960 });
    await views.getByRole('link', { name: '今日计划' }).click();
    await expect(page.locator('.plans-grid')).toBeVisible();
    await expect(page.locator('.schedule-strip')).toBeVisible();
    await shot(page, 'plans-with-schedule-strip.png');
    console.log('PASS: day schedule routine, one-off blocks, marks, note and view switching');
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
