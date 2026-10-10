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
    await expect(page.locator('.plans-quote')).toHaveCount(0);
    await expect(page.locator('.daily-section-heading')).toHaveCount(0);
    await expect(page.locator('.plans-header h2')).toHaveCount(1);
    await expect(page.locator('.plans-save-status')).toHaveText('已保存');
    await expect(page.locator('.plans-day-badge')).toHaveText('今天');
    await expect(page.getByRole('button', { name: '回到今天' })).toHaveCount(0);
    await expect(page.locator('.plans-progress')).toHaveCount(0);
    const dateInput = page.getByLabel('计划日期');
    const today = await dateInput.inputValue();
    await dateInput.fill('2026-09-29');
    await expect(page.locator('.plans-header h2')).toHaveText('9月29日 星期二');
    await expect(page.locator('.plans-day-badge')).toHaveText(/^\d+ 天前$/);
    await page.getByRole('button', { name: '前一天' }).click();
    await expect(dateInput).toHaveValue('2026-09-28');
    await expect(page.locator('.plans-header h2')).toHaveText('9月28日 星期一');
    await page.getByRole('button', { name: '回到今天' }).click();
    await expect(dateInput).toHaveValue(today);
    await expect(page.locator('.plans-day-badge')).toHaveText('今天');
    await page.getByRole('button', { name: '后一天' }).click();
    await expect(page.locator('.plans-day-badge')).toHaveText('明天');
    await dateInput.fill('2026-09-29');
    const screenshotDir = process.env.REACT_SCREENSHOT_DIR;
    await page.setViewportSize({ width: 1440, height: 900 });
    const views = page.getByRole('navigation', { name: '今日页面内容' });
    const desktopHeading = await page.locator('.plans-header h2').boundingBox();
    const desktopViews = await views.boundingBox();
    const desktopSummary = await page.locator('.day-summary').boundingBox();
    const desktopPane = await page.locator('.daily-plans-section').boundingBox();
    const desktopComposer = await page.locator('.plans-composer').boundingBox();
    assert(desktopHeading && desktopViews && desktopViews.x > desktopHeading.x + desktopHeading.width,
      'the view switcher should share the header band with the date on desktop');
    assert(desktopSummary && desktopSummary.y - desktopHeading.y < 120, 'today at a glance should sit right under the compact header');
    assert(desktopPane && desktopComposer && desktopComposer.y - desktopPane.y < 70, 'the add-task card should open the plan pane');
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
    const mobileViews = await views.boundingBox();
    const mobileHeading = await page.locator('.plans-header h2').boundingBox();
    assert(mobileViews && mobileHeading && mobileViews.y > mobileHeading.y + mobileHeading.height,
      'the view switcher should stack below the date heading on mobile');
    assert(mobileViews.width > 340, 'the view switcher should span the mobile width');
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
    await expect(page.locator('.plans-progress')).toContainText('已完成 0 / 1');
    const taskToggle = page.getByRole('button', { name: '调整 继续论文下载与阅读' });
    await expect(taskToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.getByLabel('继续论文下载与阅读状态')).toHaveCount(0);
    await taskToggle.click();
    await expect(taskToggle).toHaveAttribute('aria-expanded', 'true');
    await expect(page.getByLabel('继续论文下载与阅读状态')).toBeVisible();
    await page.keyboard.press('Tab');
    await expect(page.getByLabel('继续论文下载与阅读状态')).toBeFocused();
    await expect(page.getByLabel('继续论文下载与阅读分类')).toHaveValue('important');
    await expect(page.getByRole('button', { name: '删除 继续论文下载与阅读' })).toBeVisible();
    await taskToggle.click();
    await expect(page.getByLabel('继续论文下载与阅读状态')).toHaveCount(0);
    await expect(page.locator('.plans-composer')).not.toHaveClass(/is-dirty/);
    await page.locator('.plans-task-check input').first().check();
    await expect(page.locator('.plans-save-feedback')).toHaveText('已标记完成；点击“保存计划”后同步');
    await expect(page.locator('.plans-composer')).toHaveClass(/is-dirty/);
    await expect(page.locator('.plans-progress')).toContainText('已完成 1 / 1 · 全部完成');
    await page.getByRole('button', { name: '保存计划' }).click();
    await expect(page.locator('.plans-save-status')).toHaveText('已保存');
    await expect(page.locator('.plans-composer')).not.toHaveClass(/is-dirty/);
    assert.equal(savedItems[0].status, 'done', 'marking a task complete must persist after explicit save');
    console.log('PASS: compact plan header, task adjustments, assistant drawer layout and semantic duplicate merging');
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
