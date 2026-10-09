const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const base = process.env.PREVIEW_BASE_URL;
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, timezoneId: 'America/Los_Angeles' });
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const page = await context.newPage();
    let saveRequests = 0;
    let savedItems = [];
    await page.route('**/api/plans/reminders', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [] }) }));
    let assistantRequests = 0;
    await page.route('**/api/plans/assistant', route => {
      assistantRequests += 1;
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
        model: 'preview-chat-fast', service_name: 'Chat API', structured_output: 'json_schema', summary: '先处理最重要的事项。',
        suggestions: assistantRequests === 1
          ? [{ title: '论文下载 阅读', notes: '拆成三个小步骤。', quadrant: 'important', start_time: '10:30', estimate_minutes: 45, reminder_offset_minutes: 10, reason: '为后续工作建立结构。' },
            { title: '完成论文下载与阅读', notes: '拆成三个小步骤。', quadrant: 'important', start_time: '10:30', estimate_minutes: 45, reminder_offset_minutes: 10, reason: '与第一项是同一件事。' }]
          : [{ title: assistantRequests === 2 ? '完成论文下载与阅读' : '继续论文下载与阅读', notes: '拆成三个小步骤。', quadrant: 'important', start_time: '10:30', estimate_minutes: 45, reminder_offset_minutes: 10, reason: '为后续工作建立结构。' }],
      }) });
    });
    await page.route('**/api/plans', async route => {
      if (route.request().method() === 'PUT') {
        saveRequests += 1;
        savedItems = route.request().postDataJSON().items;
      }
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: savedItems, revision: 'b'.repeat(64) }) });
    });

    await page.goto(`${base}/admin/plans`);
    await expect(page.locator('#daily-plans-heading')).toBeVisible();
    await expect(page.locator('.plans-header .plans-eyebrow')).toHaveCount(0);
    await expect(page.locator('.plans-save-status')).toHaveText('已保存');
    const dateInput = page.getByLabel('计划日期');
    await dateInput.fill('2026-09-29');
    await expect(page.locator('.plans-header h2')).toHaveText('9月29日 星期二');
    await expect(page.locator('.plans-date-subtitle')).toContainText('2026年09月29日');
    await expect(page.locator('.plans-quote')).toContainText('专注当下，持续积累');
    await page.getByRole('button', { name: '前一天' }).click();
    await expect(dateInput).toHaveValue('2026-09-28');
    await expect(page.locator('.plans-header h2')).toHaveText('9月28日 星期一');
    await expect(page.locator('.plans-date-subtitle')).toContainText('2026年09月28日');
    await page.getByRole('button', { name: '后一天' }).click();
    await expect(dateInput).toHaveValue('2026-09-29');
    const screenshotDir = process.env.REACT_SCREENSHOT_DIR;
    await page.setViewportSize({ width: 1440, height: 900 });
    const desktopHeader = await page.locator('.plans-header').boundingBox();
    const desktopQuote = await page.locator('.plans-quote').boundingBox();
    const desktopHeading = await page.locator('.plans-header h2').boundingBox();
    assert(desktopHeader && desktopQuote && desktopHeading && desktopQuote.x > desktopHeading.x + desktopHeading.width,
      'quote card should sit to the right of the date heading on desktop');
    if (screenshotDir) {
      require('node:fs').mkdirSync(screenshotDir, { recursive: true });
      await page.screenshot({ path: require('node:path').join(screenshotDir, 'plans-header-desktop-1440.png'), fullPage: true });
    }
    await page.setViewportSize({ width: 768, height: 900 });
    assert((await page.evaluate(() => document.documentElement.scrollWidth)) <= 768, 'plan layout should fit 768px');
    await page.setViewportSize({ width: 390, height: 840 });
    await expect.poll(() => page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().right)).toBeLessThan(1);
    const cleanMobileWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    assert(cleanMobileWidth <= 390, `clean plan layout should fit 390px (scrollWidth ${cleanMobileWidth}px)`);
    const mobileQuote = await page.locator('.plans-quote').boundingBox();
    const mobileHeading = await page.locator('.plans-header h2').boundingBox();
    assert(mobileQuote && mobileHeading && mobileQuote.y > mobileHeading.y + mobileHeading.height,
      'quote card should stack below the date heading on mobile');
    if (screenshotDir) {
      require('node:fs').mkdirSync(screenshotDir, { recursive: true });
      await page.screenshot({ path: require('node:path').join(screenshotDir, 'plans-mobile-clean.png'), fullPage: true });
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    const addOptions = page.locator('.plans-create-options');
    await expect(addOptions).not.toHaveAttribute('open', '');
    await expect(page.getByLabel('预计分钟')).toBeHidden();
    await expect(page.getByLabel('提醒时间')).toBeHidden();
    await page.getByText('时间与提醒选项').click();
    await expect(page.getByLabel('预计分钟')).toBeVisible();
    await expect(page.getByLabel('提醒时间')).toBeVisible();
    const emptyHeight = await page.locator('.plans-quadrant').first().evaluate(element => element.getBoundingClientRect().height);
    assert(emptyHeight < 190, 'empty plan quadrants should avoid excess vertical space');
    if (screenshotDir) {
      require('node:fs').mkdirSync(screenshotDir, { recursive: true });
      await page.screenshot({ path: require('node:path').join(screenshotDir, 'plans-desktop.png'), fullPage: true });
    }
    const gridBefore = await page.locator('.plans-grid').boundingBox();
    await page.getByRole('button', { name: 'AI 建议' }).click();
    const assistant = page.getByRole('dialog', { name: '把目标整理成可选计划' });
    await expect(assistant).toBeVisible();
    await expect(assistant).toHaveCSS('position', 'fixed');
    await expect(page.getByRole('button', { name: '关闭', exact: true })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(assistant).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'AI 建议' })).toBeFocused();
    await page.getByRole('button', { name: 'AI 建议' }).click();
    const gridAfter = await page.locator('.plans-grid').boundingBox();
    assert.equal(gridAfter.y, gridBefore.y, 'opening AI suggestions must not push the plan grid down');

    await page.getByLabel('告诉 AI 你的目标').fill('今天先完成论文阅读。');
    await page.getByRole('button', { name: '生成建议' }).click();
    await expect(page.getByLabel('建议任务标题').first()).toHaveValue('论文下载 阅读');
    await expect(page.getByRole('button', { name: '保存选中建议' })).toBeDisabled();
    await page.getByLabel('选择建议：完成论文下载与阅读').uncheck();
    await expect(page.getByRole('button', { name: '保存选中建议' })).toBeEnabled();
    await page.getByRole('button', { name: '保存选中建议' }).click();
    await expect(page.locator('.plans-save-status')).toHaveText('已保存');
    assert.equal(savedItems.length, 1);
    assert.equal(saveRequests, 1);

    await page.getByRole('button', { name: 'AI 建议' }).click();
    await page.getByLabel('告诉 AI 你的目标').fill('再次安排论文阅读。');
    await page.getByRole('button', { name: '生成建议' }).click();
    await expect(page.getByLabel('建议任务标题')).toHaveValue('完成论文下载与阅读');
    await page.getByRole('button', { name: '保存选中建议' }).click();
    await expect(page.getByRole('dialog', { name: '把目标整理成可选计划' })).toHaveCount(0);
    assert.equal(savedItems.length, 1, 'semantically duplicate suggestions must update the existing task');
    assert.equal(savedItems[0].title, '完成论文下载与阅读');
    assert.equal(saveRequests, 2, 'a semantic duplicate should update the existing task with one save');
    await page.getByRole('button', { name: 'AI 建议' }).click();
    await page.getByLabel('告诉 AI 你的目标').fill('继续安排论文阅读。');
    await page.getByRole('button', { name: '生成建议' }).click();
    await expect(page.getByLabel('建议任务标题')).toHaveValue('继续论文下载与阅读');
    await page.getByRole('button', { name: '保存选中建议' }).click();
    await expect(page.getByRole('dialog', { name: '把目标整理成可选计划' })).toHaveCount(0);
    assert.equal(savedItems.length, 1, 'semantically duplicate suggestions must update the existing task');
    assert.equal(savedItems[0].title, '继续论文下载与阅读');
    assert.equal(saveRequests, 3, 'each changed semantic duplicate should update the existing task once');
    await page.locator('.plans-task-check input').first().check();
    await expect(page.locator('.plans-save-feedback')).toHaveText('已标记完成；点击“保存计划”后同步');
    await page.getByRole('button', { name: '保存计划' }).click();
    await expect(page.locator('.plans-save-status')).toHaveText('已保存');
    assert.equal(savedItems[0].status, 'done', 'marking a task complete must persist after explicit save');
    console.log('PASS: plan assistant drawer preserves layout and merges semantically duplicate suggestions');
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
