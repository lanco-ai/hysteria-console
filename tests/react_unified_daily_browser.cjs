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
    const drawer = page.locator('#daily-journal-drawer');
    const close = page.getByRole('button', { name: '关闭记录面板' });
    await expect(nav.getByRole('link')).toHaveCount(3);
    await expect(page.locator('.plans-summary')).toHaveCount(0);
    await expect(page.getByText(/项完成 · 时区/)).toHaveCount(0);
    await expect(drawer).toBeHidden();
    await expect(page.locator('.daily-timeline-section')).toBeHidden();
    await expect(page.locator('.daily-review-section')).toBeHidden();
    await expect(page.locator('.plans-quadrant')).toHaveCount(4);

    async function screenshot(name, width) {
      await page.evaluate(() => document.fonts.ready);
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      if (await drawer.isVisible()) {
        const box = await drawer.boundingBox();
        assert(box.x >= 0 && box.x + box.width <= width + 1 && box.y >= 0 && box.y + box.height <= 901);
        assert(await drawer.evaluate(element => element.scrollWidth <= element.clientWidth), 'drawer must not overflow horizontally');
      }
      if (process.env.REACT_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, `daily-${name}-${width}.png`), fullPage: !(await drawer.isVisible()) });
      }
    }
    for (const width of [1440, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      if (width < 1440) await expect.poll(() => page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().right)).toBeLessThan(1);
      await page.evaluate(() => scrollTo(0, 0));
      await screenshot('workspace', width);
      await nav.getByRole('link', { name: '时间线' }).click();
      await expect(drawer).toBeVisible();
      await expect(close).toBeFocused();
      const scroll = await page.evaluate(() => scrollY);
      await expect(page.locator('.journal-empty')).toBeVisible();
      await screenshot('timeline', width);
      await expect(page.getByRole('button', { name: '写记录', exact: true })).toHaveCount(0);
      await expect(page.getByLabel('记录内容')).toBeVisible();
      await expect(page.locator('.journal-timeline-primary')).toBeVisible();
      await screenshot('editor', width);
      await page.getByLabel('记录内容').fill(`Draft ${width}`);
      await page.keyboard.press('Escape');
      await expect(drawer).toBeHidden();
      await expect(nav.getByRole('link', { name: '时间线' })).toBeFocused();
      assert.equal(await page.evaluate(() => scrollY), scroll, 'closing must retain background scroll position');
      await nav.getByRole('link', { name: '时间线' }).click();
      await expect(page.getByRole('button', { name: '继续填写' })).toHaveCount(0);
      await expect(page.getByLabel('记录内容')).toHaveValue(`Draft ${width}`);
      await page.getByLabel('记录内容').fill('');
      await close.click();
      await nav.getByRole('link', { name: '回顾' }).click();
      await expect(page.locator('.journal-review')).toBeVisible();
      await expect(page.locator('.journal-editor')).toBeVisible();
      await screenshot('review', width);
      // Native modal keeps Tab focus inside, including wraparound.
      await close.focus();
      for (let i = 0; i < 15; i++) {
        await page.keyboard.press(i < 8 ? 'Tab' : 'Shift+Tab');
        assert(await drawer.evaluate(element => element.contains(document.activeElement)), 'focus must stay inside drawer');
      }
      await close.click();
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByLabel('计划日期').fill('2026-10-05');
    await page.getByLabel('计划标题').fill('Keep plan draft');
    await nav.getByRole('link', { name: '回顾' }).click();
    await expect(page.locator('.journal-week-nav strong')).toHaveText('2026-10-05 — 2026-10-11');
    await page.getByLabel('记录类型').selectOption('weekly_review');
    await expect(page.getByLabel('开始时间')).toHaveValue(/^2026-10-05T/);
    await page.getByLabel('记录内容').fill('Keep review draft');
    await close.click();
    await page.goBack();
    await expect(drawer).toBeVisible();
    await expect(page).toHaveURL(/#daily-review$/);
    await expect(page.getByLabel('记录内容')).toHaveValue('Keep review draft');
    await page.goForward();
    await expect(drawer).toBeHidden();
    await expect(page.getByLabel('计划标题')).toHaveValue('Keep plan draft');
    await nav.getByRole('link', { name: '时间线' }).click();
    await expect(page.getByLabel('记录日期')).toHaveValue('2026-10-05');
    await expect(page.getByLabel('记录内容')).toHaveValue('Keep review draft');
    page.once('dialog', dialog => dialog.dismiss());
    await page.getByLabel('记录类型').selectOption('life');
    await expect(page.getByLabel('记录内容')).toHaveValue('Keep review draft');
    await close.click();
    await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden');
    await context.close();

    const direct = await browser.newContext({ viewport: { width: 390, height: 840 }, timezoneId: 'America/Los_Angeles', reducedMotion: 'reduce' });
    await direct.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const deepLink = await direct.newPage();
    await deepLink.goto(`${base}/admin/plans#daily-review`);
    await expect(deepLink.getByRole('dialog', { name: '回顾', exact: true })).toBeVisible();
    await deepLink.reload();
    await expect(deepLink.getByRole('dialog', { name: '回顾', exact: true })).toBeVisible();
    await deepLink.keyboard.press('Escape');
    await expect(deepLink.locator('#daily-journal-drawer')).toBeHidden();
    await expect(deepLink.getByRole('navigation', { name: '今日页面内容' }).getByRole('link', { name: '今日计划' })).toBeFocused();
    await direct.close();

    const planFailure = await browser.newContext({ viewport: { width: 768, height: 900 }, timezoneId: 'America/Los_Angeles' });
    await planFailure.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const degraded = await planFailure.newPage();
    await degraded.route('**/api/plans', route => route.fulfill({ status: 503, json: { error: 'plans_unavailable' } }));
    await degraded.goto(`${base}/admin/plans`);
    await expect(degraded.locator('.plans-save-status')).toHaveText('计划读取失败');
    await degraded.getByLabel('计划日期').fill('2026-09-29');
    await degraded.getByRole('button', { name: '后一天' }).click();
    await degraded.getByRole('navigation', { name: '今日页面内容' }).getByRole('link', { name: '时间线' }).click();
    await expect(degraded.getByLabel('记录日期')).toHaveValue('2026-09-30');
    await expect(degraded.getByLabel('记录内容')).toBeEnabled();
    await planFailure.close();
    console.log('PASS: compact workspace, journal/review drawers, keyboard, history, protected drafts, shared dates and responsive layout');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
