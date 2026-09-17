const assert = require('node:assert/strict');
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
    await expect(anonymous).toHaveTitle('Hysteria 工作台');
    await expect(anonymous.locator('body')).toHaveClass(/has-shell/);
    await expect(anonymous.locator('.app')).toHaveCount(1);
    await expect(anonymous.locator('.site-main, .site-hero, .site-feature')).toHaveCount(0);
    await expect(anonymous.getByRole('dialog', { name: '登录控制台' })).toBeVisible();
    await expect(anonymous.locator('.chat-composer textarea')).toBeDisabled();
    assert.equal(chatRequests, 0, 'anonymous root must not call the chat API');

    for (const alias of ['/__react/auth', '/__react/login', '/__react/user/login']) {
      await anonymous.goto(`${baseUrl}${alias}`);
      await expect(anonymous.locator('.app')).toHaveCount(1);
      await expect(anonymous.getByRole('dialog')).toBeVisible();
    }
    await expect(anonymous.getByRole('dialog', { name: '登录用户面板' })).toBeVisible();
    await anonymousContext.close();

    const authenticatedContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    await authenticatedContext.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
    const authenticated = await authenticatedContext.newPage();
    await authenticated.goto(`${baseUrl}/__react/`);
    await expect(authenticated.locator('.app')).toHaveCount(1);
    await expect(authenticated.getByRole('dialog')).toHaveCount(0);
    await expect(authenticated.locator('.chat-composer textarea')).toBeEnabled();
    await authenticated.locator('.sidebar-toggle').click();
    await expect(authenticated.locator('.sidebar.open')).toBeVisible();
    await authenticated.getByRole('button', { name: '关闭导航' }).click();
    await authenticatedContext.close();
    console.log('PASS: root and auth aliases use the guarded workbench');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
