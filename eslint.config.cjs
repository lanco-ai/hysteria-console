const globals = require('globals');

module.exports = [
  {
    files: ['hysteria/**/*.js', 'tests/*.cjs', 'scripts/*.cjs'],
    rules: {
      'no-undef': 'error',
      'no-unreachable': 'error',
      'no-dupe-args': 'error',
      'no-dupe-keys': 'error',
      'no-constant-condition': 'error',
      'valid-typeof': 'error',
    },
  },
  { files: ['hysteria/**/*.js'], languageOptions: { sourceType: 'script', globals: globals.browser } },
  { files: ['tests/*.cjs', 'scripts/*.cjs'], languageOptions: { globals: globals.node } },
  // Playwright evaluate/wait callbacks execute in the browser, not Node.
  { files: ['tests/workspace_visual.cjs', 'tests/*_browser.cjs'], languageOptions: { globals: globals.browser } },
];
