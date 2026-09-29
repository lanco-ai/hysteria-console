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
    await page.goto(`${base}/admin/plans`);
    await expect(page.getByRole('tab', { name: '时间线' })).toHaveAttribute('aria-selected', 'true');
    await page.getByLabel('记录类型').selectOption('ielts');
    await page.getByLabel('开始时间').fill('2026-09-29T19:00');
    await page.getByLabel('结束时间').fill('2026-09-29T19:30');
    await page.getByLabel('记录标题').fill('IELTS listening drill');
    await page.getByLabel('记录内容').fill('Transcribed one clip');
    await page.getByLabel('练习项目').selectOption('listening');
    await page.getByLabel('纠错').fill('Review plural endings');
    await page.getByRole('button', { name: '保存记录' }).click();
    await expect(page.getByText('IELTS listening drill')).toBeVisible();
    await page.reload();
    await expect(page.getByText('IELTS listening drill')).toBeVisible();
    await page.getByRole('button', { name: '编辑 IELTS listening drill' }).click();
    await page.getByLabel('记录内容').fill('Retried and corrected transcript');
    await page.getByRole('button', { name: '保存修改' }).click();
    await expect(page.getByText('Retried and corrected transcript')).toBeVisible();
    await page.getByLabel('搜索记录').fill('Retried');
    await expect(page.getByText('IELTS listening drill')).toBeVisible();
    await page.getByLabel('搜索记录').fill('absent phrase');
    await expect(page.getByText('IELTS listening drill')).toHaveCount(0);
    await page.getByRole('tab', { name: '回顾' }).click();
    await page.getByRole('button', { name: '写每日复盘' }).click();
    await page.getByLabel('开始时间').fill('2026-09-29T21:00');
    await page.getByRole('textbox', { name: '学习状态' }).fill('Focused after explaining the idea aloud');
    await page.getByRole('textbox', { name: '支持这个判断的证据' }).fill('Reconstructed the example without notes');
    await page.getByRole('button', { name: '保存记录' }).click();
    await expect(page.locator('.journal-review-list')).toContainText('Focused after explaining the idea aloud');
    await page.getByRole('tab', { name: '时间线' }).click();
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
    await page.getByRole('tab', { name: '今日计划' }).click();
    await expect(page.locator('.plans-grid')).toBeVisible();
    assert.equal(await page.locator('.plans-quadrant').count(), 4);
    console.log('PASS: journal create, reload, edit, search and plan view');
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
