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
    let assistantRequests = 0;
    let saveRequests = 0;
    let savedItems = [];
    let heldSave = null;
    await page.route('**/api/plans/reminders', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [] }) }));
    await page.route('**/api/plans/assistant', async route => {
      assistantRequests += 1;
      const payload = route.request().postDataJSON();
      assert.equal(payload.date, '2026-09-19');
      assert.equal(payload.timezone, 'America/Los_Angeles');
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({
        model: 'gemini-preview-fast', service_name: 'Gemini', summary: '优先处理关键任务。',
        suggestions: [{ title: '完成项目提纲', notes: '拆成三个小步骤。', quadrant: 'important', start_time: '10:30', estimate_minutes: 45, reminder_offset_minutes: 10, reason: '为后续工作建立结构。' }],
      }) });
    });
    await page.route('**/api/plans', async route => {
      if (route.request().method() === 'PUT') {
        saveRequests += 1;
        const payload = route.request().postDataJSON();
        savedItems = payload.items;
        if (heldSave) {
          const current = heldSave;
          heldSave = null;
          current.signalStarted();
          await current.gate;
        }
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: savedItems, revision: 'b'.repeat(64) }) });
      } else {
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: savedItems, revision: 'a'.repeat(64) }) });
      }
    });

    await page.goto(`${base}/admin/plans`);
    await expect(page.getByRole('heading', { name: '今日计划' })).toBeVisible();
    await page.getByLabel('计划日期').fill('2026-09-19');
    await page.getByRole('button', { name: 'Gemini 建议' }).click();
    await page.getByLabel('告诉 Gemini 你的目标').fill('今天先完成项目方案，再留时间运动。');
    await page.getByRole('button', { name: '生成建议' }).click();
    await expect(page.getByText('优先处理关键任务。')).toBeVisible();
    await expect(page.getByLabel('建议任务标题')).toHaveValue('完成项目提纲');
    assert.equal(saveRequests, 0, 'previewing suggestions must not persist or alter plan data');

    let markSaveStarted;
    let releaseSave;
    const saveStarted = new Promise(resolve => { markSaveStarted = resolve; });
    const race = {
      signalStarted: () => markSaveStarted(),
      gate: new Promise(resolve => { releaseSave = resolve; }),
    };
    heldSave = race;
    await page.getByRole('button', { name: '添加选中建议并保存' }).click();
    await saveStarted;
    const saveStatus = page.locator('.plans-save-status');
    await expect(saveStatus).toHaveText('保存中…');
    await page.getByLabel('计划标题').fill('保存期间编辑保留');
    await page.getByRole('button', { name: '添加', exact: true }).click();
    releaseSave();
    await expect(saveStatus).toHaveText('保存完成；保存期间的新修改尚未同步，请再次保存');
    await expect(page.getByText('完成项目提纲', { exact: true })).toBeVisible();
    await expect(page.getByText('保存期间编辑保留', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '刷新' })).toBeDisabled();
    await expect(page.getByRole('region', { name: 'Gemini 每日计划建议' })).toHaveCount(0);
    assert.equal(assistantRequests, 1);
    assert.equal(saveRequests, 1);
    assert.equal(savedItems.length, 1);
    assert.equal(savedItems[0].title, '完成项目提纲');
    assert.equal(savedItems[0].quadrant, 'important');
    assert.equal(savedItems[0].start_time, '10:30');

    await page.getByRole('button', { name: '保存计划' }).click();
    await expect(page.getByText('计划已保存')).toBeVisible();
    assert.equal(saveRequests, 2);
    assert.equal(savedItems.length, 2);
    assert.deepEqual(new Set(savedItems.map(item => item.title)), new Set(['完成项目提纲', '保存期间编辑保留']));

    let markSecondSaveStarted;
    let releaseSecondSave;
    const secondSaveStarted = new Promise(resolve => { markSecondSaveStarted = resolve; });
    heldSave = {
      signalStarted: () => markSecondSaveStarted(),
      gate: new Promise(resolve => { releaseSecondSave = resolve; }),
    };
    await page.getByLabel('计划标题').fill('保存期间状态也保留');
    await page.getByRole('button', { name: '添加', exact: true }).click();
    await page.getByRole('button', { name: '保存计划' }).click();
    await secondSaveStarted;
    await expect(saveStatus).toHaveText('保存中…');
    const raceCheckbox = page.getByRole('region', { name: '重要不紧急' }).getByRole('checkbox').first();
    await raceCheckbox.check();
    releaseSecondSave();
    await expect(saveStatus).toHaveText('保存完成；保存期间的新修改尚未同步，请再次保存');
    await expect(raceCheckbox).toBeChecked();
    await page.getByRole('button', { name: '保存计划' }).click();
    await expect(page.getByText('计划已保存')).toBeVisible();
    assert.equal(savedItems.find(item => item.title === '完成项目提纲').status, 'done');
    assert.equal(savedItems.length, 3);

    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    console.log('PASS: plan suggestions merge edits made during saves; refresh protects dirty state');
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
