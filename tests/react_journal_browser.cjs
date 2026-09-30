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
    async function openPanel(name) {
      if (await page.locator('#daily-journal-drawer').isVisible()) await page.getByRole('button', { name: '关闭记录面板' }).click();
      await page.getByRole('navigation', { name: '今日页面内容' }).getByRole('link', { name }).click();
    }
    async function checkJournalLayout(view, width) {
      await page.setViewportSize({ width, height: 900 });
      const layout = await page.locator('#daily-journal-drawer').evaluate(element => ({
        width: element.scrollWidth, clientWidth: element.clientWidth,
        right: element.getBoundingClientRect().right, bottom: element.getBoundingClientRect().bottom,
      }));
      assert(layout.width <= layout.clientWidth && layout.right <= width && layout.bottom <= 900, `${view} drawer should fit ${width}px`);
      await expect(page.locator(view === 'timeline' ? '.journal-timeline-primary' : '.journal-review')).toBeVisible();
      const positions = await page.locator('#daily-journal-drawer').evaluate(element => {
        const drawer = element.getBoundingClientRect();
        const title = element.querySelector('#journal-drawer-title').getBoundingClientRect();
        const compose = Array.from(element.querySelectorAll('button')).find(button => button.textContent === '写记录').getBoundingClientRect();
        return { titleCenter: title.left + title.width / 2, drawerCenter: drawer.left + drawer.width / 2, composeBottom: compose.bottom, drawerBottom: drawer.bottom };
      });
      assert(Math.abs(positions.titleCenter - positions.drawerCenter) < 2, `${view} title should be centered at ${width}px`);
      assert(positions.drawerBottom - positions.composeBottom < 60, `${view} compose action should stay at the bottom at ${width}px`);
      if (view === 'timeline') {
        const date = await page.getByLabel('记录日期').boundingBox();
        const kind = await page.getByLabel('筛选分类').boundingBox();
        assert(Math.abs(date.y - kind.y) < 2, `date and category should share a compact row at ${width}px`);
        if (width > 680) assert(date.x > positions.drawerCenter - 100, 'date and category should sit at the upper right');
      }
    }
    await page.goto(`${base}/admin/plans#daily-timeline`);
    await expect(page.getByRole('dialog', { name: '生活与学习时间线' })).toBeVisible();
    await expect(page.locator('.journal-timeline-primary')).toHaveCount(1);
    for (const width of [390, 768, 901, 1024, 1100, 1280, 1440]) await checkJournalLayout('timeline', width);
    const screenshotDir = process.env.REACT_SCREENSHOT_DIR;
    if (screenshotDir) {
      fs.mkdirSync(screenshotDir, { recursive: true });
      await page.screenshot({ path: path.join(screenshotDir, 'journal-timeline-desktop-clean.png') });
    }
    await page.setViewportSize({ width: 390, height: 840 });
    await expect.poll(() => page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().right)).toBeLessThan(1);
    const cleanMobileWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    assert(cleanMobileWidth <= 390, `clean journal layout should fit 390px (scrollWidth ${cleanMobileWidth}px)`);
    if (screenshotDir) {
      fs.mkdirSync(screenshotDir, { recursive: true });
      await page.screenshot({ path: path.join(screenshotDir, 'journal-timeline-mobile-clean.png'), fullPage: true });
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.getByRole('button', { name: '写记录', exact: true }).click();
    for (const width of [390, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      const kind = await page.getByLabel('记录类型').boundingBox();
      const time = await page.getByLabel('开始时间').boundingBox();
      assert(Math.abs(kind.y - time.y) < 2 && Math.abs(kind.height - time.height) < 2, `record type and start time should align at ${width}px`);
      assert(await page.locator('.journal-drawer-body').evaluate(element => element.scrollWidth <= element.clientWidth), `editor should fit ${width}px`);
      if (screenshotDir && width === 390) await page.screenshot({ path: path.join(screenshotDir, 'journal-editor-mobile-aligned.png') });
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
    await page.getByLabel('记录内容').fill(`Transcribed one clip\n${'A line of listening notes.\n'.repeat(60)}`);
    await page.getByLabel('练习项目').selectOption('listening');
    await page.getByLabel('纠错').fill('Review plural endings');
    await page.getByRole('button', { name: '保存记录' }).click();
    await expect(page.locator('.journal-timeline').getByText('IELTS listening drill')).toBeVisible();
    if (screenshotDir) await page.screenshot({ path: path.join(screenshotDir, 'journal-timeline-desktop-populated.png') });
    const dateBeforeScroll = await page.getByLabel('记录日期').boundingBox();
    const composeBeforeScroll = await page.getByRole('button', { name: '写记录', exact: true }).boundingBox();
    const scrollTop = await page.locator('.journal-drawer-body').evaluate(element => {
      element.scrollTop = element.scrollHeight;
      return element.scrollTop;
    });
    assert(scrollTop > 0, 'long records should scroll inside the content area');
    assert.equal((await page.getByLabel('记录日期').boundingBox()).y, dateBeforeScroll.y, 'filters should remain visible while records scroll');
    assert.equal((await page.getByRole('button', { name: '写记录', exact: true }).boundingBox()).y, composeBeforeScroll.y, 'compose should remain visible while records scroll');
    await page.getByRole('button', { name: '写记录', exact: true }).focus();
    await page.getByRole('button', { name: '写记录', exact: true }).press('Tab');
    await expect(page.getByRole('button', { name: '关闭记录面板' })).toBeFocused();
    await page.locator('.journal-drawer-body').evaluate(element => { element.scrollTop = 0; });
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
    await openPanel('回顾');
    for (const width of [390, 768, 901, 1024, 1100, 1280, 1440]) await checkJournalLayout('review', width);
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
    await openPanel('时间线');
    await page.getByLabel('搜索记录').fill('Reconstructed');
    await expect(page.locator('.journal-timeline')).toContainText('支持这个判断的证据');
    await expect(page.locator('.journal-timeline')).toContainText('Reconstructed the example without notes');
    await page.getByRole('button', { name: '写记录', exact: true }).click();
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
    await expect(page.locator('.journal-timeline-primary')).toBeHidden();
    assert(mobileLayout.editor.top < 260, 'mobile editor opens directly inside the drawer');
    assert(mobileLayout.scrollWidth <= 390, 'journal mobile layout should not overflow');
    if (screenshotDir) await page.screenshot({ path: path.join(screenshotDir, 'journal-timeline-mobile.png'), fullPage: true });
    await page.getByRole('button', { name: '关闭记录面板' }).click();
    await expect(page.locator('.plans-grid')).toBeVisible();
    assert.equal(await page.locator('.plans-quadrant').count(), 4);
    console.log('PASS: journal create, reload, edit, search and plan view');
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
