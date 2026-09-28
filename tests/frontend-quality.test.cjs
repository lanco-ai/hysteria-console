const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { ESLint } = require('eslint');
const stylelint = require('stylelint');

test('frontend release gate builds React assets before all React browser suites', () => {
  const scripts = JSON.parse(fs.readFileSync('package.json', 'utf8')).scripts;
  assert.match(scripts['test:services-browser'], /tests\/run_services_browser\.py/);
  assert.match(scripts['check:react'], /check:react-core.*test:react-browser.*test:services-browser/);
  assert.match(scripts['check:frontend'], /check:react/);
  assert.doesNotMatch(scripts['check:frontend'], /&&\s*npm run test:browser/);
});

test('JavaScript gate rejects undefined references but accepts browser globals', async () => {
  const lint = new ESLint();
  const [bad] = await lint.lintText('unknownRefresh();', { filePath: 'hysteria/gate-probe.js' });
  assert(bad.messages.some(message => message.ruleId === 'no-undef'));
  const [good] = await lint.lintText('document.title = "preview";', { filePath: 'hysteria/gate-probe.js' });
  assert.equal(good.errorCount, 0);
});

test('CSS gate rejects invalid declarations but accepts semantic custom properties', async () => {
  const bad = await stylelint.lint({ code: '.probe { colro: red; }', codeFilename: 'hysteria/gate-probe.css' });
  assert.equal(bad.errored, true);
  const good = await stylelint.lint({ code: '.probe { color: var(--text-primary); }', codeFilename: 'hysteria/gate-probe.css' });
  assert.equal(good.errored, false);
});
