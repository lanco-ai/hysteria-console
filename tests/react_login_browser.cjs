const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { expect } = require('@playwright/test');

const baseUrl = process.env.PREVIEW_BASE_URL;
const fixturePassword = process.env.REACT_PREVIEW_LOGIN_PASSWORD;
const dialog = '[role="dialog"][aria-labelledby="login-modal-title"]';

async function main() {
  assert(fixturePassword, 'preview password is required');
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const invalidContext = await browser.newContext();
    const invalid = await invalidContext.newPage();
    await invalid.goto(`${baseUrl}/__react/login`);
    await expect(invalid.locator(dialog)).toBeVisible();
    await expect(invalid.locator('#login-modal-username')).toBeFocused();
    await invalid.locator('#login-modal-username').fill('admin');
    await invalid.locator('#login-modal-password').fill('incorrect');
    await invalid.getByRole('button', { name: '登录', exact: true }).click();
    await expect(invalid.getByRole('alert')).toHaveText('用户名或密码错误');
    await expect(invalid).toHaveURL(`${baseUrl}/__react/login`);
    assert.equal(await invalid.locator('#login-modal-password').inputValue(), '');
    await invalidContext.close();

    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto(`${baseUrl}/__react/login?next=/admin/usage`);
    await page.locator('#login-modal-username').fill('admin');
    await page.locator('#login-modal-password').fill(fixturePassword);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page).toHaveURL(`${baseUrl}/__react/admin/usage`);
    await expect(page.locator('.app')).toHaveCount(1);
    const sid = (await context.cookies()).find(cookie => cookie.name === 'sid');
    assert(sid?.httpOnly, 'successful workbench login must set an HttpOnly session cookie');
    await page.reload();
    await expect(page.getByRole('dialog')).toHaveCount(0);

    const sensitiveContext = await browser.newContext();
    const sensitive = await sensitiveContext.newPage();
    await sensitive.goto(`${baseUrl}/__react/admin/usage?range=week&token=private-token&api_key=private-key&secret=private-secret`);
    await expect(sensitive.getByRole('dialog', { name: '登录控制台' })).toBeVisible();
    await sensitive.locator('#login-modal-username').fill('admin');
    await sensitive.locator('#login-modal-password').fill(fixturePassword);
    await sensitive.getByRole('button', { name: '登录', exact: true }).click();
    await expect(sensitive).toHaveURL(`${baseUrl}/__react/admin/usage?range=week`);
    await sensitiveContext.close();

    const userContext = await browser.newContext();
    await userContext.addCookies([{ name: 'usid', value: process.env.REACT_PREVIEW_USER_COOKIE, url: baseUrl }]);
    const userPage = await userContext.newPage();
    await userPage.goto(`${baseUrl}/__react/admin/usage`);
    await expect(userPage.getByRole('dialog', { name: '登录控制台' })).toBeVisible();
    await expect(userPage.locator('.app')).toHaveCount(1);
    await userContext.close();

    await page.goto(`${baseUrl}/__react/auth?next=https://attacker.invalid/`);
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.locator('#login-modal-username').fill('admin');
    await page.locator('#login-modal-password').fill(fixturePassword);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await expect(page).toHaveURL(`${baseUrl}/__react/`);

    await page.goto(`${baseUrl}/__react/user/login`);
    await expect(page.getByRole('dialog', { name: '登录用户面板' })).toBeVisible();
    await context.close();
    console.log('PASS: modal login handles failures, safe returns, and aliases');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
