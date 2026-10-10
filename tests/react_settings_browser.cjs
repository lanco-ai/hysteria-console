const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

const base = process.env.PREVIEW_BASE_URL;
const screenshots = process.env.REACT_SETTINGS_SCREENSHOT_DIR;
const TOKEN = '123456789:' + 'A'.repeat(35);

async function capture(page, filename) {
  if (!screenshots) return;
  fs.mkdirSync(screenshots, { recursive: true });
  await page.screenshot({ path: path.join(screenshots, filename), fullPage: true });
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    // The preview refuses alert writes, so the channel store is simulated here.
    let state = {
      telegram: { configured: false, chat_id: '' },
      webhook: { configured: false, host: '', signed: false },
      anomaly_z_threshold: 3,
      anomaly_min_gib: 1,
      revision: 'r0',
    };
    const saves = [];
    let override = null;
    let reads = 0;
    let tests = 0;
    await context.route('**/api/v1/admin/alerts', route => { reads += 1; return route.fulfill({ json: state }); });
    await context.route('**/api/v1/admin/alerts/save', route => {
      const form = Object.fromEntries(new URLSearchParams(route.request().postData() || ''));
      saves.push(form);
      if (override) { const reply = override; override = null; return route.fulfill({ status: reply.status, json: reply.body }); }
      const url = form.webhook_url ? new URL(form.webhook_url) : null;
      state = {
        telegram: form.telegram_enabled === '1' ? { configured: true, chat_id: form.telegram_chat_id } : { configured: false, chat_id: '' },
        webhook: form.webhook_enabled === '1'
          ? { configured: true, host: url ? url.hostname : state.webhook.host, signed: form.webhook_secret_clear === '1' ? false : Boolean(form.webhook_secret) || state.webhook.signed }
          : { configured: false, host: '', signed: false },
        anomaly_z_threshold: Number(form.anomaly_z_threshold),
        anomaly_min_gib: Number(form.anomaly_min_gib),
        revision: `r${saves.length}`,
      };
      return route.fulfill({ json: { ok: true, ...state } });
    });
    await context.route('**/api/v1/admin/health/test-alert', route => { tests += 1; return route.fulfill({ json: { ok: true, status: 'alert_dispatched' } }); });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${base}/__react/admin/settings`);
    await page.getByText('admin', { exact: true }).waitFor();

    // Section navigation beside the cards.
    const nav = page.getByRole('navigation', { name: '设置分类' });
    await expect(nav.getByRole('link')).toHaveCount(3);
    const navBox = await nav.boundingBox();
    const cardBox = await page.locator('#settings-account').boundingBox();
    assert(navBox && cardBox && navBox.x + navBox.width <= cardBox.x, 'Desktop section list sits beside the cards');
    await nav.getByRole('link', { name: /告警通知/ }).click();
    await expect(nav.getByRole('link', { name: /告警通知/ })).toHaveAttribute('aria-current', 'location');
    await expect(page.locator('#settings-alerts')).toBeInViewport();

    // Password helpers never submit anything.
    const fresh = page.locator('#settings-new-password');
    const confirm = page.locator('#settings-confirm-password');
    await fresh.fill('abc');
    await expect(page.locator('#settings-password-strength')).toHaveText('强度：太短，至少 8 位');
    await fresh.fill('Abcdefgh12345!xyz');
    await expect(page.locator('#settings-password-strength')).toHaveText('强度：强');
    await confirm.fill('Abcdefgh');
    await expect(page.locator('#settings-password-match')).toHaveText('两次输入还不一致');
    await confirm.fill('Abcdefgh12345!xyz');
    await expect(page.locator('#settings-password-match')).toHaveText('✓ 两次输入一致');
    const reveal = page.getByRole('button', { name: '显示密码' });
    await reveal.click();
    await expect(page.getByRole('button', { name: '隐藏密码' })).toHaveAttribute('aria-pressed', 'true');
    await expect(fresh).toHaveAttribute('type', 'text');
    await page.getByRole('button', { name: '隐藏密码' }).click();
    await expect(fresh).toHaveAttribute('type', 'password');
    await page.getByRole('button', { name: '生成随机密码' }).click();
    const generated = await fresh.inputValue();
    assert.equal(generated.length, 20);
    assert.equal(await confirm.inputValue(), generated);
    await expect(fresh).toHaveAttribute('type', 'text');
    await expect(page.locator('#settings-password-strength')).toHaveText(/强度：(较强|强)/);
    await page.getByRole('button', { name: '隐藏密码' }).click();

    // Alerts start unconfigured; enabling Telegram asks for both fields.
    const alerts = page.locator('#settings-alerts');
    await expect(alerts.getByText('Telegram · 未配置')).toBeVisible();
    await expect(alerts.getByRole('button', { name: '发送测试告警' })).toBeDisabled();
    await expect(alerts.getByRole('button', { name: '保存告警设置' })).toBeDisabled();
    await alerts.getByRole('switch', { name: '启用 Telegram 告警' }).check();
    const token = alerts.getByLabel('Bot Token');
    await expect(token).toHaveAttribute('placeholder', '123456789:AA…');
    await expect(token).toHaveAttribute('autocomplete', 'new-password');
    await token.fill(TOKEN);
    await alerts.getByLabel('Chat ID').fill('chat id');
    override = { status: 422, body: { ok: false, error: 'validation_error', code: 'telegram_chat_invalid' } };
    await alerts.getByRole('button', { name: '保存告警设置' }).click();
    await expect(alerts.getByRole('alert')).toContainText('Chat ID 应为数字 ID');
    await expect(token).toHaveValue(TOKEN);
    await alerts.getByLabel('Chat ID').fill('-1001234567890');
    await alerts.getByLabel('异常阈值（标准差倍数）').fill('4');
    await alerts.getByRole('button', { name: '保存告警设置' }).click();
    await expect(alerts.getByRole('status')).toContainText('告警设置已保存');
    assert.deepEqual(saves.at(-1), {
      telegram_enabled: '1', telegram_bot_token: TOKEN, telegram_chat_id: '-1001234567890',
      webhook_enabled: '', webhook_url: '', webhook_secret: '', webhook_secret_clear: '',
      anomaly_z_threshold: '4', anomaly_min_gib: '1', revision: 'r0',
    });
    await expect(alerts.getByText('Telegram · 已配置')).toBeVisible();
    // The stored token is write-only: the field is emptied and says so.
    await expect(token).toHaveValue('');
    await expect(token).toHaveAttribute('placeholder', '已保存，留空保持不变');
    assert.equal(await page.content().then(html => html.includes(TOKEN)), false, 'token is not kept in the page');

    // Test message goes through the saved channels.
    await alerts.getByRole('button', { name: '发送测试告警' }).click();
    await expect(alerts.getByRole('status')).toContainText('测试告警已在后台发送');
    assert.equal(tests, 1);

    // Webhook with a signing secret; blank fields keep what is stored.
    await alerts.getByRole('switch', { name: '启用 Webhook 告警' }).check();
    await alerts.getByLabel('地址（https）').fill('https://hooks.example.test/alerts?key=hidden');
    await alerts.getByLabel('签名密钥（可选）').fill('signing-secret');
    await alerts.getByRole('button', { name: '保存告警设置' }).click();
    await expect(alerts.getByText('Webhook · 已配置 · hooks.example.test')).toBeVisible();
    assert.equal(saves.at(-1).telegram_bot_token, '', 'a blank token keeps the stored one');
    assert.equal(saves.at(-1).revision, 'r2');
    await expect(alerts.getByLabel('地址（https）')).toHaveValue('');
    await expect(alerts.getByLabel('签名密钥（可选）')).toHaveAttribute('placeholder', '已保存，留空保持不变');
    await alerts.getByLabel('清除已保存的签名密钥').check();
    await expect(alerts.getByLabel('签名密钥（可选）')).toBeDisabled();
    await capture(page, 'settings-alerts-1440.png');

    // Another window saved first: nothing is written and the latest copy can be loaded.
    override = { status: 409, body: { ok: false, error: 'revision_conflict' } };
    await alerts.getByRole('button', { name: '保存告警设置' }).click();
    await expect(alerts.getByRole('alert')).toContainText('刚在其他窗口修改过');
    const before = reads;
    await alerts.getByRole('button', { name: '载入最新设置' }).click();
    await expect.poll(() => reads).toBeGreaterThan(before);
    await expect(alerts.getByLabel('清除已保存的签名密钥')).not.toBeChecked();
    await expect(alerts.getByRole('button', { name: '保存告警设置' })).toBeDisabled();

    // Turning a channel off removes it on save.
    await alerts.getByRole('switch', { name: '启用 Webhook 告警' }).uncheck();
    await expect(alerts.getByLabel('地址（https）')).toHaveCount(0);
    await alerts.getByRole('button', { name: '保存告警设置' }).click();
    await expect(alerts.getByText('Webhook · 未配置')).toBeVisible();
    assert.equal(saves.at(-1).webhook_enabled, '');
    assert.equal(saves.at(-1).webhook_url, '');

    // Interface preference keeps its stable id for the shell.
    await page.locator('#sidebar-motion-toggle').check();
    assert.equal(await page.evaluate(() => document.documentElement.classList.contains('sidebar-motion-enabled')), true);
    await page.locator('#sidebar-motion-toggle').uncheck();
    await capture(page, 'settings-1440.png');

    // Phones: the section list becomes one row of tabs and fields stack.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload();
    await page.getByText('admin', { exact: true }).waitFor();
    const links = await nav.getByRole('link').evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().y));
    assert(links.every(y => Math.abs(y - links[0]) < 2), 'Phone section tabs share one row');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'No horizontal overflow on phones');
    await expect(alerts.getByLabel('Chat ID')).toHaveValue('-1001234567890');
    // Measure both in one frame; the page may still be smooth-scrolling to the section.
    const stacked = await alerts.evaluate(node => {
      const token = node.querySelector('#alert-telegram-token').getBoundingClientRect();
      const chat = node.querySelector('#alert-telegram-chat').getBoundingClientRect();
      return chat.top >= token.bottom - 1 && Math.abs(chat.left - token.left) < 1;
    });
    assert(stacked, 'Phone fields stack');
    await capture(page, 'settings-390.png');

    assert.deepEqual(errors, []);
    await context.close();
    console.log('PASS: 设置 section nav, password helpers, write-only alert channels, conflict reload, test alert and phone layout');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
