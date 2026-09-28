const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, hasTouch: true });
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: process.env.PREVIEW_BASE_URL }]);
    const page = await context.newPage();
    await page.route('**/api/chat/**', route => route.fulfill({ contentType: 'application/json', body: '[]' }));
    await page.route('**/api/v1/admin/rules', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ users: ['alice'], rules: [], packs: [] }) }));
    await page.goto(`${process.env.PREVIEW_BASE_URL}/__react/admin/chat`);
    const launcher = page.getByRole('button', { name: '打开 Lanco Agent' });
    const panel = page.locator('.lanco-agent');
    const close = page.getByRole('button', { name: '收起 Lanco Agent' });
    const settle = () => panel.evaluate(node => Promise.all(node.getAnimations().map(animation => animation.finished.catch(() => {}))));
    // Pause real browser animations at their midpoint to inspect the rendered state.
    await page.evaluate(() => {
      const animate = Element.prototype.animate;
      Element.prototype.animate = function (...args) {
        const animation = animate.apply(this, args);
        if (this.matches('.lanco-agent')) {
          animation.pause();
          animation.currentTime = Number(animation.effect.getTiming().duration) / 2;
        }
        return animation;
      };
    });
    await launcher.click();
    await expect.poll(() => panel.evaluate(node => node.getAnimations().length)).toBe(1);
    const entering = await panel.evaluate(node => ({ opacity: Number(getComputedStyle(node).opacity), duration: node.getAnimations()[0].effect.getTiming().duration }));
    assert(entering.opacity > 0 && entering.opacity < 1, 'opening panel must actually fade in');
    assert.equal(entering.duration, 200);
    await page.screenshot({ path: '/tmp/lanco-agent-opening.png' });
    await panel.evaluate(node => node.getAnimations().forEach(animation => animation.finish()));
    await panel.locator('textarea').fill('private draft');
    await close.click();
    await expect(panel).toHaveAttribute('inert', '');
    const leaving = await panel.evaluate(node => ({ opacity: Number(getComputedStyle(node).opacity), duration: node.getAnimations()[0].effect.getTiming().duration }));
    assert(leaving.opacity > 0 && leaving.opacity < 1, 'closing panel must actually fade out');
    assert.equal(leaving.duration, 150);
    await panel.evaluate(node => node.getAnimations().forEach(animation => animation.finish()));
    await expect(launcher).toBeVisible();
    await launcher.focus();
    await page.keyboard.press('Enter');
    await expect(panel).toBeVisible();
    await expect(panel.locator('textarea')).toHaveValue('');
    await panel.evaluate(node => node.getAnimations().forEach(animation => animation.finish()));
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await close.click();
    await expect(launcher).toBeVisible();
    const box = await launcher.boundingBox();
    await page.mouse.move(box.x + 28, box.y + 28);
    await page.mouse.down();
    await page.mouse.move(640, 400, { steps: 8 });
    await page.mouse.up();
    await expect(panel).toHaveCount(0);
    // Keyboard activation must not consume a stale drag-suppression flag.
    await launcher.focus();
    await page.keyboard.press('Enter');
    await expect(panel).toBeVisible();
    assert.equal(await panel.evaluate(node => node.getAnimations().length), 0);
    await page.getByRole('button', { name: '重置位置' }).click();
    await expect.poll(async () => { const bounds = await panel.boundingBox(); return Math.round(900 - bounds.y - bounds.height); }).toBe(24);
    await expect(page.getByRole('status')).toHaveText('位置已重置');
    await close.click();
    await launcher.click();
    await settle();
    const resetBounds = await panel.boundingBox();
    assert.equal(Math.round(900 - resetBounds.y - resetBounds.height), 24, 'reopen must keep the reset position');
    await page.setViewportSize({ width: 390, height: 844 });
    await close.click();
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await launcher.click();
    await expect.poll(() => panel.evaluate(node => node.getAnimations().length)).toBe(1);
    const mobile = await panel.evaluate(node => getComputedStyle(node).transform);
    assert(/^matrix\(1, 0, 0, 1, 0, /.test(mobile), 'mobile opening should slide without scaling');
    await panel.evaluate(node => node.getAnimations().forEach(animation => animation.finish()));
    await page.screenshot({ path: '/tmp/lanco-agent-mobile.png' });
    await close.click();
    await panel.evaluate(node => node.getAnimations().forEach(animation => animation.finish()));
    await expect(launcher).toBeVisible();
    const touchBox = await launcher.boundingBox();
    await page.touchscreen.tap(touchBox.x + 28, touchBox.y + 28);
    await expect(panel).toBeVisible();
    await panel.evaluate(node => node.getAnimations().forEach(animation => animation.finish()));
    console.log('Agent motion, single reset, keyboard after drag, reduced motion, mobile slide and single touch tap passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
