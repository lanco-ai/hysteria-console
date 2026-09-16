const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { expect } = require('@playwright/test');

const baseUrl = process.env.PREVIEW_BASE_URL;

async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu', '--num-raster-threads=1', '--renderer-process-limit=2'] });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    permissions: ['clipboard-read', 'clipboard-write'],
  });
  await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
  const page = await context.newPage();
  let settings = {
    base_url: 'https://api.example.test/v1',
    temperature: 0.7,
    api_key_configured: true,
    api_key_masked: 'sk-…1234',
  };
  const putBodies = [];
  let expectedReasoning = undefined;
  await page.route('**/api/chat/settings', async route => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(settings) });
      return;
    }
    const body = JSON.parse(route.request().postData() || '{}');
    putBodies.push(body);
    settings = { ...settings, ...body };
    delete settings.api_key;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(settings) });
  });
  await page.route('**/api/chat/models', async route => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify([
      { id: 'model-a', name: 'Model A', context_window: 8192 },
      { id: 'model-b', name: 'Model B' },
    ]) });
  });
  await page.route('**/api/chat/test', async route => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true, message: 'Connected', models_count: 2 }) });
  });
  await page.route('**/api/chat/completions', async route => {
    const body = JSON.parse(route.request().postData() || '{}');
    assert.equal(body.model, 'model-a');
    if (expectedReasoning === undefined) assert.equal(Object.hasOwn(body, 'reasoning_effort'), false);
    else assert.equal(body.reasoning_effort, expectedReasoning);
    const last = body.messages?.at(-1)?.content || '';
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ choices: [{ message: { content: last.toUpperCase() } }] }) });
  });

  await page.goto(`${baseUrl}/__react/admin/chat`);
  await expect(page).toHaveTitle('AI 对话');
  await expect(page.getByRole('heading', { name: 'Lanco AI' })).toBeVisible();
  await expect(page.locator('.chat-toolbar-select').first()).toHaveValue('model-a');
  await expect(page.getByLabel('上下文状态')).toContainText('未知 / 8,192');

  await page.locator('button[aria-label="设置"]').click();
  await expect(page.getByRole('heading', { name: '设置' })).toBeVisible();
  await expect(page.locator('#chat-api-key')).toHaveValue('');
  await expect(page.locator('#chat-api-key')).toHaveAttribute('placeholder', 'sk-…1234');
  await page.getByRole('button', { name: '刷新模型' }).click();
  await expect(page.getByText('模型连接成功后会保存在当前会话的可用列表中')).toBeVisible();
  await page.getByRole('button', { name: '保存设置' }).click();
  await expect(page.getByText('设置已保存')).toBeVisible();
  assert(putBodies.every(body => !Object.hasOwn(body, 'model') && !Object.hasOwn(body, 'reasoning_effort')));
  await page.locator('button[aria-label="关闭设置"]').click();

  const composer = page.locator('.chat-composer textarea');
  await composer.fill('hello');
  await composer.press('Enter');
  await expect(page.locator('.chat-message-assistant .chat-message-content')).toContainText('HELLO');
  await page.locator('select[aria-label="思考强度"]').selectOption('high');
  expectedReasoning = 'high';
  await composer.fill('second');
  await composer.press('Enter');
  await expect(page.locator('.chat-message-assistant .chat-message-content').last()).toContainText('SECOND');
  await page.getByRole('button', { name: '复制' }).last().click();
  await expect(page.getByRole('button', { name: '已复制' })).toBeVisible();
  const stored = await page.evaluate(key => localStorage.getItem(key), 'hy2.chat.sessions.v1');
  assert(stored && !stored.includes('sk-…1234'));

  await page.reload();
  await expect(page.locator('.chat-message-assistant .chat-message-content').last()).toContainText('SECOND');
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '清空' }).click();
  await expect(page.getByRole('heading', { name: 'Lanco AI' })).toBeVisible();
  await expect(page.locator('.chat-empty-state')).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(page.locator('.chat-sidebar-open')).toBeVisible();
  await page.locator('.chat-sidebar-open').click();
  await expect(page.locator('.chat-sidebar.is-open')).toBeVisible();
  await page.locator('.chat-sidebar-close').click();

  await context.close();
  await browser.close();
  console.log('React chat browser acceptance passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
