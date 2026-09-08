const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function load(fetch) {
  const file = 'hysteria/static/ui-core.js';
  assert(fs.existsSync(file), 'Shared UI helpers must exist');
  const context = { fetch, AbortController, setTimeout, clearTimeout };
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), context);
  return context.Hy2UI;
}

test('formatting preserves zero variants, units and escaped text', () => {
  const ui = load();
  assert.equal(ui.formatBytes(1073741824), '1.00 GB');
  assert.equal(ui.formatBytes(-1), '0.00 B');
  assert.equal(ui.formatBytes(0, true), '0 B');
  assert.equal(ui.escapeHtml('<a title="x">&\''), '&lt;a title=&quot;x&quot;&gt;&amp;&#39;');
  assert.equal(ui.escapeHtml(null), '');
});

test('request returns response and preserves rejection', async () => {
  const result = { ok: true };
  assert.equal(await load(async () => result).fetchWithTimeout('/test', {}, 100), result);
  const error = new Error('offline');
  await assert.rejects(load(async () => { throw error; }).fetchWithTimeout('/test', {}, 100), e => e === error);
});

test('request timeout rejects and aborts the pending fetch', async () => {
  let signal;
  const ui = load((_url, options) => { signal = options.signal; return new Promise(() => {}); });
  await assert.rejects(ui.fetchWithTimeout('/test', {}, 10), error => error.code === 'timeout');
  assert.equal(signal.aborted, true);
});

test('caller cancellation aborts the request', async () => {
  const controller = new AbortController();
  const ui = load((_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('cancelled')), { once: true });
  }));
  const request = ui.fetchWithTimeout('/test', {}, 1000, controller);
  controller.abort();
  await assert.rejects(request, /cancelled/);
});
