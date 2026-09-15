const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

async function loadOverviewHook({ writeConfirmed = true } = {}) {
  const listeners = new Map();
  const stateValues = [];
  const effects = [];
  const timers = new Map();
  const canonical = deferred();
  const pendingWrite = deferred();
  let timerId = 0;
  let overviewReads = 0;

  const react = {
    useCallback: callback => callback,
    useEffect: effect => { effects.push(effect); },
    useRef: value => ({ current: value }),
    useState: initial => {
      const index = stateValues.length;
      stateValues.push(typeof initial === 'function' ? initial() : initial);
      return [stateValues[index], value => {
        stateValues[index] = typeof value === 'function' ? value(stateValues[index]) : value;
      }];
    },
  };
  const fakeSetTimeout = callback => {
    const id = ++timerId;
    timers.set(id, callback);
    return id;
  };
  const fakeClearTimeout = id => timers.delete(id);
  const requests = {
    readJson: async url => {
      if (url !== '/api/v1/admin/overview-page') return false;
      overviewReads++;
      if (overviewReads === 1) return { users: [], cycle: {} };
      return canonical.promise;
    },
    write: async () => writeConfirmed
      ? { kind: 'success', code: 'reset-usage demo_alex' }
      : pendingWrite.promise,
  };
  const context = vm.createContext({
    AbortController,
    URLSearchParams,
    location: { search: '' },
    document: {
      hidden: false,
      addEventListener: (name, handler) => listeners.set(`document:${name}`, handler),
      removeEventListener: name => listeners.delete(`document:${name}`),
    },
    window: {
      addEventListener: (name, handler) => listeners.set(`window:${name}`, handler),
      removeEventListener: name => listeners.delete(`window:${name}`),
      setTimeout: fakeSetTimeout,
      clearTimeout: fakeClearTimeout,
    },
    setTimeout: fakeSetTimeout,
    clearTimeout: fakeClearTimeout,
    __react: react,
    __requests: requests,
  });

  const sourcePath = path.resolve(__dirname, '../frontend/src/features/network-admin/overview/useOverview.ts');
  const { transformWithOxc } = await import('vite');
  const { code: source } = await transformWithOxc(fs.readFileSync(sourcePath, 'utf8'), sourcePath, {
    lang: 'ts',
  });
  const module = new vm.SourceTextModule(source, { context, identifier: sourcePath });
  const modules = new Map([
    ['react', `
      export const useCallback = globalThis.__react.useCallback;
      export const useEffect = globalThis.__react.useEffect;
      export const useRef = globalThis.__react.useRef;
      export const useState = globalThis.__react.useState;
    `],
    ['../../../shared/readResource', 'export class ResourceError extends Error {}'],
    ['./presentation', 'export const feedback = value => value;'],
    ['./requests', `
      export const parseOverview = value => value;
      export const parsePoll = value => value;
      export const parseReload = value => value;
      export const readJson = globalThis.__requests.readJson;
      export const write = globalThis.__requests.write;
    `],
  ]);
  await module.link(async specifier => {
    const stub = modules.get(specifier);
    if (!stub) throw new Error(`unexpected dependency: ${specifier}`);
    return new vm.SourceTextModule(stub, { context, identifier: specifier });
  });
  await module.evaluate();

  const hook = module.namespace.useOverview();
  assert.equal(effects.length, 1);
  effects[0]();
  await new Promise(resolve => setImmediate(resolve));
  return { hook, listeners, stateValues, overviewReads: () => overviewReads };
}

test('pagehide preserves confirmed mutation feedback while canonical read is pending', async () => {
  const harness = await loadOverviewHook();
  const mutation = harness.hook.mutate('reset-usage', {});
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(harness.overviewReads(), 2, 'confirmed write starts its canonical read');
  assert.equal(harness.stateValues[5], 'reset-usage demo_alex');

  harness.listeners.get('window:pagehide')({ persisted: true });

  assert.equal(harness.stateValues[5], 'reset-usage demo_alex');
  void mutation;
});

test('pagehide marks an unanswered mutation as unknown', async () => {
  const harness = await loadOverviewHook({ writeConfirmed: false });
  const mutation = harness.hook.mutate('reset-usage', {});
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(harness.overviewReads(), 1, 'unanswered write has not started a canonical read');
  assert.equal(harness.stateValues[5], '正在提交…');

  harness.listeners.get('window:pagehide')({ persisted: true });

  assert.equal(harness.stateValues[5], '操作结果尚未确认，请刷新核对后再操作');
  void mutation;
});
