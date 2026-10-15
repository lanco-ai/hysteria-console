const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function helper() {
  const file = path.resolve(__dirname, '../frontend/src/shared/serviceHref.ts');
  const { transformWithOxc } = await import('vite');
  const { code } = await transformWithOxc(fs.readFileSync(file, 'utf8'), file);
  const module = new vm.SourceTextModule(code, { context: vm.createContext({ URL }) });
  await module.link(() => { throw new Error('unexpected import'); });
  await module.evaluate();
  return module.namespace.serviceHref;
}

test('HTTPS pages use their service origin while API and authentication stay local', async () => {
  const href = await helper();
  for (const source of ['https://shop.test/', 'https://shop.test:8444/admin', 'https://shop.test:8445/admin/plans', 'https://shop.test:9444/']) {
    const location = new URL(source);
    const cases = {
      '/': 'https://shop.test/',
      '/?view=sms': 'https://shop.test/?view=sms',
      '/admin/shop': 'https://shop.test/admin/shop',
      '/admin': 'https://shop.test:8444/admin',
      '/admin/usage?range=7d#chart': 'https://shop.test:8444/admin/usage?range=7d#chart',
      '/admin/plans': 'https://shop.test:8445/admin/plans',
      '/admin/chat?conversation=12': 'https://shop.test:8445/admin/chat?conversation=12',
      '/trending': 'https://shop.test:8445/trending',
      '/user/panel': 'https://shop.test:8444/user/panel',
    };
    for (const [value, expected] of Object.entries(cases)) assert.equal(new URL(href(value, location), location).href, expected);
    for (const value of ['/login?next=%2Fadmin', '/api/v1/login', '/api/plans', '/logout', '/sub/opaque']) assert.equal(href(value, location), value);
    assert.equal(href('https://external.test/admin', location), 'https://external.test/admin');
  }
});

test('development previews keep their paths and ports', async () => {
  const href = await helper();
  for (const base of ['http://127.0.0.1:18765/', 'https://preview.test:19444/']) {
    for (const route of ['/admin', '/admin/shop', '/admin/plans', '/__react/admin', '/']) assert.equal(href(route, new URL(base)), route);
  }
});
