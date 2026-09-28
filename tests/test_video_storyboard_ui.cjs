const assert = require('node:assert/strict');
const fs = require('node:fs');

const root = 'frontend/src/features/video/';
const page = fs.readFileSync(`${root}VideoPage.tsx`, 'utf8');
const canvas = fs.readFileSync(`${root}VideoCanvas.tsx`, 'utf8');
const node = fs.readFileSync(`${root}VideoNode.tsx`, 'utf8');
const types = fs.readFileSync(`${root}videoTypes.ts`, 'utf8');

assert.equal(fs.existsSync(`${root}VideoStoryboard.tsx`), false);
assert.equal(fs.existsSync(`${root}VideoCreativeAssistant.tsx`), false);
assert.equal(fs.existsSync(`${root}VideoTemplates.ts`), false);
assert.match(page, /target_node_id|createRun\(id, node\.id\)/);
assert.match(page, /legacyStoryboard/);
assert.match(types, /storyboard\?: VideoStoryboard/);
assert.match(canvas, /onPaneContextMenu/);
assert.match(canvas, /screenToFlowPosition/);
assert.match(canvas, /video-add-node-touch/);
assert.match(node, /candidate_asset_refs/);
assert.match(node, /selected_first_asset_ref/);
assert.match(node, /selected_last_asset_ref/);
console.log('video canvas UI contract passed');
