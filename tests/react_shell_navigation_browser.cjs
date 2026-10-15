const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { expect } = require('@playwright/test');

const baseUrl = process.env.PREVIEW_BASE_URL;

async function geometry(page) {
  return page.evaluate(() => {
    const inspect = selector => {
      const element = document.querySelector(selector);
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width),
        height: Math.round(rect.height), padding: style.padding, background: style.backgroundColor };
    };
    return { sidebar: inspect('.sidebar'), nav: inspect('.sidebar-nav'),
      link: inspect('a[href="/admin/health"]'), footer: inspect('.sidebar-footer'),
      title: { x: Math.round(document.querySelector('.page-title').getBoundingClientRect().x) } };
  });
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 1024, height: 768 }, { width: 820, height: 1180 }, { width: 390, height: 844 }]) {
      const context = await browser.newContext({ viewport });
      await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
      const page = await context.newPage();
    const failures = [];
      page.on('pageerror', error => failures.push(error.message));
      // Workspace list endpoints use the isolated preview backend; only model
      // configuration needs a stub for this navigation/layout test.
      await page.route('**/api/chat/models', route => route.fulfill({ json: [] }));
      await page.route('**/api/chat/settings', route => route.fulfill({ json: { api_key_configured: false, base_url: '', temperature: 0.7 } }));
      await page.route('**/api/video/**', route => route.fulfill({ json: route.request().url().endsWith('/workflows') ? { workflows: [] } : { image_models: [], video_models: [], first_last_frame: { supported: false } } }));
      await page.route('**/api/plans', route => route.fulfill({ json: { revision: '0'.repeat(64), items: [] } }));
      await page.route('**/api/plans/reminders', route => route.fulfill({ json: { items: [] } }));
      await page.route('**/api/plans/reminders/**', route => route.fulfill({ json: { item: {} } }));
      await page.goto(`${baseUrl}/__react/admin`);
      await expect(page.locator('.overview-cycle')).toBeVisible();
      const navGroups = await page.locator('.sidebar-nav > div').evaluateAll(groups => groups.map(group => ({
        label: group.querySelector('.sidebar-section')?.textContent?.trim() || '',
        hrefs: Array.from(group.querySelectorAll('a[href]'), link => new URL(link.href).pathname),
      })));
      assert.deepEqual(navGroups.map(group => group.label), ['网络管理']);
      assert.deepEqual(navGroups.find(group => group.label === '网络管理')?.hrefs, ['/admin', '/admin/usage', '/admin/config', '/admin/health']);
      assert.equal(navGroups.find(group => group.hrefs.includes('/admin/github-trending')), undefined, 'GitHub trending has moved to the public portal');
      assert.deepEqual(await page.locator('.sidebar-footer a[href]').evaluateAll(links => links.map(link => new URL(link.href).pathname)), ['/admin/settings']);
      // Each entry has its own icon, so the collapsed rail stays readable.
      const icons = await page.locator('.sidebar a.sidebar-link svg').evaluateAll(svgs => svgs.map(svg => svg.innerHTML));
      assert.equal(new Set(icons).size, icons.length, 'sidebar icons must be distinct');
      assert.equal(await page.locator('.sidebar-home').getAttribute('href'), '/admin');
      await page.evaluate(() => { window.__navigationMarker = 'same-document'; });
      const mobile = viewport.width <= 880;
      if (mobile) {
        await page.locator('#sidebar-toggle').click();
        await expect.poll(async () => Math.round((await page.locator('.sidebar').boundingBox()).x)).toBe(0);
      }
      const baseline = await geometry(page);
      // This service's own console pages switch in the same document; 今日计划,
      // 商品管理 and the AI tools belong to the other services (full page loads).
      // Not /admin/health: geometry() samples that link, which must stay inactive.
      for (const path of ['/admin/usage', '/admin/config', '/admin']) {
        await page.locator(`.sidebar-link[href="${path}"]`).click();
        await expect(page).toHaveURL(`${baseUrl}/__react${path}`);
        await expect(page.locator(`.sidebar-link[href="${path}"]`)).toHaveAttribute('aria-current', 'page');
        if (path === '/admin/usage') await expect(page.getByRole('heading', { name: '流量分析', level: 1 })).toBeVisible();
        if (mobile) {
          await page.locator('#sidebar-toggle').click();
          await expect.poll(async () => Math.round((await page.locator('.sidebar').boundingBox()).x)).toBe(0);
        }
        assert.deepEqual(await geometry(page), baseline, `${viewport.width}px shell geometry must stay stable on ${path}`);
        assert.equal(await page.evaluate(() => window.__navigationMarker), 'same-document');
        if (!mobile) {
          await page.locator('#sidebar-collapse').click();
          await expect.poll(async () => Math.round((await page.locator('.sidebar').boundingBox()).width)).toBe(64);
          await page.locator('#sidebar-collapse').click();
        }
      }
      if (viewport.width === 1024) {
        // Another page opens at its top even when the previous one was scrolled
        // (here /admin is taller than the viewport, so the offset would survive).
        await page.locator('.sidebar-link[href="/admin/usage"]').click();
        await expect(page).toHaveURL(`${baseUrl}/__react/admin/usage`);
        // Retry until the content has rendered and the page can scroll.
        await expect.poll(() => page.evaluate(() => { window.scrollTo({ top: 400, behavior: 'instant' }); return window.scrollY; })).toBeGreaterThan(0);
        await page.locator('.sidebar-link[href="/admin"]').click();
        await expect(page).toHaveURL(`${baseUrl}/__react/admin`);
        await expect(page.locator('.overview-cycle')).toBeVisible();
        assert.equal(await page.evaluate(() => window.scrollY), 0);
      }
      assert.deepEqual(failures, []);
      await context.close();
    }

    const staleContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await staleContext.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
    const stalePage = await staleContext.newPage();
    await stalePage.route('**/api/plans/reminders', route => route.fulfill({ json: { items: [] } }));
    let blockedChunk;
    await stalePage.route(/\/assets\/UsagePage-[^/]+\.js$/, route => { blockedChunk = route; });
    await stalePage.goto(`${baseUrl}/__react/admin`);
    await expect(stalePage.locator('.overview-cycle')).toBeVisible();
    // The background preload has asked for 流量分析's code; it stays pending.
    await expect.poll(() => Boolean(blockedChunk)).toBe(true);
    await stalePage.locator('.sidebar-link[href="/admin/usage"]').click();
    await stalePage.locator('.sidebar-link[href="/admin/health"]').click();
    await expect(stalePage).toHaveURL(`${baseUrl}/__react/admin/health`);
    await blockedChunk.abort('failed');
    await stalePage.waitForTimeout(1500);
    await expect(stalePage).toHaveURL(`${baseUrl}/__react/admin/health`);
    await expect(stalePage.getByRole('tab', { name: '健康状态' })).toHaveAttribute('aria-selected', 'true');
    await staleContext.close();

    const reminderContext = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await reminderContext.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
    const reminderPage = await reminderContext.newPage();
    const foreignRequests = [];
    await reminderPage.route('**/api/plans**', async route => {
      foreignRequests.push(route.request().url());
      await route.fulfill({ json: { items: [{ id: 'foreign-reminder', title: '工作台提醒', plan_date: '2026-10-15', reminder_at: '2026-10-15T00:00:00Z' }] } });
    });
    await reminderPage.goto(`${baseUrl}/__react/admin`);
    await expect(reminderPage.locator('.overview-cycle')).toBeVisible();
    await expect(reminderPage.locator('.plans-global-reminders')).toHaveCount(0);
    await reminderPage.locator('.sidebar-link[href="/admin/usage"]').click();
    await expect(reminderPage).toHaveURL(`${baseUrl}/__react/admin/usage`);
    await expect(reminderPage.getByRole('heading', { name: '流量分析', level: 1 })).toBeVisible();
    await expect(reminderPage.locator('.plans-global-reminders')).toHaveCount(0);
    assert.deepEqual(foreignRequests, [], 'network pages must not poll workbench reminders');
    await reminderContext.close();

    console.log('Shared sidebar geometry and client navigation passed: desktop, iPad landscape/portrait, mobile');
    console.log('Network-only navigation does not load workbench reminders');
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
