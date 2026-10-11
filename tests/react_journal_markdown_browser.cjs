const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');

// Record text is the user's own Markdown. However it is written, the page stays usable: deep nesting
// falls back to plain text, wide content scrolls inside its card, footnotes stay within their record,
// and a zone name the browser does not know shows times in UTC instead of failing the page or export.
const blank = {
  title: '', body: '', question: '', explanation: '', source: '', uncertainty: '', next_check: '', skill: null,
  material: '', raw_result: '', correction: '', previous_view: '', trigger_evidence: '', current_view: '',
  learning_state: '', evidence: '', takeaway: '',
};
const record = (n, body, extra = {}) => ({
  ...blank, id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`, revision: 1, kind: 'life',
  occurred_at: `2026-09-29T1${n}:00:00Z`, ended_at: null, timezone: 'America/Los_Angeles', local_date: '2026-09-29',
  created_at: '2026-09-29T10:00:00Z', updated_at: '2026-09-29T10:00:00Z', chat_source: null, body, ...extra,
});
const week = items => ({ week_start: '2026-09-28', week_end: '2026-10-04', total: items.length, counts: items.length ? { life: items.length } : {}, items });

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const base = process.env.PREVIEW_BASE_URL;
    async function open(dayItems, { summaries = [week(dayItems)], width = 1440, exportItems = dayItems } = {}) {
      const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'America/Los_Angeles', acceptDownloads: true });
      await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: base }]);
      const page = await context.newPage();
      const state = { errors: [], summaryCalls: 0 };
      page.on('pageerror', error => state.errors.push(error.message));
      await page.route('**/api/plans/reminders', route => route.fulfill({ json: { items: [] } }));
      await page.route(url => url.pathname === '/api/journal', route => route.request().method() === 'GET' ? route.fulfill({ json: { items: dayItems } }) : route.continue());
      await page.route(url => url.pathname === '/api/journal/summary', route => {
        const reply = summaries[Math.min(state.summaryCalls, summaries.length - 1)];
        state.summaryCalls += 1;
        return route.fulfill({ json: reply });
      });
      await page.route(url => url.pathname === '/api/journal/export', route => route.fulfill({ json: { items: exportItems } }));
      await page.clock.install({ time: new Date('2026-09-29T17:00:00Z') });
      await page.goto(`${base}/admin/plans#daily-timeline`);
      await expect(page.getByLabel('记录内容')).toBeVisible();
      return { context, page, state };
    }

    // Deep nesting is shown as plain text; the other records and the page still render.
    {
      const deep = record(1, `${'> '.repeat(4000)}deep quote`);
      const listed = record(2, `${'- '.repeat(3000)}deep list`);
      const emphasis = record(3, `${'*'.repeat(60)}deep emphasis${'*'.repeat(60)}`);
      const normal = record(4, 'Normal **bold** text\n\n- one\n- two');
      const { context, page, state } = await open([deep, listed, emphasis, normal]);
      await expect(page.locator('.journal-timeline > li')).toHaveCount(4);
      await expect(page.locator('.journal-markdown.is-plain')).toHaveCount(3);
      await expect(page.locator('.journal-markdown.is-plain').first()).toContainText('deep quote');
      await expect(page.locator('.journal-timeline strong', { hasText: 'bold' })).toBeVisible();
      assert.ok((await page.evaluate(() => document.body.innerText.length)) > 100, 'the page still shows its content');
      // The timeline's dots belong to its own items, not to lists inside a record.
      assert.equal(await page.locator('.journal-markdown li').first().evaluate(item => getComputedStyle(item, '::before').content), 'none');
      assert.deepEqual(state.errors, []);
      await context.close();
    }

    // A wide table and a long code line scroll inside their card on a phone.
    {
      const header = `| ${Array.from({ length: 20 }, (_, n) => `col${n}`).join(' | ')} |`;
      const rule = `|${' --- |'.repeat(20)}`;
      const row = `| ${Array.from({ length: 20 }, (_, n) => `value${n}`).join(' | ')} |`;
      const wide = record(1, `${header}\n${rule}\n${row}\n\n\`\`\`\n${'x'.repeat(2000)}\n\`\`\``);
      const { context, page } = await open([wide], { width: 390 });
      await expect(page.locator('.journal-markdown pre')).toBeAttached();
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
      assert.ok(await page.locator('.journal-markdown-table').evaluate(box => box.scrollWidth > box.clientWidth), 'the table scrolls inside the record');
      assert.ok(await page.locator('.journal-markdown pre').evaluate(box => box.scrollWidth > box.clientWidth), 'the code scrolls inside the record');
      await context.close();
    }

    // Footnotes in two records keep separate ids and move within the page without changing its view.
    {
      const first = record(1, 'First note[^a]\n\n[^a]: First footnote');
      const second = record(2, 'Second note[^a]\n\n[^a]: Second footnote');
      const { context, page } = await open([first, second]);
      const ids = await page.locator('[id]').evaluateAll(nodes => nodes.map(node => node.id));
      assert.equal(new Set(ids).size, ids.length, 'every id on the page is unique');
      const refs = page.locator('a[data-footnote-ref]');
      await expect(refs).toHaveCount(2);
      assert.deepEqual(await refs.evaluateAll(links => links.map(link => link.target)), ['', '']);
      const secondCard = page.locator('.journal-timeline > li').filter({ hasText: 'Second note' });
      const target = await secondCard.locator('a[data-footnote-ref]').getAttribute('href');
      assert.equal(await secondCard.locator(`[id="${target.slice(1)}"]`).count(), 1, "a footnote link points into its own record");
      await secondCard.locator('a[data-footnote-ref]').click();
      await expect(page).toHaveURL(/#daily-timeline$/);
      await expect(page.getByLabel('记录内容')).toBeVisible();
      await expect(secondCard.locator('.footnotes')).toContainText('Second footnote');
      await expect(secondCard.locator('.footnotes')).toContainText('脚注');
      await context.close();
    }

    // A zone name the browser does not know: the time shows in UTC and the export still works.
    {
      const local = record(1, 'Written with an unusual zone', { timezone: 'localtime', ended_at: '2026-09-30T02:30:00Z' });
      const { context, page, state } = await open([local], { exportItems: [local, record(2, 'Another record')] });
      await expect(page.locator('.journal-timeline time')).toContainText('UTC');
      const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: '导出 Markdown' }).click()]);
      const text = fs.readFileSync(await download.path(), 'utf8');
      assert.match(text, /### 11:00 UTC–9月30日 02:30 UTC · 生活/);
      assert.match(text, /Another record/);
      assert.deepEqual(state.errors, []);
      await context.close();
    }

    // When the day just read disagrees with the week's summary (another device wrote), the summary is
    // read again once, and not again while the two keep disagreeing.
    {
      const fromElsewhere = record(1, 'Added on another device');
      const caughtUp = await open([fromElsewhere], { summaries: [week([]), week([fromElsewhere])] });
      await expect(caughtUp.page.locator('.daily-review-section .daily-column-head small')).toHaveText('共 1 条记录');
      assert.equal(caughtUp.state.summaryCalls, 2);
      await caughtUp.context.close();
      const stuck = await open([fromElsewhere], { summaries: [week([])] });
      await expect.poll(() => stuck.state.summaryCalls).toBe(2);
      await stuck.page.clock.runFor(5000);
      assert.equal(stuck.state.summaryCalls, 2);
      await stuck.context.close();
    }
    console.log('PASS: deep Markdown falls back to text, wide content stays in its card, footnotes per record, unknown zones in UTC and the week summary catches up');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
