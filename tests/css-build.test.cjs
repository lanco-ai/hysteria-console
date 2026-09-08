const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

test('CSS check detects a stale bundle without overwriting it', () => {
  const script = path.resolve('scripts/build-css.cjs');
  assert(fs.existsSync(script), 'CSS assembly needs a checked build entry');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hy2-css-test-'));
  try {
    fs.mkdirSync(path.join(root, 'hysteria/styles'), { recursive: true });
    fs.writeFileSync(path.join(root, 'hysteria/styles/manifest.json'), '["base.css","page.css"]');
    fs.writeFileSync(path.join(root, 'hysteria/styles/base.css'), '.a { color: red; }\n');
    fs.writeFileSync(path.join(root, 'hysteria/styles/page.css'), '.a { color: blue; }\n');
    fs.writeFileSync(path.join(root, 'hysteria/admin.css'), 'stale');
    const run = (...args) => spawnSync(process.execPath, [script, '--root', root, ...args], { encoding: 'utf8' });
    assert.equal(run('--check').status, 1);
    assert.equal(fs.readFileSync(path.join(root, 'hysteria/admin.css'), 'utf8'), 'stale');
    assert.equal(run().status, 0);
    assert.equal(fs.readFileSync(path.join(root, 'hysteria/admin.css'), 'utf8'), '.a { color: red; }\n.a { color: blue; }\n');
    assert.equal(run('--check').status, 0);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
