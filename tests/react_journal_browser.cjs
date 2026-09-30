const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');
const path = require('node:path');
const fs = require('node:fs');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const base = process.env.PREVIEW_BASE_URL;
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, timezoneId: 'America/Los_Angeles' });
    await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.clock.install({ time: new Date('2026-09-29T17:00:00Z') });
    await page.goto(`${base}/admin/plans#daily-timeline`);
    const box = page.getByLabel('记录内容');
    await expect(page.locator('.journal-timeline-primary textarea[aria-label="记录内容"]')).toBeVisible();
    await expect(box).toHaveAttribute('placeholder', '这一天暂无记录。可以先记下一件小事。');
    await expect(page.locator('.journal-editor, .journal-extra-fields, .journal-optional')).toHaveCount(0);
    for (const label of ['记录类型', '开始时间', '结束时间', '记录标题']) await expect(page.getByLabel(label, { exact: true })).toHaveCount(0);
    for (const name of ['写记录', '保存记录']) await expect(page.getByRole('button', { name, exact: true })).toHaveCount(0);
    await expect(page.getByText('关闭面板后，草稿保留在当前页面。')).toHaveCount(0);
    await expect(page.getByText('时间与至少一项内容必填。')).toHaveCount(0);

    async function snapshot(width, name) {
      await page.setViewportSize({ width, height: 900 });
      await page.evaluate(() => document.fonts.ready);
      if (width < 1100) await expect.poll(() => page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().right)).toBeLessThan(1);
      const layout = await page.locator('#daily-journal-drawer').evaluate(element => {
        const drawer = element.getBoundingClientRect();
        const title = element.querySelector('#journal-drawer-title').getBoundingClientRect();
        return { fit: element.scrollWidth <= element.clientWidth, right: drawer.right, centerDifference: title.x + title.width / 2 - drawer.x - drawer.width / 2 };
      });
      assert(layout.fit && layout.right <= width + 1 && Math.abs(layout.centerDifference) < 2, `drawer should fit and center the heading at ${width}px`);
      const date = await page.getByLabel('记录日期').boundingBox();
      const category = await page.getByLabel('筛选分类').boundingBox();
      assert(Math.abs(date.y - category.y) < 2, 'date and category stay on the same toolbar row');
      assert(await page.locator('.journal-drawer-body').evaluate(element => element.scrollWidth <= element.clientWidth));
      if (process.env.REACT_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, `journal-direct-${name}-${width}.png`) });
      }
    }
    for (const width of [390, 768, 1440]) await snapshot(width, 'empty');
    await box.fill('A small thing today\nA second line');
    await page.clock.runFor(1300);
    await expect(page.locator('#journal-autosave-state')).toHaveText('已自动保存');
    await expect(box).toHaveValue('A small thing today\nA second line');
    await expect(page.locator('.journal-timeline li')).toHaveCount(0); // The active record is shown once, in the editor.
    let records = (await (await context.request.get(`${base}/api/journal?date=2026-09-29`)).json()).items;
    assert.equal(records.length, 1);
    const id = records[0].id;
    await box.fill('A small thing today\nContinued without a second record');
    await page.clock.runFor(1300);
    await expect(page.locator('#journal-autosave-state')).toHaveText('已自动保存');
    records = (await (await context.request.get(`${base}/api/journal?date=2026-09-29`)).json()).items;
    assert.equal(records.length, 1); assert.equal(records[0].id, id); assert.equal(records[0].revision, 2);
    for (const width of [390, 768, 1440]) await snapshot(width, 'writing');
    await page.reload();
    await expect(page.locator('.journal-timeline')).toContainText('Continued without a second record');
    await expect(box).toHaveValue('');

    const fixture = {
      kind: 'ielts', occurred_at: '2026-09-30T02:00:00Z', ended_at: '2026-09-30T02:30:00Z', timezone: 'America/Los_Angeles',
      title: 'Historical listening drill', body: 'Old listening notes', skill: 'listening', material: 'Episode one', correction: 'Review plural endings',
    };
    const created = await context.request.post(`${base}/api/journal`, { data: fixture });
    assert(created.ok()); const historical = (await created.json()).item;
    await page.reload();
    await expect(page.locator('.journal-details')).toContainText('Review plural endings');
    await page.getByRole('button', { name: '编辑 Historical listening drill' }).click();
    await expect(box).toBeFocused();
    await box.fill('Updated listening notes');
    await page.clock.runFor(1300);
    await expect(page.locator('#journal-autosave-state')).toHaveText('已自动保存');
    records = (await (await context.request.get(`${base}/api/journal?date=2026-09-29`)).json()).items;
    const updated = records.find(item => item.id === historical.id);
    for (const field of ['kind', 'title', 'skill', 'material', 'correction', 'occurred_at', 'ended_at']) assert.equal(updated[field], historical[field], `editing must retain ${field}`);
    assert.equal(updated.body, 'Updated listening notes');
    await page.getByRole('button', { name: '另记一条' }).click();
    await expect(box).toHaveValue('');
    await page.getByLabel('搜索记录').fill('plural endings');
    await expect(page.locator('.journal-timeline li')).toHaveCount(1);
    await expect(page.locator('.journal-filter-note').first()).toContainText('全部日期');
    await page.getByLabel('筛选分类').selectOption('paper');
    await expect(page.locator('.journal-timeline li')).toHaveCount(0);
    await expect(page.getByText('没有其他符合筛选条件的记录。')).toBeVisible();
    await page.getByLabel('搜索记录').fill('');
    await page.getByLabel('记录日期').fill('2026-09-28');
    await box.fill('A paper note on the selected day');
    await page.clock.runFor(1300);
    await expect(page.locator('#journal-autosave-state')).toHaveText('已自动保存');
    const paper = (await (await context.request.get(`${base}/api/journal?date=2026-09-28&kind=paper`)).json()).items;
    assert.equal(paper.length, 1); assert.equal(paper[0].local_date, '2026-09-28');
    await page.getByRole('button', { name: '关闭记录面板' }).click();
    await page.getByRole('navigation', { name: '今日页面内容' }).getByRole('link', { name: '回顾' }).click();
    await expect(page.locator('.journal-review')).toBeVisible();
    await expect(page.locator('.journal-counts')).toContainText('论文阅读：1');
    await expect(page.locator('.journal-counts')).toContainText('雅思练习：1');
    await expect(page.locator('.journal-review textarea')).toHaveCount(1);
    await page.getByRole('button', { name: '导出 JSON' }).focus();
    await page.keyboard.press('Tab');
    await expect(page.getByRole('button', { name: '关闭记录面板' })).toBeFocused();
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: '导出 JSON' }).click();
    assert.equal((await download).suggestedFilename(), 'life-learning-journal.json');
    assert.deepEqual(errors, []);
    await context.close();
    console.log('PASS: direct content box, removed form, autosave persistence, metadata, search/category, recap/export and 390/768/1440px layout');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
