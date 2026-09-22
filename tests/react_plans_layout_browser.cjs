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
    await page.route('**/api/plans/assistant', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({
      model: 'gemini-preview-fast', service_name: 'Gemini', structured_output: 'gemini_native_schema', summary: '先处理最重要的事项。',
      suggestions: [{ title: '完成项目提纲', notes: '拆成三个小步骤。', quadrant: 'important', start_time: '10:30', estimate_minutes: 45, reminder_offset_minutes: 10, reason: '为后续工作建立结构。' }],
    }) }));
    await page.route('**/api/plans', async route => {
      if (route.request().method() === 'PUT') {
        saveRequests += 1;
        savedItems = route.request().postDataJSON().items;
      }
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: savedItems, revision: 'b'.repeat(64) }) });
    });

    await page.goto(`${base}/admin/plans`);
    await expect(page.getByRole('heading', { name: '今日计划' })).toBeVisible();
    await expect(page.locator('.plans-save-status')).toHaveText('已保存');
    const gridBefore = await page.locator('.plans-grid').boundingBox();
    await page.getByRole('button', { name: 'AI 建议' }).click();
    const assistant = page.getByRole('region', { name: 'AI 每日计划建议' });
    await expect(assistant).toBeVisible();
    await expect(assistant).toHaveCSS('position', 'fixed');
    await expect(page.getByRole('button', { name: '关闭', exact: true })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(assistant).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'AI 建议' })).toBeFocused();
    await page.getByRole('button', { name: 'AI 建议' }).click();
    const gridAfter = await page.locator('.plans-grid').boundingBox();
    assert.equal(gridAfter.y, gridBefore.y, 'opening AI suggestions must not push the plan grid down');

    await page.getByLabel('告诉 Gemini 你的目标').fill('今天先完成项目方案。');
    await page.getByRole('button', { name: '生成建议' }).click();
    await expect(page.getByLabel('建议任务标题')).toHaveValue('完成项目提纲');
    await page.getByRole('button', { name: '添加选中建议并保存' }).click();
    await expect(page.locator('.plans-save-status')).toHaveText('已保存');
    assert.equal(savedItems.length, 1);
    assert.equal(saveRequests, 1);

    await page.getByRole('button', { name: 'AI 建议' }).click();
    await page.getByLabel('告诉 Gemini 你的目标').fill('再次安排项目方案。');
    await page.getByRole('button', { name: '生成建议' }).click();
    await page.getByRole('button', { name: '添加选中建议并保存' }).click();
    await expect(page.getByRole('region', { name: 'AI 每日计划建议' })).toHaveCount(0);
    assert.equal(savedItems.length, 1, 're-adding the same suggestion must not create a duplicate');
    assert.equal(saveRequests, 1, 'duplicate suggestions must not submit an unnecessary save');
    console.log('PASS: plan assistant drawer preserves layout and deduplicates repeated suggestions');
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
