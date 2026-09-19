const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { expect } = require('@playwright/test');

const baseUrl = process.env.PREVIEW_BASE_URL;
const secretSentinel = 'sk-workbench-browser-sentinel-9f7e';

async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu', '--num-raster-threads=1', '--renderer-process-limit=2'] });
  const anonymousContext = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const anonymousPage = await anonymousContext.newPage();
  let anonymousChatRequests = 0;
  await anonymousPage.addInitScript(() => {
    const watchedKeys = new Set(['hy2.chat.sessions.v1', 'hy2.chat.usage.v1']);
    window.__chatStorageCalls = 0;
    for (const method of ['getItem', 'setItem', 'removeItem']) {
      const original = Storage.prototype[method];
      Object.defineProperty(Storage.prototype, method, {
        configurable: true,
        value(key, ...values) {
          if (watchedKeys.has(key)) window.__chatStorageCalls += 1;
          return original.call(this, key, ...values);
        },
      });
    }
  });
  anonymousPage.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/chat/')) anonymousChatRequests += 1;
  });
  const anonymousSession = anonymousPage.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/session');
  await anonymousPage.goto(`${baseUrl}/__react/admin/chat`);
  assert.equal((await anonymousSession).status(), 401);
  await expect(anonymousPage.locator('.chat-composer textarea')).toBeDisabled();
  await expect(anonymousPage.locator('.chat-history-panel')).toHaveCount(0);
  await expect(anonymousPage.getByRole('button', { name: '打开历史记录' })).toBeVisible();
  await expect(anonymousPage.locator('button[aria-label="设置"]')).toBeDisabled();
  assert.equal(anonymousChatRequests, 0);
  const anonymousStorageCalls = await anonymousPage.evaluate(() => window.__chatStorageCalls);
  assert.equal(anonymousStorageCalls, 0);
  await anonymousContext.close();

  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    permissions: ['clipboard-read', 'clipboard-write'],
  });
  await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
  const page = await context.newPage();
  const browserResponseBodies = [];
  page.on('response', response => {
    const path = new URL(response.url()).pathname;
    if (path === '/__react/admin/chat' || path.startsWith('/api/chat/') || path.startsWith('/static/react/')) {
      browserResponseBodies.push(response.text().catch(() => ''));
    }
  });
  let settings = {
    base_url: 'https://api.example.test/v1',
    temperature: 0.7,
    api_key_configured: true,
    api_key_masked: 'sk-…1234',
  };
  const putBodies = [];
  let expectedReasoning = undefined;
  let expectedModel = 'gemini-3.8-flash-high';
  let modelLoads = 0;
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
    modelLoads += 1;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify([
      { id: 'gemini-3.8-flash-high', name: 'Gemini 3.8 Flash High' },
      { id: 'model-a', name: 'Model A', ...(modelLoads === 1 ? { context_window: 8192 } : {}) },
      { id: 'model-b', name: 'Model B' },
    ]) });
  });
  await page.route('**/api/chat/test', async route => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true, message: 'Connected', models_count: 2 }) });
  });
  await page.route('**/api/v1/admin/rules', async route => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ users: ['alice'], rules: [], revision: 'a'.repeat(64), packs: [] }) });
  });
  await page.route('**/api/v1/admin/agent/plan', async route => {
    const requestBody = JSON.parse(route.request().postData() || '{}');
    const deleteRule = String(requestBody.message || '').includes('删除');
    const directRule = deleteRule || String(requestBody.message || '').includes('添加');
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({
      ok: true,
      action: deleteRule ? 'delete_rule' : directRule ? 'add_rule' : 'apply_pack',
      target_user: 'alice',
      explanation: directRule ? '已生成用户规则预览。' : '已生成规则预览。',
      snapshot: {
        username: 'alice',
        revision: 'a'.repeat(64),
        rules: [],
        fake_ip_filter: [],
        tun_route_exclude_address: [],
        global_revision: 'g'.repeat(64),
        global_rules: ['DOMAIN-SUFFIX,global.example,DIRECT', 'MATCH,🚀 节点选择'],
        merged_rules: ['DOMAIN-SUFFIX,global.example,DIRECT', 'MATCH,🚀 节点选择'],
      },
      plan: {
        change_id: 'agent-browser-change',
        target_user: 'alice',
        operation: deleteRule ? 'delete' : directRule ? 'add' : 'pack',
        pack: directRule ? '' : 'overleaf',
        rule: directRule ? 'DOMAIN,example.com,DIRECT' : '',
        label: directRule ? '添加自定义规则' : 'Overleaf 加速',
        description: '仅影响 alice',
        before_revision: 'a'.repeat(64),
        after_revision: 'b'.repeat(64),
        additions: deleteRule ? [] : [directRule ? 'DOMAIN,example.com,DIRECT' : 'DOMAIN-SUFFIX,overleaf.com,🚀 节点选择'],
        removals: deleteRule ? ['DOMAIN,example.com,DIRECT'] : [],
        requires_confirmation: true,
      },
    }) });
  });
  let agentApplyAttempts = 0;
  let delayAgentSave = false;
  let releaseAgentSave;
  let agentSaveFinished;
  await page.route('**/api/v1/admin/agent/apply', async route => {
    agentApplyAttempts += 1;
    if (delayAgentSave) await new Promise(resolve => { releaseAgentSave = resolve; });
    if (agentApplyAttempts === 2) {
      await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ ok: false, error: 'upstream_unavailable' }) });
      return;
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true, result: { username: 'alice', revision: 'b'.repeat(64) } }) });
    agentSaveFinished?.();
  });
  await page.route('**/api/v1/admin/agent/undo', async route => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true, result: { username: 'alice', revision: 'c'.repeat(64) } }) });
  });
  await page.route('**/api/chat/completions', async route => {
    const body = JSON.parse(route.request().postData() || '{}');
    assert.equal(body.model, expectedModel);
    assert.equal(body.stream, true);
    if (expectedReasoning === undefined) assert.equal(Object.hasOwn(body, 'reasoning_effort'), false);
    else assert.equal(body.reasoning_effort, expectedReasoning);
    const last = body.messages?.at(-1)?.content || '';
    if (last === 'slow') {
      await new Promise(resolve => setTimeout(resolve, 1500));
      try { await route.fulfill({ contentType: 'text/event-stream', body: 'data: {"type":"done"}\n\n' }); } catch { /* The browser may have cancelled the request. */ }
      return;
    }
    const text = last.toUpperCase();
    await route.fulfill({
      contentType: 'text/event-stream',
      body: `data: ${JSON.stringify({ type: 'delta', text })}\n\ndata: ${JSON.stringify({ type: 'done' })}\n\n`,
    });
  });

  await page.goto(`${baseUrl}/__react/admin/chat`);
  await expect(page).toHaveTitle('AI 对话');
  await expect(page.getByRole('heading', { name: 'Lanco AI' })).toBeVisible();
  await expect(page.getByRole('button', { name: '打开 Lanco Agent' })).toBeVisible();
  await page.getByRole('button', { name: '打开 Lanco Agent' }).click();
  const agent = page.locator('.lanco-agent');
  await expect(agent).toBeVisible();
  assert.equal(await agent.evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(255, 255, 255)');
  await expect(page.getByText('你好，我可以帮你整理指定用户的网络规则。')).toBeVisible();
  await expect(page.getByRole('button', { name: '重置位置' })).toBeVisible();
  const launcher = page.getByRole('button', { name: '打开 Lanco Agent' });
  const beforeDrag = await agent.boundingBox();
  assert(beforeDrag, 'agent panel should have a bounding box');
  const header = page.locator('.lanco-agent-header');
  const headerBox = await header.boundingBox();
  assert(headerBox, 'agent header should have a bounding box');
  await page.mouse.move(headerBox.x + 80, headerBox.y + 20);
  await page.mouse.down();
  await page.mouse.move(headerBox.x - 100, headerBox.y - 40);
  await page.mouse.up();
  const afterDrag = await agent.boundingBox();
  assert(afterDrag && (afterDrag.x !== beforeDrag.x || afterDrag.y !== beforeDrag.y), 'agent panel should be draggable');
  await page.getByRole('button', { name: '收起 Lanco Agent' }).click();
  const launcherAfterPanelDrag = await launcher.boundingBox();
  assert(launcherAfterPanelDrag, 'agent launcher should be visible after closing the dragged panel');
  assert(launcherAfterPanelDrag.x !== 1200 || launcherAfterPanelDrag.y !== 820,
    'closing a dragged panel should keep the launcher anchored to that panel');
  await launcher.click();
  const reopenedPanel = await page.locator('.lanco-agent').boundingBox();
  assert(reopenedPanel && Math.abs(reopenedPanel.x - afterDrag.x) < 3 && Math.abs(reopenedPanel.y - afterDrag.y) < 3,
    'reopening should preserve the dragged panel position');
  await page.getByRole('button', { name: '重置位置' }).click();
  await page.locator('#lanco-agent-user').selectOption('alice');
  await expect(page.getByLabel('自动执行本用户规则')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '结束对话' })).toHaveCount(0);
  await page.locator('.lanco-agent-input').fill('给 alice 启用 Overleaf 加速');
  await page.getByRole('button', { name: '发送', exact: true }).last().click();
  await expect(page.locator('.lanco-agent-result')).toBeVisible();
  await expect(page.getByText('用户覆盖规则', { exact: true })).toBeVisible();
  await expect(page.getByText('继承全局规则', { exact: true })).toBeVisible();
  await expect(page.getByText('合并后订阅规则', { exact: true })).toBeVisible();
  await expect(page.getByText('用户覆盖规则', { exact: true }).locator('..').getByText('0 条', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '应用修改' })).toBeEnabled();
  await page.getByRole('button', { name: '收起 Lanco Agent' }).click();
  await launcher.click();
  await expect(page.locator('.lanco-agent-result')).toHaveCount(0);
  await expect(page.locator('.lanco-agent-input')).toHaveValue('');
  await page.locator('.lanco-agent-input').fill('给 alice 启用 Overleaf 加速');
  await page.getByRole('button', { name: '发送', exact: true }).last().click();
  await page.getByRole('button', { name: '应用修改' }).click();
  await expect(page.getByText(/已保存，用户下次拉取订阅时生效/)).toBeVisible();
  await page.getByRole('button', { name: '撤销这次修改' }).click();
  await expect(page.getByText('变更已撤销，规则恢复到修改前版本。')).toBeVisible();
  await page.getByRole('button', { name: '收起 Lanco Agent' }).click();
  await launcher.click();
  await expect(page.getByText('你好，我可以帮你整理指定用户的网络规则。')).toBeVisible();
  await expect(page.locator('.lanco-agent-input')).toHaveValue('');

  await page.locator('.lanco-agent-input').fill('给 alice 添加 example.com 直连');
  await page.getByRole('button', { name: '发送', exact: true }).last().click();
  await expect(page.getByText('模型服务暂时不可用，请稍后重试')).toBeVisible();
  await expect(page.getByRole('button', { name: '重试保存' })).toBeVisible();
  await page.getByRole('button', { name: '重试保存' }).click();
  await expect(page.getByText(/已保存，用户下次拉取订阅时生效/)).toBeVisible();
  await expect(page.getByRole('button', { name: '撤销这次修改' })).toBeVisible();
  await page.getByRole('button', { name: '收起 Lanco Agent' }).click();
  await expect(launcher).toBeVisible();
  const launcherBefore = await launcher.boundingBox();
  assert(launcherBefore, 'agent launcher should have a bounding box');
  await page.mouse.move(launcherBefore.x + 28, launcherBefore.y + 28);
  await page.mouse.down();
  await page.mouse.move(640, 450, { steps: 8 });
  await page.mouse.up();
  const launcherAfter = await launcher.boundingBox();
  assert(launcherAfter && (launcherAfter.x !== launcherBefore.x || launcherAfter.y !== launcherBefore.y), 'agent launcher should be draggable');
  await launcher.click();
  await expect(page.locator('.lanco-agent')).toBeVisible();
  const panelAfterLauncherDrag = await page.locator('.lanco-agent').boundingBox();
  assert(panelAfterLauncherDrag, 'agent panel should have a bounding box after opening from the moved launcher');
  assert(panelAfterLauncherDrag.x <= launcherAfter.x + launcherAfter.width + 12,
    'agent panel should open beside the moved launcher instead of returning to the old corner');
  assert(panelAfterLauncherDrag.y <= launcherAfter.y + launcherAfter.height + 12,
    'agent panel should open near the moved launcher vertically');
  await page.setViewportSize({ width: 800, height: 600 });
  const panelAfterResize = await page.locator('.lanco-agent').boundingBox();
  assert(panelAfterResize && panelAfterResize.x >= 0 && panelAfterResize.y >= 0
    && panelAfterResize.x + panelAfterResize.width <= 800
    && panelAfterResize.y + panelAfterResize.height <= 600,
  'agent panel should remain fully inside the viewport after resizing');
  await page.setViewportSize({ width: 1280, height: 900 });
  await expect(page.getByText('你好，我可以帮你整理指定用户的网络规则。')).toBeVisible();
  await expect(page.locator('.lanco-agent-input')).toHaveValue('');
  // Closing during a submitted save clears the UI; late responses cannot
  // overwrite the new conversation. The already-submitted write may finish.
  delayAgentSave = true;
  const finishedSave = new Promise(resolve => { agentSaveFinished = resolve; });
  await page.locator('.lanco-agent-input').fill('删除 alice 的 example.com 直连规则');
  await page.getByRole('button', { name: '发送', exact: true }).last().click();
  await expect.poll(() => typeof releaseAgentSave).toBe('function');
  await page.getByRole('button', { name: '收起 Lanco Agent' }).click();
  await launcher.click();
  await page.locator('.lanco-agent-input').fill('新对话草稿');
  releaseAgentSave();
  await finishedSave;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(page.locator('.lanco-agent-result')).toHaveCount(0);
  await expect(page.locator('.lanco-agent-input')).toHaveValue('新对话草稿');
  await expect(page.getByText('你好，我可以帮你整理指定用户的网络规则。')).toBeVisible();
  await page.getByRole('button', { name: '收起 Lanco Agent' }).click();
  await launcher.click();
  await expect(page.locator('.lanco-agent-input')).toHaveValue('');
  await page.getByRole('button', { name: '重置位置' }).click();
  await page.getByRole('button', { name: '收起 Lanco Agent' }).click();
  await expect(page.getByRole('button', { name: '打开 Lanco Agent' })).toBeVisible();
  await expect(page.locator('.chat-history-panel')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '打开历史记录' })).toContainText('历史');
  await expect(page.locator('.chat-history-count')).toHaveText('0');
  await expect(page.getByLabel('当前模型')).toBeEnabled();
  await expect(page.getByLabel('当前模型')).toHaveValue('gemini-3.8-flash-high');

  await page.locator('button[aria-label="设置"]').click();
  await expect(page.getByRole('heading', { name: '设置' })).toBeVisible();
  await expect(page.locator('#chat-api-key')).toHaveValue('');
  await expect(page.locator('#chat-api-key')).toHaveAttribute('placeholder', 'sk-…1234');
  await page.getByRole('button', { name: '刷新模型' }).click();
  await expect(page.getByText('模型连接成功后会保存在当前会话的可用列表中')).toBeVisible();
  await expect(page.getByLabel('上下文状态')).toContainText('未知 / 未知');
  await page.locator('#chat-api-key').fill(secretSentinel);
  await page.getByRole('button', { name: '保存设置' }).click();
  await expect(page.getByText('设置已保存')).toBeVisible();
  assert(putBodies.some(body => body.api_key === secretSentinel));
  assert(putBodies.every(body => !Object.hasOwn(body, 'model') && !Object.hasOwn(body, 'reasoning_effort')));
  await page.locator('button[aria-label="关闭设置"]').click();

  const composer = page.locator('.chat-composer textarea');
  await composer.fill('hello');
  await composer.press('Enter');
  await expect(page.locator('.chat-message-assistant .chat-message-content')).toContainText('HELLO');
  await expect(page.locator('.chat-history-count')).toHaveText('1');
  await page.locator('select[aria-label="思考强度"]').selectOption('high');
  expectedReasoning = 'high';
  await composer.fill('second');
  await composer.press('Enter');
  await expect(page.locator('.chat-message-assistant .chat-message-content').last()).toContainText('SECOND');

  const seededMessages = Array.from({ length: 24 }, (_, index) => ({
    role: index % 2 ? 'assistant' : 'user',
    content: `历史消息 ${index + 1} `.repeat(12),
  }));
  await page.evaluate(messages => {
    localStorage.setItem('hy2.chat.sessions.v1', JSON.stringify([{
      id: 'scroll-test',
      title: '滚动测试',
      messages,
      updatedAt: Date.now(),
      model: 'model-a',
      reasoningEffort: 'high',
    }]));
  }, seededMessages);
  expectedModel = 'model-a';
  await page.reload();
  await expect(page.locator('.chat-history-count')).toHaveText('1');
  await expect(page.getByLabel('当前模型')).toHaveValue('model-a');
  await composer.fill('scroll-check');
  await composer.press('Enter');
  await expect(page.locator('.chat-message-assistant .chat-message-content').last()).toContainText('SCROLL-CHECK');
  await page.waitForFunction(() => {
    const node = document.querySelector('.chat-messages');
    return node && node.scrollHeight - node.scrollTop - node.clientHeight <= 4;
  }, undefined, { timeout: 5000 });

  await page.getByRole('button', { name: '复制' }).last().click();
  await expect(page.getByRole('button', { name: '已复制' })).toBeVisible();
  await composer.fill('slow');
  await composer.press('Enter');
  await expect(page.getByRole('button', { name: '停止' })).toBeVisible();
  await expect(page.locator('.chat-message-assistant .chat-message-content').last()).toContainText('模型正在思考');
  await page.getByRole('button', { name: '停止' }).click();
  await expect(page.getByRole('status')).toContainText('已停止生成');
  const stored = await page.evaluate(key => localStorage.getItem(key), 'hy2.chat.sessions.v1');
  assert(stored && !stored.includes('sk-…1234'));
  const [html, localStorageValues, responseBodies] = await Promise.all([
    page.content(),
    page.evaluate(() => Object.keys(localStorage).map(key => localStorage.getItem(key))),
    Promise.all(browserResponseBodies),
  ]);
  assert.equal(html.includes(secretSentinel), false);
  assert.equal(JSON.stringify(localStorageValues).includes(secretSentinel), false);
  assert.equal(responseBodies.join('\n').includes(secretSentinel), false);

  await page.reload();
  await expect(page.locator('.chat-message-assistant .chat-message-content').last()).toContainText('SCROLL-CHECK');
  await expect(page.locator('.chat-history-panel')).toHaveCount(0);
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '清空' }).click();
  await expect(page.getByRole('heading', { name: 'Lanco AI' })).toBeVisible();
  await expect(page.locator('.chat-empty-state')).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(page.locator('.chat-history-panel')).toHaveCount(0);
  await expect(page.locator('.chat-history-toggle > span').first()).toBeHidden();
  await expect(page.locator('.chat-history-count')).toBeHidden();
  await page.getByRole('button', { name: '打开历史记录' }).click();
  await expect(page.locator('.chat-history-panel')).toBeVisible();
  await expect(page.locator('.chat-history')).toHaveCSS('display', 'flex');
  await expect(page.locator('.chat-session-list')).toHaveCSS('overflow-y', 'auto');
  await expect(page.locator('.chat-new-button')).toBeVisible();
  await page.locator('.chat-session').first().click();
  await expect(page.locator('.chat-history-panel')).toHaveCount(0);
  await expect(page.locator('.sidebar-toggle')).toBeVisible();
  await page.locator('.sidebar-toggle').click();
  await expect(page.locator('.sidebar.open')).toBeVisible();
  await page.getByRole('button', { name: '关闭导航' }).click();

  await context.close();
  await browser.close();
  console.log('React chat browser acceptance passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
