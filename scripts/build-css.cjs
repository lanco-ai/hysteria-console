// Ordered source assembly keeps the existing public URL and deployment contract.
const fs = require('node:fs');
const path = require('node:path');

const args = process.argv.slice(2);
const rootIndex = args.indexOf('--root');
const root = rootIndex < 0 ? path.resolve(__dirname, '..') : path.resolve(args[rootIndex + 1]);
const source = path.join(root, 'hysteria/styles');
const manifest = JSON.parse(fs.readFileSync(path.join(source, 'manifest.json'), 'utf8'));
if (!Array.isArray(manifest) || !manifest.length || new Set(manifest).size !== manifest.length ||
    manifest.some(name => typeof name !== 'string' || !/^[a-z0-9-]+\.css$/.test(name))) {
  throw new Error('Invalid CSS source manifest');
}
const assembled = manifest.map(name => fs.readFileSync(path.join(source, name), 'utf8')).join('');
const output = path.join(root, 'hysteria/admin.css');
if (args.includes('--check')) {
  if (!fs.existsSync(output) || fs.readFileSync(output, 'utf8') !== assembled) {
    console.error('CSS bundle is stale. Run npm run build:css.');
    process.exitCode = 1;
  }
} else {
  fs.writeFileSync(output, assembled);
}
