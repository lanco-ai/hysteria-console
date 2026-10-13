const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
const overview = { users: [], cycle: { total_used: 17 } };
const summary = bytes => ({ ts: '2026-10-13T12:00:00', stats: {
  current_hour_bytes: 3, today_bytes: 5, yesterday_bytes: 7, last_7d_bytes: 11,
  cycle_bytes: bytes, cycle_day: 2, cycle_total_days: 30, online: 1,
} });

// Execute the real hook; only the React scheduler and I/O boundary are replaced.
async function loadHook({ pendingOverview = false, pendingWrite = false } = {}) {
  const slots = [], effects = [], listeners = new Map(), timers = new Map();
  const summaryReads = [], overviewReads = [], writes = [];
  let cursor = 0, timerId = 0, mounted = false;
  const react = {
    useCallback: callback => callback,
    useEffect: effect => { if (!mounted) effects.push(effect); },
    useRef: value => {
      const index = cursor++;
      if (!slots[index]) slots[index] = { current: value };
      return slots[index];
    },
    useState: initial => {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      return [slots[index], value => {
        slots[index] = typeof value === 'function' ? value(slots[index]) : value;
      }];
    },
  };
  const setTimer = (callback, delay) => {
    const id = ++timerId;
    timers.set(id, { callback, delay });
    return id;
  };
  const document = {
    hidden: false,
    addEventListener: (name, handler) => listeners.set(`document:${name}`, handler),
    removeEventListener: name => listeners.delete(`document:${name}`),
  };
  const requests = {
    readJson: (url, signal) => {
      if (url === '/api/v1/admin/reload-status') return Promise.resolve(false);
      const request = { ...deferred(), url, signal };
      overviewReads.push(request);
      if (!pendingOverview) request.resolve(url.endsWith('overview-page') ? overview : { users: [], total_used: 17 });
      return request.promise;
    },
    readUsageSummary: signal => {
      const request = { ...deferred(), signal };
      summaryReads.push(request);
      return request.promise;
    },
    write: (action, fields, signal) => {
      const request = { ...deferred(), action, fields, signal };
      writes.push(request);
      if (!pendingWrite) request.resolve({ kind: 'success', code: 'reset usage all' });
      return request.promise;
    },
  };
  const context = vm.createContext({
    AbortController, URLSearchParams, location: { search: '' }, document,
    window: {
      addEventListener: (name, handler) => listeners.set(`window:${name}`, handler),
      removeEventListener: name => listeners.delete(`window:${name}`),
      setTimeout: setTimer, clearTimeout: id => timers.delete(id),
    },
    setTimeout: setTimer, clearTimeout: id => timers.delete(id),
    __react: react, __requests: requests,
  });
  const sourcePath = path.resolve(__dirname, '../frontend/src/features/network-admin/overview/useOverview.ts');
  const { transformWithOxc } = await import('vite');
  const { code } = await transformWithOxc(fs.readFileSync(sourcePath, 'utf8'), sourcePath, { lang: 'ts' });
  const module = new vm.SourceTextModule(code, { context, identifier: sourcePath });
  const resourceModule = new vm.SourceTextModule(`
    export class ResourceError extends Error {
      constructor(message, status) { super(message); this.status = status; }
    }
  `, { context });
  const modules = new Map([
    ['react', `
      export const useCallback = globalThis.__react.useCallback;
      export const useEffect = globalThis.__react.useEffect;
      export const useRef = globalThis.__react.useRef;
      export const useState = globalThis.__react.useState;
    `],
    ['./presentation', 'export const feedback = value => value;'],
    ['./requests', `
      export const parseOverview = value => value;
      export const parsePoll = value => value;
      export const parseReload = value => value;
      export const readJson = globalThis.__requests.readJson;
      export const write = globalThis.__requests.write;
    `],
    ['../usage/requests', 'export const readUsageSummary = globalThis.__requests.readUsageSummary;'],
  ]);
  await module.link(async specifier => {
    if (specifier === '../../../shared/readResource') return resourceModule;
    if (!modules.has(specifier)) throw new Error(`unexpected dependency: ${specifier}`);
    return new vm.SourceTextModule(modules.get(specifier), { context, identifier: specifier });
  });
  await module.evaluate();
  const render = () => { cursor = 0; return module.namespace.useOverview(); };
  render(); mounted = true;
  const cleanup = effects.map(effect => effect());
  await flush();
  return {
    render, summaryReads, overviewReads, writes, document,
    authError: () => new resourceModule.namespace.ResourceError('HTTP 401', 401),
    dispatch: (name, event = {}) => listeners.get(name)(event),
    tick: delay => {
      for (const [id, timer] of [...timers]) {
        if (timer.delay !== delay) continue;
        timers.delete(id); timer.callback();
      }
    },
    cleanup: () => cleanup.forEach(done => done?.()),
  };
}

test('summary loads separately while the user list is still pending', async () => {
  const h = await loadHook({ pendingOverview: true });
  assert.equal(h.summaryReads.length, 1);
  h.summaryReads[0].resolve(summary(123)); await flush();
  assert.equal(h.render().summary.data.stats.cycle_bytes, 123);
  assert.equal(h.render().loading, true);
  h.overviewReads[0].resolve(overview); await flush();
  assert.equal(h.render().data.cycle.total_used, 17);
  assert.equal(h.render().summary.data.stats.cycle_bytes, 123, 'global total must not come from users or list total');
  h.cleanup();
});

