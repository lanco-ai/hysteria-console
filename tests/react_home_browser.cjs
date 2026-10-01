const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { expect } = require('@playwright/test');

const baseUrl = process.env.PREVIEW_BASE_URL;

async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const anonymousContext = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const anonymous = await anonymousContext.newPage();
    let chatRequests = 0;
    anonymous.on('request', request => {
      if (new URL(request.url()).pathname.startsWith('/api/chat/')) chatRequests += 1;
    });
    const sessionResponse = anonymous.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/session');
    await anonymous.goto(`${baseUrl}/__react/`);
    assert.equal((await sessionResponse).status(), 401);
    await expect(anonymous).toHaveTitle('购物 · Hysteria');
    await expect(anonymous.locator('.sidebar')).toHaveCount(0);
    await expect(anonymous.locator('nav[aria-label="主导航"] a')).toHaveText(['购物', 'AI 对话', 'AI 视频']);
    await expect(anonymous.getByRole('dialog')).toHaveCount(0);
    await expect(anonymous.getByRole('heading', { name: '暂无商品' })).toBeVisible();
    await anonymous.evaluate(() => { window.__portalMarker = 'same-document'; });
    await anonymous.getByRole('link', { name: 'AI 对话', exact: true }).click();
    await expect(anonymous).toHaveURL(`${baseUrl}/__react/?view=chat`);
    await expect(anonymous.locator('.chat-composer textarea')).toBeDisabled();
    await expect(anonymous.getByRole('dialog')).toHaveCount(0);
    await anonymous.getByRole('link', { name: 'AI 视频', exact: true }).click();
    await expect(anonymous.getByRole('heading', { name: '把想法，变成画面。' })).toBeVisible();
    await expect(anonymous.locator('.video-workspace')).toHaveCount(0);
    assert.equal(await anonymous.evaluate(() => window.__portalMarker), 'same-document');
    await anonymous.goBack();
    await expect(anonymous.locator('.chat-composer textarea')).toBeDisabled();
    await anonymous.goForward();
    await expect(anonymous).toHaveTitle('AI 视频 · Hysteria');
    assert.equal(chatRequests, 0, 'anonymous root must not call the chat API');

    for (const width of [390, 768, 1440]) {
      await anonymous.setViewportSize({ width, height: 900 });
      await anonymous.goto(`${baseUrl}/__react/`);
      await expect(anonymous.locator('.portal-nav')).toBeVisible();
      await expect(anonymous.getByText('创建账号', { exact: true })).toHaveCount(0);
      await expect(anonymous.getByText('订单查询', { exact: true })).toHaveCount(0);
      await expect(anonymous.getByText('公告', { exact: true })).toHaveCount(0);
      assert.equal(await anonymous.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, `${width}px must not overflow`);
      await anonymous.getByRole('searchbox', { name: '搜索商品' }).fill('test');
      await expect(anonymous.getByRole('heading', { name: '没有匹配的商品' })).toBeVisible();
      await anonymous.getByRole('button', { name: '清除搜索' }).click();
      await expect(anonymous.getByRole('heading', { name: '暂无商品' })).toBeVisible();
      if (process.env.REACT_HOME_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.REACT_HOME_SCREENSHOT_DIR, { recursive: true });
        await anonymous.screenshot({ path: path.join(process.env.REACT_HOME_SCREENSHOT_DIR, `portal-shop-${width}.png`), fullPage: true });
      }
    }

    for (const alias of ['/__react/auth', '/__react/login', '/__react/user/login']) {
      await anonymous.goto(`${baseUrl}${alias}`);
      await expect(anonymous.locator('.portal-header')).toHaveCount(1);
      await expect(anonymous.getByRole('dialog')).toBeVisible();
    }
    await expect(anonymous.getByRole('dialog', { name: '登录用户面板' })).toBeVisible();
    await anonymousContext.close();

    const authenticatedContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    await authenticatedContext.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
    const authenticated = await authenticatedContext.newPage();
    await authenticated.route('**/api/chat/settings', route => route.fulfill({ json: { temperature: 0.7, api_key_configured: true } }));
    await authenticated.route('**/api/chat/models', route => route.fulfill({ json: [{ id: 'preview-model', name: 'Preview model' }] }));
    await authenticated.goto(`${baseUrl}/__react/?view=chat`);
    await expect(authenticated.locator('.portal-header')).toHaveCount(1);
    await expect(authenticated.getByRole('dialog')).toHaveCount(0);
    await expect(authenticated.locator('.chat-composer textarea')).toBeEnabled();
    await expect(authenticated.locator('.sidebar')).toHaveCount(0);
    await expect(authenticated.getByRole('button', { name: '新对话', exact: true })).toBeEnabled();
    await authenticated.getByRole('button', { name: '新对话', exact: true }).click();
    await expect(authenticated).toHaveURL(`${baseUrl}/__react/?view=chat`);
    await authenticated.reload();
    await expect(authenticated.locator('.chat-composer textarea')).toBeEnabled();
    await authenticated.getByRole('link', { name: '管理后台' }).click();
    await expect(authenticated.locator('.sidebar')).toHaveCount(1);
    await expect(authenticated.locator('.sidebar a[href="/admin/chat"], .sidebar a[href="/admin/video"]')).toHaveCount(0);
    await authenticatedContext.close();

    const videoContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const video = await videoContext.newPage();
    await video.route('**/api/video/workflows', route => route.fulfill({ json: { workflows: [] } }));
    await video.route('**/api/video/capabilities', route => route.fulfill({ json: { image_models: [], video_models: [], first_last_frame: { supported: false } } }));
    await video.route('**/api/chat/settings', route => route.fulfill({ json: { temperature: 0.7, api_key_configured: true } }));
    await video.route('**/api/chat/models', route => route.fulfill({ json: [{ id: 'preview-model', name: 'Preview model' }] }));
    let videoRequests = 0;
    video.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/video/')) videoRequests += 1; });
    await video.goto(`${baseUrl}/__react/?view=video`);
    await expect(video.getByRole('heading', { name: '把想法，变成画面。' })).toBeVisible();
    assert.equal(videoRequests, 0, 'guest video view must not request private workflows');
    await video.getByRole('button', { name: '登录', exact: true }).click();
    await expect(video.locator('.portal').locator('..')).toHaveAttribute('inert', '');
    await expect(video.locator('#login-modal-username')).toBeFocused();
    await video.locator('#login-modal-username').fill('admin');
    await video.locator('#login-modal-password').fill(process.env.REACT_PREVIEW_LOGIN_PASSWORD);
    await video.getByRole('button', { name: '登录', exact: true }).click();
    await expect(video).toHaveURL(`${baseUrl}/__react/?view=video`);
    await expect(video.locator('.video-canvas-editor')).toBeVisible();
    await expect(video.locator('.sidebar')).toHaveCount(0);
    await video.reload();
    await expect(video.locator('.video-canvas-editor')).toBeVisible();
    await expect(video.locator('.react-flow')).toBeVisible();
    for (const width of [390, 768, 1440]) {
      await video.setViewportSize({ width, height: 900 });
      assert.equal(await video.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, `video ${width}px must not overflow`);
      if (process.env.REACT_HOME_SCREENSHOT_DIR) await video.screenshot({ path: path.join(process.env.REACT_HOME_SCREENSHOT_DIR, `portal-video-${width}.png`), fullPage: true });
    }
    await video.getByRole('link', { name: 'AI 对话', exact: true }).click();
    await expect(video.locator('.chat-composer textarea')).toBeEnabled();
    await expect(video.getByLabel('当前模型')).toHaveValue('preview-model');
    for (const width of [390, 768, 1440]) {
      await video.setViewportSize({ width, height: 900 });
      assert.equal(await video.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, `chat ${width}px must not overflow`);
      if (process.env.REACT_HOME_SCREENSHOT_DIR) await video.screenshot({ path: path.join(process.env.REACT_HOME_SCREENSHOT_DIR, `portal-chat-${width}.png`), fullPage: true });
    }
    await videoContext.close();
    console.log('PASS: public portal navigation, guest permissions, auth aliases and management return');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
