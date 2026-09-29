const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');
const path = require('node:path');
const fs = require('node:fs');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const base = process.env.PREVIEW_BASE_URL;
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, timezoneId: 'America/Los_Angeles' });
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const page = await context.newPage();
    async function checkJournalLayout(view, width) {
      await page.setViewportSize({ width, height: 900 });
      const layout = await page.evaluate(currentView => {
        const primary = document.querySelector(currentView === 'timeline' ? '.journal-timeline-primary' : '.journal-review').getBoundingClientRect();
        const editor = document.querySelector('.journal-editor').getBoundingClientRect();
        const search = document.querySelector('.journal-filters input[type="search"]')?.getBoundingClientRect();
        return { primaryRight: primary.right, primaryBottom: primary.bottom, editorLeft: editor.left, editorTop: editor.top, searchRight: search?.right, scrollWidth: document.documentElement.scrollWidth };
      }, view);
      assert(layout.scrollWidth <= width, `${view} should fit ${width}px (scrollWidth ${layout.scrollWidth}px)`);
      if (view === 'timeline') {
        if (width < 1200) assert(layout.primaryBottom <= layout.editorTop, `timeline should stack before editor at ${width}px`);
        else assert(layout.primaryRight < layout.editorLeft, `timeline should have separated desktop columns at ${width}px`);
      } else assert(layout.primaryBottom > layout.editorTop, `review should follow the editor at ${width}px`);
      if (view === 'timeline') assert(layout.searchRight <= layout.primaryRight + 1, `timeline filters should fit their column at ${width}px`);
      if (process.env.REACT_SCREENSHOT_DIR && (width === 901 || width === 1100)) {
        fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, `journal-${view}-${width}.png`), fullPage: true });
      }
    }
    await page.goto(`${base}/admin/plans`);
    await expect(page.locator('#daily-timeline')).toBeVisible();
    await expect(page.locator('.journal-timeline-primary')).toHaveCount(1);
    const timelineLayout = await page.evaluate(() => {
      const primary = document.querySelector('.journal-timeline-primary').getBoundingClientRect();
      const editor = document.querySelector('.journal-editor').getBoundingClientRect();
      return { primary, editor };
    });
    assert(timelineLayout.primary.right < timelineLayout.editor.left, 'timeline filters and records should lead the desktop layout');
    assert(timelineLayout.editor.width >= 320 && timelineLayout.editor.width <= 380, 'quick entry should be a compact side column');
    for (const width of [901, 1024, 1100, 1280]) await checkJournalLayout('timeline', width);
    const screenshotDir = process.env.REACT_SCREENSHOT_DIR;
    await page.setViewportSize({ width: 390, height: 840 });
    await expect.poll(() => page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().right)).toBeLessThan(1);
    const cleanMobileWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    assert(cleanMobileWidth <= 390, `clean journal layout should fit 390px (scrollWidth ${cleanMobileWidth}px)`);
    if (screenshotDir) {
      fs.mkdirSync(screenshotDir, { recursive: true });
      await page.screenshot({ path: path.join(screenshotDir, 'journal-timeline-mobile-clean.png'), fullPage: true });
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    const extraFields = page.locator('.journal-extra-fields');
    await expect(extraFields).not.toHaveAttribute('open', '');
    await expect(page.getByLabel('记录标题')).toBeHidden();
    await page.getByText('更多记录选项').click();
    await expect(page.getByLabel('记录标题')).toBeVisible();
    if (screenshotDir) {
      fs.mkdirSync(screenshotDir, { recursive: true });
      await page.screenshot({ path: path.join(screenshotDir, 'journal-timeline-desktop.png'), fullPage: true });
    }
    await page.getByLabel('记录类型').selectOption('ielts');
    await page.getByLabel('开始时间').fill('2026-09-29T19:00');
    await page.getByLabel('结束时间').fill('2026-09-29T19:30');
    await page.getByLabel('记录标题').fill('IELTS listening drill');
    await page.getByLabel('记录内容').fill('Transcribed one clip');
    await page.getByLabel('练习项目').selectOption('listening');
    await page.getByLabel('纠错').fill('Review plural endings');
    await page.getByRole('button', { name: '保存记录' }).click();
    await expect(page.locator('.journal-timeline').getByText('IELTS listening drill')).toBeVisible();
    await page.reload();
    await expect(page.locator('.journal-timeline').getByText('IELTS listening drill')).toBeVisible();
    await page.setViewportSize({ width: 390, height: 840 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.getByRole('button', { name: '编辑 IELTS listening drill' }).click();
    const reducedMotionTop = await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(document.querySelector('.journal-editor').getBoundingClientRect().top)))));
    assert(reducedMotionTop < 180, `reduced-motion edit should position the editor without smooth scrolling (top ${reducedMotionTop}px)`);
    await expect.poll(() => page.locator('.journal-editor').evaluate(element => element.getBoundingClientRect().top)).toBeLessThan(180);
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.getByLabel('记录内容').fill('Retried and corrected transcript');
    await page.getByRole('button', { name: '保存修改' }).click();
    await expect(page.getByText('Retried and corrected transcript')).toBeVisible();
    await page.getByLabel('搜索记录').fill('Retried');
    await expect(page.locator('.journal-timeline').getByText('IELTS listening drill')).toBeVisible();
    await page.getByLabel('搜索记录').fill('absent phrase');
    await expect(page.locator('.journal-timeline').getByText('IELTS listening drill')).toHaveCount(0);
    await page.getByRole('navigation', { name: '今日页面内容' }).getByRole('link', { name: '回顾' }).click();
    const reviewLayout = await page.evaluate(() => {
      const primary = document.querySelector('.journal-review').getBoundingClientRect();
      const editor = document.querySelector('.journal-editor').getBoundingClientRect();
      return { primary, editor };
    });
    assert(reviewLayout.primary.top > reviewLayout.editor.top, 'weekly review should follow the timeline and editor');
    for (const width of [901, 1024, 1100, 1280]) await checkJournalLayout('review', width);
    await expect(page.locator('.journal-review-list')).toContainText('IELTS listening drill');
    if (screenshotDir) await page.screenshot({ path: path.join(screenshotDir, 'journal-review-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 840 });
    await expect.poll(() => page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().right)).toBeLessThan(1);
    const reviewMobileWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    assert(reviewMobileWidth <= 390, `clean review layout should fit 390px (scrollWidth ${reviewMobileWidth}px)`);
    if (screenshotDir) await page.screenshot({ path: path.join(screenshotDir, 'journal-review-mobile-clean.png'), fullPage: true });
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.getByRole('button', { name: '写每日复盘' }).click();
    await page.getByLabel('开始时间').fill('2026-09-29T21:00');
    await page.getByRole('textbox', { name: '学习状态' }).fill('Focused after explaining the idea aloud');
    await page.getByRole('textbox', { name: '支持这个判断的证据' }).fill('Reconstructed the example without notes');
    await page.getByRole('button', { name: '保存记录' }).click();
    await expect(page.locator('.journal-review-list')).toContainText('Focused after explaining the idea aloud');
    await page.getByRole('navigation', { name: '今日页面内容' }).getByRole('link', { name: '时间线' }).click();
    await page.getByLabel('搜索记录').fill('Reconstructed');
    await expect(page.locator('.journal-timeline')).toContainText('支持这个判断的证据');
    await expect(page.locator('.journal-timeline')).toContainText('Reconstructed the example without notes');
    await page.getByLabel('记录内容').fill('Unsaved conflict note');
    await page.route('**/api/journal', async route => {
      if (route.request().method() === 'POST') {
        await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: 'revision_conflict' }) });
      } else await route.continue();
    });
    await page.getByRole('button', { name: '保存记录' }).click();
    await expect(page.getByRole('alert')).toContainText('草稿已保留');
    page.once('dialog', dialog => dialog.dismiss());
    await page.getByLabel('记录类型').selectOption('life');
    await expect(page.getByLabel('记录内容')).toHaveValue('Unsaved conflict note');
    await page.setViewportSize({ width: 390, height: 840 });
    const mobileLayout = await page.evaluate(() => {
      const primary = document.querySelector('.journal-timeline-primary').getBoundingClientRect();
      const editor = document.querySelector('.journal-editor').getBoundingClientRect();
      return { primary, editor, scrollWidth: document.documentElement.scrollWidth };
    });
    assert(mobileLayout.primary.bottom <= mobileLayout.editor.top, 'timeline should stack records before editor on mobile');
    assert(mobileLayout.scrollWidth <= 390, 'journal mobile layout should not overflow');
    if (screenshotDir) await page.screenshot({ path: path.join(screenshotDir, 'journal-timeline-mobile.png'), fullPage: true });
    await page.getByRole('navigation', { name: '今日页面内容' }).getByRole('link', { name: '今日计划' }).click();
    await expect(page.locator('.plans-grid')).toBeVisible();
    assert.equal(await page.locator('.plans-quadrant').count(), 4);
    console.log('PASS: journal create, reload, edit, search and plan view');
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
