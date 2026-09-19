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
      await page.route('**/api/chat/**', route => route.fulfill({ json: route.request().url().endsWith('/models') ? [] : { api_key_configured: false, base_url: '', temperature: 0.7 } }));
      await page.route('**/api/video/**', route => route.fulfill({ json: route.request().url().endsWith('/workflows') ? { workflows: [] } : { image_models: [], video_models: [], first_last_frame: { supported: false } } }));
      await page.route('**/api/plans', route => route.fulfill({ json: { revision: '0'.repeat(64), items: [] } }));
      await page.route('**/api/plans/reminders', route => route.fulfill({ json: { items: [] } }));
      await page.route('**/api/plans/reminders/**', route => route.fulfill({ json: { item: {} } }));
      await page.goto(`${baseUrl}/__react/admin`);
      await expect(page.locator('.overview-stats')).toBeVisible();
      const navGroups = await page.locator('.sidebar-nav > div').evaluateAll(groups => groups.map(group => ({
        label: group.querySelector('.sidebar-section')?.textContent?.trim() || '',
        hrefs: Array.from(group.querySelectorAll('a[href]'), link => new URL(link.href).pathname),
      })));
      assert.deepEqual(navGroups.map(group => group.label), ['工作台', '网络管理', '运维管理', '服务接入']);
      for (const path of ['/admin/plans', '/admin/chat', '/admin/video']) {
        assert.equal(navGroups.find(group => group.hrefs.includes(path))?.label, '工作台', `${path} belongs in the workbench group`);
      }
      assert.equal(navGroups.find(group => group.hrefs.includes('/admin/services'))?.label, '服务接入');
      assert.deepEqual(await page.locator('.sidebar-footer a[href]').evaluateAll(links => links.map(link => new URL(link.href).pathname)), ['/admin/settings']);
      await page.evaluate(() => { window.__navigationMarker = 'same-document'; });
      const mobile = viewport.width <= 880;
      if (mobile) {
        await page.locator('#sidebar-toggle').click();
        await expect.poll(async () => Math.round((await page.locator('.sidebar').boundingBox()).x)).toBe(0);
      }
      const baseline = await geometry(page);
      for (const path of ['/admin/plans', '/admin/chat', '/admin/video', '/admin']) {
        await page.locator(`.sidebar a[href="${path}"]`).click();
        await expect(page).toHaveURL(`${baseUrl}/__react${path}`);
        await expect(page.locator(`.sidebar a[href="${path}"]`)).toHaveAttribute('aria-current', 'page');
        if (path === '/admin/plans') await expect(page.getByRole('heading', { name: '今日计划' })).toBeVisible();
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
      assert.deepEqual(failures, []);
      await context.close();
    }

    const reminderContext = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await reminderContext.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
    const reminderPage = await reminderContext.newPage();
    const date = await reminderPage.evaluate(() => {
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
      const parts = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
      const get = type => parts.find(part => part.type === type)?.value || '00';
      return `${get('year')}-${get('month')}-${get('day')}`;
    });
    const task = { id: 'reminder-demo', title: '核验提醒跨页显示', notes: '', quadrant: 'important_urgent', plan_date: date,
      timezone: 'UTC', start_time: null, due_at: null, estimate_minutes: 30, reminder_at: '2026-09-18T00:00:00Z',
      status: 'todo', created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T00:00:00Z' };
    let due = [{ id: task.id, title: task.title, plan_date: task.plan_date, reminder_at: task.reminder_at }];
    let actionBody = null;
    await reminderPage.route('**/api/plans', route => route.fulfill({ json: { revision: '1'.repeat(64), items: [task] } }));
    await reminderPage.route('**/api/plans/reminders', route => route.fulfill({ json: { items: due } }));
    await reminderPage.route('**/api/plans/reminders/**', async route => {
      actionBody = route.request().postDataJSON();
      due = [];
      await route.fulfill({ json: { item: { ...task, reminder_at: null } } });
    });
    await reminderPage.goto(`${baseUrl}/__react/admin`);
    await expect(reminderPage.getByRole('complementary', { name: '今日计划提醒' })).toContainText(task.title);
    await reminderPage.locator('.sidebar a[href="/admin/plans"]').click();
    await expect(reminderPage.locator('.plans-global-reminders')).toContainText(task.title);
    await expect(reminderPage.locator('.plans-reminder-list')).toHaveCount(0, 'the global reminder must not be duplicated inside the Plans page');
    await reminderPage.getByRole('button', { name: '稍后', exact: true }).click();
    await expect.poll(() => actionBody?.action).toBe('snooze');
    await expect(reminderPage.locator('.plans-global-reminders')).toHaveCount(0);
    await reminderContext.close();

    console.log('Shared sidebar geometry and client navigation passed: desktop, iPad landscape/portrait, mobile');
    console.log('Global plan reminders display once across admin pages and persist actions');
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
