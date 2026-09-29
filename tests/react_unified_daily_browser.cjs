const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const base = process.env.PREVIEW_BASE_URL;
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, timezoneId: 'America/Los_Angeles' });
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const page = await context.newPage();
    await page.goto(`${base}/admin/plans`);
    const nav = page.getByRole('navigation', { name: '今日页面内容' });
    await expect(nav.getByRole('link')).toHaveCount(3);
    for (const id of ['daily-plans', 'daily-timeline', 'daily-review']) await expect(page.locator(`#${id}`)).toBeVisible();
    const positions = await page.evaluate(() => Object.fromEntries(['.plans-header', '.daily-section-nav', '#daily-plans', '#daily-timeline', '#daily-review'].map(selector => [selector, document.querySelector(selector).getBoundingClientRect().top + scrollY])));
    assert(positions['.plans-header'] < positions['.daily-section-nav'] && positions['.daily-section-nav'] < positions['#daily-plans'] && positions['#daily-plans'] < positions['#daily-timeline'] && positions['#daily-timeline'] < positions['#daily-review'], 'hero, navigation and three sections should follow reading order');
    await expect(page.locator('.journal-empty')).toBeVisible();
    for (const width of [1440, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      if (width < 1440) await expect.poll(() => page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().right)).toBeLessThan(1);
      await page.evaluate(() => scrollTo(0, 0));
      await page.evaluate(() => document.fonts.ready);
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      if (process.env.REACT_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, `unified-daily-${width}.png`), fullPage: true });
      }
    }
    await page.setViewportSize({ width: 1440, height: 900 });

    await page.getByLabel('计划日期').fill('2026-09-29');
    await expect(page.getByLabel('记录日期')).toHaveValue('2026-09-29');
    await expect(page.locator('.journal-week-nav strong')).toHaveText('2026-09-28 — 2026-10-04');
    await page.getByLabel('计划标题').fill('Keep plan draft');
    await page.getByLabel('记录内容').fill('Keep journal draft');
    await nav.getByRole('link', { name: '时间线' }).click();
    await expect(page).toHaveURL(/#daily-timeline$/);
    await expect(page.locator('#daily-timeline')).toBeFocused();
    await nav.getByRole('link', { name: '回顾' }).click();
    await expect(page).toHaveURL(/#daily-review$/);
    await expect(page.locator('#daily-review')).toBeFocused();
    await page.goBack();
    await expect(page).toHaveURL(/#daily-timeline$/);
    await expect(page.getByLabel('计划标题')).toHaveValue('Keep plan draft');
    await expect(page.getByLabel('记录内容')).toHaveValue('Keep journal draft');
    await page.getByRole('button', { name: '后一天' }).click();
    await expect(page.getByLabel('记录日期')).toHaveValue('2026-09-30');
    await expect(page.locator('.journal-week-nav strong')).toHaveText('2026-09-28 — 2026-10-04');
    await page.getByLabel('计划日期').fill('2026-10-05');
    await expect(page.locator('.journal-week-nav strong')).toHaveText('2026-10-05 — 2026-10-11');
    await expect(page.getByLabel('计划标题')).toHaveValue('Keep plan draft');
    await expect(page.getByLabel('记录内容')).toHaveValue('Keep journal draft');
    page.once('dialog', dialog => dialog.dismiss());
    await page.getByRole('button', { name: '写每日复盘' }).click();
    await expect(page.getByLabel('记录内容')).toHaveValue('Keep journal draft');
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: '写每周回顾' }).click();
    await expect(page.getByLabel('记录类型')).toHaveValue('weekly_review');
    await expect(page.getByLabel('记录类型')).toBeFocused();
    await expect(page.getByLabel('开始时间')).toHaveValue(/^2026-10-05T/);

    for (const width of [1440, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    }
    await page.setViewportSize({ width: 768, height: 900 });
    await nav.getByRole('link', { name: '今日计划' }).focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/#daily-plans$/);
    await expect(page.locator('#daily-plans')).toBeFocused();
    await context.close();

    const direct = await browser.newContext({ viewport: { width: 390, height: 840 }, timezoneId: 'America/Los_Angeles' });
    await direct.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const deepLink = await direct.newPage();
    await deepLink.goto(`${base}/admin/plans#daily-review`);
    await expect(deepLink.locator('#daily-review')).toBeFocused();
    await expect(deepLink.locator('#daily-plans')).toBeVisible();
    await expect(deepLink.locator('#daily-timeline')).toBeVisible();
    await direct.close();

    const planFailure = await browser.newContext({ viewport: { width: 768, height: 900 }, timezoneId: 'America/Los_Angeles' });
    await planFailure.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const degraded = await planFailure.newPage();
    await degraded.route('**/api/plans', route => route.fulfill({ status: 503, json: { error: 'plans_unavailable' } }));
    await degraded.goto(`${base}/admin/plans`);
    await expect(degraded.locator('.plans-save-status')).toHaveText('计划读取失败');
    await degraded.getByLabel('计划日期').fill('2026-09-29');
    await degraded.getByRole('button', { name: '后一天' }).click();
    await expect(degraded.getByLabel('记录日期')).toHaveValue('2026-09-30');
    await expect(degraded.locator('.journal-week-nav strong')).toHaveText('2026-09-28 — 2026-10-04');
    await expect(degraded.getByLabel('记录内容')).toBeEnabled();
    await planFailure.close();
    console.log('PASS: shared hero, mounted sections, fragments, drafts, dates, review editor, and responsive layout');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
