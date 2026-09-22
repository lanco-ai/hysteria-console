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
    let rejectNextSave = false;
    let conflictNextSave = false;
    let conflictServerItems = null;
    let loseNextSaveResponse = false;
    let committedSaveConflict = false;
    let heldRead = null;
    let failNextRead = false;
    const suggestionIds = [];
    const createReadGate = () => {
      let signalStarted;
      let release;
      const started = new Promise(resolve => { signalStarted = resolve; });
      const gate = new Promise(resolve => { release = resolve; });
      return { started, signalStarted, gate, release };
    };
    await page.route('**/api/plans/reminders', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [] }) }));
    await page.route('**/api/plans/assistant', async route => {
      assistantRequests += 1;
      const payload = route.request().postDataJSON();
      assert.equal(payload.date, '2026-09-19');
      assert.equal(payload.timezone, 'America/Los_Angeles');
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({
        model: 'gemini-preview-fast', service_name: 'Gemini', structured_output: 'gemini_native_schema', summary: '优先处理关键任务。',
        suggestions: [{ title: assistantRequests === 1 ? '完成项目提纲' : '补充项目提纲', notes: '拆成三个小步骤。', quadrant: 'important', start_time: '10:30', estimate_minutes: 45, reminder_offset_minutes: 10, reason: '为后续工作建立结构。' }],
      }) });
    });
    await page.route('**/api/plans', async route => {
      if (route.request().method() === 'PUT') {
        saveRequests += 1;
        const payload = route.request().postDataJSON();
        suggestionIds.push(payload.items.filter(item => ['完成项目提纲', '补充项目提纲'].includes(item.title)).map(item => item.id)[0] || '');
        if (rejectNextSave) {
          rejectNextSave = false;
          await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'plans_unavailable' }) });
          return;
        }
        if (loseNextSaveResponse) {
          loseNextSaveResponse = false;
          savedItems = payload.items;
          await route.abort('failed');
          return;
        }
        if (committedSaveConflict) {
          committedSaveConflict = false;
          conflictServerItems = [...savedItems];
          await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: 'plan_revision_conflict' }) });
          return;
        }
        if (conflictNextSave) {
          conflictNextSave = false;
          conflictServerItems = [...savedItems, { ...savedItems[0], id: 'remote-conflict-task', title: '服务器新增任务' }];
          await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: 'plan_revision_conflict' }) });
          return;
        }
        savedItems = payload.items;
        conflictServerItems = null;
        if (heldSave) {
          const current = heldSave;
          heldSave = null;
          current.signalStarted();
          await current.gate;
        }
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: savedItems, revision: 'b'.repeat(64) }) });
      } else {
        if (failNextRead) {
          failNextRead = false;
          await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'plans_unavailable' }) });
          return;
        }
        if (heldRead) {
          const current = heldRead;
          heldRead = null;
          current.signalStarted();
          await current.gate;
        }
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: conflictServerItems || savedItems, revision: 'a'.repeat(64) }) });
      }
    });

    const initialRead = createReadGate();
    heldRead = initialRead;
    await page.goto(`${base}/admin/plans`);
    await expect(page.getByRole('heading', { name: '今日计划' })).toBeVisible();
    await initialRead.started;
    await expect(page.getByLabel('计划标题')).toBeDisabled();
    await expect(page.getByRole('button', { name: '添加', exact: true })).toBeDisabled();
    initialRead.release();
    await expect(page.locator('.plans-save-status')).toHaveText('已保存');
    failNextRead = true;
    await page.getByRole('button', { name: '刷新' }).click();
    await expect(page.locator('.plans-save-status')).toHaveText('计划读取失败');
    await expect(page.getByLabel('计划标题')).toBeDisabled();
    await expect(page.getByRole('button', { name: '添加', exact: true })).toBeDisabled();
    await page.getByRole('button', { name: '刷新' }).click();
    await expect(page.locator('.plans-save-status')).toHaveText('已保存');
    await expect(page.getByLabel('计划标题')).toBeEnabled();
    await page.getByLabel('计划标题').fill('未提交的表单草稿');
    const formUnloadPrevented = await page.evaluate(() => {
      const event = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    });
    assert.equal(formUnloadPrevented, true, 'a typed form draft must be protected before it is added as a plan');
    await page.getByLabel('计划标题').fill('');
    await page.getByLabel('计划标题').fill('刷新期间编辑');
    await page.getByRole('button', { name: '添加', exact: true }).click();
    failNextRead = true;
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: '放弃修改' }).click();
    await expect(page.locator('.plans-save-status')).toHaveText('计划读取失败');
    await expect(page.getByText('刷新期间编辑', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '重试读取' })).toBeVisible();
    page.once('dialog', dialog => dialog.dismiss());
    await page.getByRole('button', { name: '重试读取' }).click();
    await expect(page.getByText('刷新期间编辑', { exact: true })).toBeVisible();
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: '重试读取' }).click();
    await expect(page.locator('.plans-save-status')).toHaveText('已保存');
    await expect(page.getByText('刷新期间编辑', { exact: true })).toHaveCount(0);

    await page.getByLabel('计划标题').fill('刷新期间编辑');
    await page.getByRole('button', { name: '添加', exact: true }).click();
    const reloadRead = createReadGate();
    heldRead = reloadRead;
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: '放弃修改' }).click();
    await reloadRead.started;
    await expect(page.getByLabel('计划标题')).toBeDisabled();
    await expect(page.getByRole('button', { name: '添加', exact: true })).toBeDisabled();
    reloadRead.release();
    await expect(page.locator('.plans-save-status')).toHaveText('已保存');
    await expect(page.getByText('刷新期间编辑', { exact: true })).toHaveCount(0);
    await page.getByLabel('计划日期').fill('2026-09-19');
    await page.getByRole('button', { name: 'AI 建议' }).click();
    await page.getByLabel('告诉 Gemini 你的目标').fill('今天先完成项目方案，再留时间运动。');
    await page.getByRole('button', { name: '生成建议' }).click();
    await expect(page.getByText('优先处理关键任务。')).toBeVisible();
    await expect(page.getByLabel('建议任务标题')).toHaveValue('完成项目提纲');
    assert.equal(saveRequests, 0, 'previewing suggestions must not persist or alter plan data');

    rejectNextSave = true;
    await page.getByRole('button', { name: '添加选中建议并保存' }).click();
    await expect(page.locator('.plans-save-status')).toHaveText('保存失败');
    await expect(page.getByRole('region', { name: 'AI 每日计划建议' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'AI 每日计划建议' }).getByRole('alert')).toContainText('计划暂时无法读取或保存');
    assert.deepEqual(savedItems, [], 'a failed acknowledgement must not close or apply the preview');

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
    await page.getByRole('button', { name: '关闭', exact: true }).click();
    await page.getByLabel('计划标题').fill('保存期间编辑保留');
    await page.getByRole('button', { name: '添加', exact: true }).click();
    releaseSave();
    await expect(saveStatus).toHaveText('未保存');
    await expect(page.locator('.plans-save-feedback')).toHaveText('保存期间的新修改尚未同步，请再次保存');
    await expect(page.getByText('完成项目提纲', { exact: true })).toBeVisible();
    await expect(page.getByText('保存期间编辑保留', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '刷新' })).toBeDisabled();
    await expect(page.getByRole('region', { name: 'Gemini 每日计划建议' })).toHaveCount(0);
    assert.equal(assistantRequests, 1);
    assert.equal(saveRequests, 2);
    assert.equal(suggestionIds[0], suggestionIds[1], 'retrying a suggestion after failure must reuse its task ID');
    assert.equal(savedItems.length, 1);
    assert.equal(savedItems[0].title, '完成项目提纲');
    assert.equal(savedItems[0].quadrant, 'important');
    assert.equal(savedItems[0].start_time, '10:30');

    await page.getByRole('button', { name: '保存计划' }).click();
    await expect(saveStatus).toHaveText('已保存');
    assert.equal(saveRequests, 3);
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
    await expect(saveStatus).toHaveText('未保存');
    await expect(page.locator('.plans-save-feedback')).toHaveText('保存期间的新修改尚未同步，请再次保存');
    await expect(raceCheckbox).toBeChecked();
    await page.getByRole('button', { name: '保存计划' }).click();
    await expect(saveStatus).toHaveText('已保存');
    assert.equal(savedItems.find(item => item.title === '完成项目提纲').status, 'done');
    assert.equal(savedItems.length, 3);

    await page.getByLabel('计划标题').fill('失败后保留');
    await page.getByRole('button', { name: '添加', exact: true }).click();
    await expect(saveStatus).toHaveText('未保存');
    const unloadPrevented = await page.evaluate(() => {
      const event = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    });
    assert.equal(unloadPrevented, true, 'dirty plans must register the browser unload warning');
    page.once('dialog', dialog => dialog.dismiss());
    await page.locator('.plans-footer').getByRole('link', { name: '服务中心' }).click();
    await expect(page).toHaveURL(/\/admin\/plans$/);
    rejectNextSave = true;
    await page.getByRole('button', { name: '保存计划' }).click();
    await expect(saveStatus).toHaveText('保存失败');
    await expect(page.getByText('失败后保留', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '保存计划' }).click();
    await expect(saveStatus).toHaveText('已保存');
    await page.reload();
    await page.getByLabel('计划日期').fill('2026-09-19');
    await expect(page.getByText('失败后保留', { exact: true })).toBeVisible();

    await page.getByLabel('计划标题').fill('本地冲突草稿');
    await page.getByRole('button', { name: '添加', exact: true }).click();
    conflictNextSave = true;
    await page.getByRole('button', { name: '保存计划' }).click();
    await expect(saveStatus).toHaveText('保存失败');
    failNextRead = true;
    await page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: '加载服务器最新版本' }).click();
    await expect(saveStatus).toHaveText('计划读取失败');
    await expect(page.getByText('本地冲突草稿', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '重试读取' })).toBeVisible();
    page.once('dialog', dialog => dialog.dismiss());
    await page.getByRole('button', { name: '重试读取' }).click();
    await expect(page.getByText('本地冲突草稿', { exact: true })).toBeVisible();
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: '重试读取' }).click();
    await expect(page.getByText('服务器新增任务', { exact: true })).toBeVisible();
    await expect(page.getByText('本地冲突草稿', { exact: true })).toHaveCount(0);
    const conflictUnloadPrevented = await page.evaluate(() => {
      const event = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    });
    assert.equal(conflictUnloadPrevented, true, 'a preserved conflict draft must remain protected after loading server state');
    page.once('dialog', dialog => dialog.dismiss());
    await page.locator('.plans-footer').getByRole('link', { name: '服务中心' }).click();
    await expect(page).toHaveURL(/\/admin\/plans$/);
    await page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: '恢复本地冲突草稿' }).click();
    await expect(page.getByText('本地冲突草稿', { exact: true })).toBeVisible();
    await expect(page.getByText('服务器新增任务', { exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: '保存计划' }).click();
    await expect(saveStatus).toHaveText('已保存');
    assert(savedItems.some(item => item.title === '本地冲突草稿'));
    assert.equal(savedItems.some(item => item.title === '服务器新增任务'), false);

    await page.getByRole('button', { name: 'AI 建议' }).click();
    await page.getByLabel('告诉 Gemini 你的目标').fill('再次安排项目提纲');
    await page.getByRole('button', { name: '生成建议' }).click();
    await expect(page.getByText('优先处理关键任务。')).toBeVisible();
    loseNextSaveResponse = true;
    await page.getByRole('button', { name: '添加选中建议并保存' }).click();
    await expect(saveStatus).toHaveText('保存失败');
    const committedSuggestionId = suggestionIds.at(-1);
    assert(savedItems.some(item => item.id === committedSuggestionId));
    committedSaveConflict = true;
    await page.getByRole('button', { name: '添加选中建议并保存' }).click();
    await expect(page.getByRole('button', { name: '加载服务器最新版本' })).toBeVisible();
    await page.getByRole('button', { name: '关闭', exact: true }).click();
    await page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: '加载服务器最新版本' }).click();
    await expect(page.locator('.plans-grid').getByText('补充项目提纲', { exact: true })).toHaveCount(1);
    const savesBeforeDedupe = saveRequests;
    await page.getByRole('button', { name: 'AI 建议' }).click();
    await page.getByLabel('建议任务标题').fill('补充项目提纲（已调整）');
    await page.getByRole('button', { name: '添加选中建议并保存' }).click();
    await expect(page.getByRole('region', { name: 'AI 每日计划建议' })).toHaveCount(0);
    await expect(page.locator('.plans-grid').getByText('补充项目提纲', { exact: true })).toHaveCount(1);
    await expect(page.locator('.plans-grid').getByText('补充项目提纲（已调整）', { exact: true })).toHaveCount(0);
    assert.equal(saveRequests, savesBeforeDedupe, 'retry after a lost acknowledgement must not submit a duplicate task ID');
    assert.equal(suggestionIds.at(-1), committedSuggestionId, 'the same suggestion ID must be reused across a lost-ack retry');
    await page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: '丢弃本地草稿' }).click();

    await page.getByLabel('计划标题').fill('确认离开后仍保留服务器版本');
    await page.getByRole('button', { name: '添加', exact: true }).click();
    let navigationDialogs = 0;
    const acceptNavigationDialog = async (dialog) => { navigationDialogs += 1; await dialog.accept(); };
    page.on('dialog', acceptNavigationDialog);
    await page.locator('.plans-footer').getByRole('link', { name: '服务中心' }).click();
    await expect(page).toHaveURL(/\/admin\/services\?tab=ai$/);
    assert.equal(navigationDialogs, 1, 'accepting the in-app leave warning must not trigger a second unload prompt');
    page.off('dialog', acceptNavigationDialog);

    await page.getByRole('link', { name: '今日计划' }).click();
    await expect(page).toHaveURL(/\/admin\/plans$/);
    await page.getByLabel('计划标题').fill('返回取消后保留');
    const historyDraftGuarded = await page.evaluate(() => {
      const event = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    });
    assert.equal(historyDraftGuarded, true, 'the Back test must start with a protected form draft');
    page.once('dialog', dialog => dialog.dismiss());
    await page.goBack();
    await expect(page).toHaveURL(/\/admin\/plans$/);
    await expect(page.getByLabel('计划标题')).toHaveValue('返回取消后保留');
    page.once('dialog', dialog => dialog.accept());
    await page.goBack();
    await expect(page).toHaveURL(/\/admin\/services\?tab=ai$/);

    await page.goForward();
    await expect(page).toHaveURL(/\/admin\/plans$/);
    await page.locator('.plans-footer').getByRole('link', { name: '服务中心' }).click();
    await expect(page).toHaveURL(/\/admin\/services\?tab=ai$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/admin\/plans$/);
    await page.getByLabel('计划标题').fill('前进取消后保留');
    page.once('dialog', dialog => dialog.dismiss());
    await page.goForward();
    await expect(page).toHaveURL(/\/admin\/plans$/);
    await expect(page.getByLabel('计划标题')).toHaveValue('前进取消后保留');
    page.once('dialog', dialog => dialog.accept());
    await page.goForward();
    await expect(page).toHaveURL(/\/admin\/services\?tab=ai$/);

    await page.getByRole('link', { name: '服务中心', exact: true }).click();
    await expect(page).toHaveURL(/\/admin\/services$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/admin\/services\?tab=ai$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/admin\/plans$/);
    await page.getByLabel('计划标题').fill('跨过未标记历史项后仍保留');
    page.once('dialog', dialog => dialog.dismiss());
    await page.evaluate(() => window.history.go(2));
    await expect(page).toHaveURL(/\/admin\/plans$/);
    await expect(page.getByLabel('计划标题')).toHaveValue('跨过未标记历史项后仍保留');
    page.once('dialog', dialog => dialog.accept());
    await page.evaluate(() => window.history.go(2));
    await expect(page).toHaveURL(/\/admin\/services$/);

    await page.getByRole('link', { name: '今日计划' }).click();
    await expect(page).toHaveURL(/\/admin\/plans$/);
    await page.getByLabel('计划标题').fill('跳转锚点后保留草稿');
    let skipLinkPrompts = 0;
    const dismissSkipLinkPrompt = async dialog => { skipLinkPrompts += 1; await dialog.dismiss(); };
    page.on('dialog', dismissSkipLinkPrompt);
    await page.getByRole('link', { name: '跳到主内容' }).evaluate(link => link.click());
    await expect(page).toHaveURL(/\/admin\/plans#main-content$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/admin\/plans$/);
    await expect(page.getByLabel('计划标题')).toHaveValue('跳转锚点后保留草稿');
    page.off('dialog', dismissSkipLinkPrompt);
    assert.equal(skipLinkPrompts, 0, 'same-page fragment history must not ask to leave Plans');
    await page.goForward();
    await expect(page).toHaveURL(/\/admin\/plans#main-content$/);
    await expect(page.getByLabel('计划标题')).toHaveValue('跳转锚点后保留草稿');
    page.once('dialog', dialog => dialog.accept());
    await page.locator('.plans-footer').getByRole('link', { name: '服务中心' }).click();
    await expect(page).toHaveURL(/\/admin\/services\?tab=ai$/);
    await page.getByRole('link', { name: '今日计划' }).click();
    await expect(page).toHaveURL(/\/admin\/plans$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/admin\/services\?tab=ai$/);

    await page.getByRole('link', { name: '今日计划' }).click();
    await expect(page).toHaveURL(/\/admin\/plans$/);
    await page.getByLabel('计划标题').fill('多步历史取消后仍保留锚点');
    await page.getByRole('link', { name: '跳到主内容' }).evaluate(link => link.click());
    await expect(page).toHaveURL(/\/admin\/plans#main-content$/);
    page.once('dialog', dialog => dialog.dismiss());
    await page.evaluate(() => window.history.go(-2));
    await expect(page).toHaveURL(/\/admin\/plans#main-content$/);
    await expect(page.getByLabel('计划标题')).toHaveValue('多步历史取消后仍保留锚点');

    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    console.log('PASS: plan suggestions merge edits made during saves; refresh protects dirty state');
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
