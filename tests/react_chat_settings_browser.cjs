const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

const base = process.env.PREVIEW_BASE_URL;
const screenshots = process.env.REACT_CHAT_SETTINGS_SCREENSHOT_DIR;

async function capture(page, filename) {
  if (!screenshots) return;
  fs.mkdirSync(screenshots, { recursive: true });
  await page.screenshot({ path: path.join(screenshots, filename) });
}

async function preferences(context) {
  const response = await context.request.get(`${base}/api/chat/workspace/preferences`);
  assert.equal(response.status(), 200);
  return response.json();
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    let settings = { temperature: 0.7, api_key_configured: true, service_name: 'Preview 服务' };
    const settingsWrites = [];
    await context.route('**/api/chat/settings', async route => {
      if (route.request().method() === 'PUT') {
        const body = JSON.parse(route.request().postData() || '{}');
        settingsWrites.push(body);
        settings = { ...settings, ...body };
      }
      await route.fulfill({ json: settings });
    });
    await context.route('**/api/chat/models', route => route.fulfill({ json: [
      { id: 'model-a', name: 'Model A' }, { id: 'model-b', name: 'Model B' }, { id: 'model-c', name: 'Model C' },
    ] }));
    await context.route('**/api/plans/reminders', route => route.fulfill({ json: { items: [] } }));
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${base}/__react/admin/chat`);
    const composer = page.getByLabel('聊天消息');
    await expect(composer).toBeEnabled();
    await expect(page.getByLabel('当前模型')).toHaveValue('model-a');

    // Defaults: model, reasoning and the shared temperature save together.
    const opener = page.getByRole('button', { name: 'AI 设置', exact: true });
    await opener.click();
    const dialog = page.getByRole('dialog', { name: 'AI 设置' });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('button', { name: '模型与默认值' })).toHaveAttribute('aria-current', 'page');
    await expect(dialog.getByText('已连接 · Preview 服务 · 3 个可用模型')).toBeVisible();
    const save = dialog.getByRole('button', { name: '保存设置' });
    await expect(save).toBeDisabled();
    await dialog.getByLabel('默认模型').selectOption('model-b');
    await dialog.getByLabel('默认思考强度').selectOption('high');
    await dialog.locator('#chat-temperature').fill('1.2');
    await expect(dialog.getByText('有未保存的修改')).toBeVisible();
    await capture(page, 'ai-settings-defaults-1440.png');
    await save.click();
    await expect(dialog.getByText('已保存，下一条消息开始生效')).toBeVisible();
    assert.deepEqual(settingsWrites, [{ temperature: 1.2 }]);
    let saved = await preferences(context);
    assert.equal(saved.default_model, 'model-b');
    assert.equal(saved.default_reasoning, 'high');
    assert.equal(saved.revision, 1);

    // Custom instructions are trimmed and kept on the server.
    await dialog.getByRole('button', { name: '自定义指令', exact: true }).click();
    const instructions = dialog.getByLabel('自定义指令', { exact: true });
    await instructions.fill('  回答尽量用中文。\n先给结论，再给依据。  ');
    await expect(dialog.getByText('/ 2000')).toBeVisible();
    await save.click();
    await expect(dialog.getByText('已保存，下一条消息开始生效')).toBeVisible();
    saved = await preferences(context);
    assert.equal(saved.instructions, '回答尽量用中文。\n先给结论，再给依据。');

    // Closing with unsaved edits asks first; declining keeps the dialog.
    await instructions.fill('这段修改不会保存');
    page.once('dialog', prompt => prompt.dismiss());
    await page.keyboard.press('Escape');
    await expect(dialog).toBeVisible();
    page.once('dialog', prompt => prompt.accept());
    await dialog.getByRole('button', { name: '关闭窗口', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(opener).toBeFocused();
    // New conversations start from the saved defaults.
    await expect(page.getByLabel('当前模型')).toHaveValue('model-b');
    await expect(page.getByLabel('思考强度')).toHaveValue('high');

    // A save from another window is reported in the dialog without losing these edits.
    await opener.click();
    await dialog.getByRole('button', { name: '自定义指令', exact: true }).click();
    await expect(instructions).toHaveValue(saved.instructions);
    const other = await context.request.put(`${base}/api/chat/workspace/preferences`, { data: { ...saved, instructions: '另一个窗口的指令', revision: saved.revision } });
    assert.equal(other.status(), 200);
    await instructions.fill('本窗口的新指令');
    await save.click();
    await expect(dialog.getByRole('alert')).toContainText('刚在其他窗口修改过');
    await expect(instructions).toHaveValue('本窗口的新指令');
    assert.equal((await preferences(context)).instructions, '另一个窗口的指令');
    await save.click();
    await expect(dialog.getByText('已保存，下一条消息开始生效')).toBeVisible();
    assert.equal((await preferences(context)).instructions, '本窗口的新指令');

    // Send key preference: Ctrl/⌘+Enter sends, plain Enter inserts a newline.
    await dialog.getByRole('button', { name: '输入习惯', exact: true }).click();
    await dialog.getByText('Ctrl / ⌘ + Enter 发送').click();
    await expect(dialog.getByRole('radio', { name: /Ctrl/ })).toBeChecked();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    assert.equal(await page.evaluate(() => localStorage.getItem('hy2.chat.send-key')), 'mod-enter');
    await expect(page.getByText('Ctrl / ⌘ + Enter 发送 · Enter 换行')).toBeVisible();
    await composer.fill('第一行');
    await composer.press('Enter');
    await expect(composer).toHaveValue('第一行\n');
    await composer.press('Control+Enter');
    await expect(page.locator('.chat-message-user')).toContainText('第一行');
    await expect(page.locator('.chat-message-assistant')).toContainText('LSM Tree');
    const conversation = (await (await context.request.get(`${base}/api/chat/conversations`)).json()).items[0];
    assert.equal(conversation.model, 'model-b');
    assert.equal(conversation.reasoningEffort, 'high');

    // A manual choice applies to this conversation only; 新对话 starts from the defaults again.
    await page.getByLabel('当前模型').fill('model-c');
    await page.locator('.chat-history-panel').getByRole('button', { name: '新对话', exact: true }).click();
    await expect(page.getByLabel('当前模型')).toHaveValue('model-b');
    await page.reload();
    await expect(composer).toBeEnabled();
    await expect(page.getByText('Ctrl / ⌘ + Enter 发送 · Enter 换行')).toBeVisible();

    // Usage moved into AI 设置.
    await opener.click();
    await dialog.getByRole('button', { name: '用量', exact: true }).click();
    await expect(dialog.locator('.ai-usage-grid dd').first()).toHaveText('1');
    await capture(page, 'ai-settings-usage-1440.png');
    await page.keyboard.press('Escape');

    // Phones: the section list becomes a scrollable tab row and nothing overflows.
    await page.setViewportSize({ width: 390, height: 844 });
    await opener.click();
    await expect(dialog).toBeVisible();
    const box = await dialog.boundingBox();
    assert(box && box.x >= 12 && box.x + box.width <= 378, 'AI settings fit a phone viewport');
    const tabs = await dialog.locator('.ai-settings-nav').boundingBox();
    const body = await dialog.locator('.ai-settings-body').boundingBox();
    assert(tabs && body && tabs.y + tabs.height <= body.y + 1, 'Section tabs sit above the settings on phones');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'No horizontal overflow');
    await capture(page, 'ai-settings-390.png');
    await page.keyboard.press('Escape');

    assert.deepEqual(errors, []);
    await context.close();
    console.log('PASS: AI 设置 defaults, custom instructions, conflict-safe save, unsaved-edit prompt, send key and usage');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
