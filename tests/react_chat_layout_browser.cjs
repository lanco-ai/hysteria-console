const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

const base = process.env.PREVIEW_BASE_URL;
const screenshots = process.env.REACT_CHAT_LAYOUT_SCREENSHOT_DIR;

async function assertCentered(dialog, width, height) {
  await expect(dialog).toBeVisible();
  const box = await dialog.boundingBox();
  assert(box, 'Dialog has a rendered box');
  assert(Math.abs(box.x + box.width / 2 - width / 2) <= 2, 'Dialog is horizontally centered');
  assert(Math.abs(box.y + box.height / 2 - height / 2) <= 2, 'Dialog is vertically centered');
  assert(box.x >= 12 && box.y >= 12, 'Dialog has space from viewport edges');
  assert(box.x + box.width <= width - 12 && box.y + box.height <= height - 12, 'Dialog fits within the viewport');
  assert(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth), 'No dialog horizontal overflow');
}

async function capture(page, filename) {
  if (!screenshots) return;
  fs.mkdirSync(screenshots, { recursive: true });
  await page.screenshot({ path: path.join(screenshots, filename), fullPage: true });
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    for (const width of [1440, 768, 390]) {
      const height = 900;
      const context = await browser.newContext({ viewport: { width, height } });
      await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
      await context.route('**/api/chat/settings', route => route.fulfill({ json: { temperature: 0.7, api_key_configured: true } }));
      await context.route('**/api/chat/models', route => route.fulfill({ json: [{ id: 'preview-model', name: 'Preview model' }] }));
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base + '/__react/?view=chat');
      await expect(page.getByLabel('聊天消息')).toBeEnabled();
      await page.evaluate(() => document.fonts.ready);

      const project = page.getByRole('button', { name: '＋ 项目', exact: true });
      await project.click();
      await assertCentered(page.getByRole('dialog', { name: '新建学习项目' }), width, height);
      await capture(page, `project-${width}.png`);
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toHaveCount(0);
      await expect(project).toBeFocused();

      const toolsButton = page.getByRole('button', { name: '搜索与工具', exact: true });
      const scope = page.getByLabel('回答使用的知识库范围');
      const toolsBox = await toolsButton.boundingBox();
      const scopeBox = await scope.boundingBox();
      const textareaBox = await page.getByLabel('聊天消息').boundingBox();
      assert(toolsBox && scopeBox && textareaBox);
      if (width >= 768) {
        assert(Math.abs(toolsBox.y + toolsBox.height / 2 - scopeBox.y - scopeBox.height / 2) <= 3, 'Desktop composer controls share one row');
      }
      assert(Math.max(toolsBox.y + toolsBox.height, scopeBox.y + scopeBox.height) <= textareaBox.y, 'Controls do not overlap the writing area');
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'No page horizontal overflow');
      await capture(page, `composer-${width}.png`);

      for (const [buttonName, title, filename] of [
        ['知识库', '个人知识库', 'knowledge'],
        ['AI 用量', 'AI 用量', 'usage'],
        ['搜索与工具', '搜索与工具', 'tools'],
      ]) {
        const opener = page.getByRole('button', { name: buttonName, exact: true });
        await opener.click();
        const dialog = page.getByRole('dialog', { name: title });
        await assertCentered(dialog, width, height);
        await capture(page, `${filename}-${width}.png`);
        if (title === '搜索与工具') {
          await dialog.getByRole('button', { name: 'MCP 连接', exact: true }).click();
          await page.setViewportSize({ width, height: 420 });
          await assertCentered(dialog, width, 420);
          const close = dialog.getByRole('button', { name: '关闭窗口', exact: true });
          const before = await close.boundingBox();
          const scroll = await dialog.locator('.workspace-dialog-body').evaluate(element => {
            element.scrollTop = element.scrollHeight;
            return { scrollTop: element.scrollTop, scrollHeight: element.scrollHeight, clientHeight: element.clientHeight };
          });
          assert(scroll.scrollTop > 0 && scroll.scrollHeight > scroll.clientHeight, 'Long dialog content scrolls internally');
          await expect(close).toBeInViewport();
          const after = await close.boundingBox();
          assert(before && after && Math.abs(before.y - after.y) < 1, 'Close control stays visible while content scrolls');
          await capture(page, `tools-short-viewport-${width}.png`);
          await close.click();
          await page.setViewportSize({ width, height });
        } else {
          await dialog.getByRole('button', { name: '关闭窗口', exact: true }).click();
        }
        await expect(page.getByRole('dialog')).toHaveCount(0);
        await expect(opener).toBeFocused();
      }
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log('PASS: centered chat dialogs, scrollable content, keyboard close/focus and aligned composer at 390/768/1440px');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