test('summary failure leaves the list usable and its own retry recovers', async () => {
  const h = await loadHook();
  assert.equal(h.summaryReads.length, 1);
  h.summaryReads[0].reject(new Error('summary offline')); await flush();
  assert.equal(h.render().blocked, false);
  assert.equal(h.render().busy, false);
  assert.equal(h.render().data.cycle.total_used, 17);
  assert.equal(h.render().summary.data, undefined);
  assert(h.render().summary.error);
  const listReads = h.overviewReads.length;
  h.render().retrySummary();
  h.summaryReads[1].resolve(summary(456)); await flush();
  assert.equal(h.render().summary.data.stats.cycle_bytes, 456);
  assert.equal(h.overviewReads.length, listReads, 'summary retry must leave list and drafts alone');
  h.cleanup();
});

test('a failed automatic summary refresh removes old totals and keeps polling', async () => {
  const h = await loadHook();
  assert.equal(h.summaryReads.length, 1);
  h.summaryReads[0].resolve(summary(123)); await flush();
  h.tick(30_000); await flush();
  assert.equal(h.summaryReads.length, 2);
  h.summaryReads[1].reject(new Error('refresh failed')); await flush();
  assert.equal(h.render().summary.data, undefined, 'failed data must not appear current');
  assert.equal(h.render().blocked, false);
  h.tick(30_000); await flush();
  assert.equal(h.summaryReads.length, 3);
  h.summaryReads[2].resolve(summary(789)); await flush();
  assert.equal(h.render().summary.data.stats.cycle_bytes, 789);
  h.cleanup();
});

test('manual refresh supersedes an earlier summary response', async () => {
  const h = await loadHook();
  assert.equal(h.summaryReads.length, 1);
  h.render().refresh();
  assert.equal(h.summaryReads[0].signal.aborted, true);
  assert.equal(h.summaryReads.length, 2);
  h.summaryReads[1].resolve(summary(20)); await flush();
  h.summaryReads[0].resolve(summary(900)); await flush();
  assert.equal(h.render().summary.data.stats.cycle_bytes, 20);
  h.cleanup();
});

test('clear-all invalidates summary before the write and rejects its late response', async () => {
  const h = await loadHook({ pendingWrite: true });
  assert.equal(h.summaryReads.length, 1);
  h.summaryReads[0].resolve(summary(123)); await flush();
  h.render().retrySummary();
  const oldRead = h.summaryReads[1];
  const mutation = h.render().mutate('reset-usage-all', {});
  assert.equal(oldRead.signal.aborted, true);
  assert.equal(h.render().summary.data, undefined);
  oldRead.resolve(summary(123)); await flush();
  assert.equal(h.render().summary.data, undefined, 'pre-write total cannot return during mutation');
  assert.equal(h.summaryReads.length, 2, 'no summary read during an unanswered write');
  h.writes[0].resolve({ kind: 'success', code: 'reset usage all' });
  await mutation;
  assert.equal(h.summaryReads.length, 3);
  h.summaryReads[2].resolve(summary(0)); await flush();
  assert.equal(h.render().summary.data.stats.cycle_bytes, 0);
  h.cleanup();
});

test('summary 401 clears account data and cannot be undone by an older list response', async () => {
  const h = await loadHook({ pendingOverview: true });
  assert.equal(h.summaryReads.length, 1);
  h.summaryReads[0].reject(h.authError()); await flush();
  assert.equal(h.render().auth, true);
  assert.equal(h.render().blocked, true);
  assert.equal(h.render().summary.data, undefined);
  assert.equal(h.overviewReads[0].signal.aborted, true);
  h.overviewReads[0].resolve(overview); await flush();
  assert.equal(h.render().data, undefined);
  h.tick(30_000); await flush();
  assert.equal(h.summaryReads.length, 1, 'expired session stops automatic reads');
  h.cleanup();
});

test('session expiry during a confirmed mutation read releases the busy gate', async () => {
  const h = await loadHook({ pendingOverview: true });
  h.overviewReads[0].resolve(overview); await flush();
  const mutation = h.render().mutate('reset-usage-all', {});
  await flush();
  assert.equal(h.render().busy, true);
  h.overviewReads[1].reject(h.authError());
  await mutation;
  assert.equal(h.render().auth, true);
  assert.equal(h.render().busy, false);
  assert.equal(h.render().data, undefined);
  h.render().refresh();
  assert.equal(h.overviewReads.length, 3, 'authentication failure must not leave a permanent pending gate');
  h.cleanup();
});

test('a timed-out summary leaves no stale totals or stuck loading state', async () => {
  const h = await loadHook();
  h.summaryReads[0].resolve(summary(123)); await flush();
  h.render().retrySummary();
  h.tick(10_000);
  assert.equal(h.summaryReads[1].signal.aborted, true);
  h.summaryReads[1].reject(new DOMException('The operation was aborted', 'AbortError')); await flush();
  assert.equal(h.render().summary.loading, false);
  assert.equal(h.render().summary.data, undefined);
  assert(h.render().summary.error);
  h.cleanup();
});

test('visibility and BFCache boundaries abort summaries and resume with a fresh read', async () => {
  const h = await loadHook();
  assert.equal(h.summaryReads.length, 1);
  h.document.hidden = true; h.dispatch('document:visibilitychange');
  assert.equal(h.summaryReads[0].signal.aborted, true);
  h.tick(30_000); await flush();
  assert.equal(h.summaryReads.length, 1);
  h.document.hidden = false; h.dispatch('document:visibilitychange');
  assert.equal(h.summaryReads.length, 2);
  h.dispatch('window:pagehide', { persisted: true });
  assert.equal(h.summaryReads[1].signal.aborted, true);
  h.summaryReads[1].resolve(summary(999)); await flush();
  assert.equal(h.render().summary.data, undefined);
  h.dispatch('window:pageshow', { persisted: true });
  assert.equal(h.summaryReads.length, 3);
  h.summaryReads[2].resolve(summary(30)); await flush();
  assert.equal(h.render().summary.data.stats.cycle_bytes, 30);
  h.cleanup();
});
