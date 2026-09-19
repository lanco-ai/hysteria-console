const assert = require('node:assert/strict');
const fs = require('node:fs');

const page = fs.readFileSync('frontend/src/features/video/VideoPage.tsx', 'utf8');
const storyboard = fs.readFileSync('frontend/src/features/video/VideoStoryboard.tsx', 'utf8');
const types = fs.readFileSync('frontend/src/features/video/videoTypes.ts', 'utf8');
const styles = fs.readFileSync('frontend/src/styles/sections/22-video-storyboard.css', 'utf8');

assert.match(page, /VideoStoryboard/);
assert.match(storyboard, /video-storyboard/);
assert.match(storyboard, /添加分镜/);
assert.match(storyboard, /运行当前镜头/);
assert.match(page, /storyboard/);
assert.match(page, /run\.workflow\?\.nodes/);
assert.match(page, /refreshCapabilities/);
assert.match(fs.readFileSync('frontend/src/features/video/VideoSettingsDrawer.tsx', 'utf8'), /onSaved/);
assert.match(types, /VideoStoryboardShot/);
assert.match(types, /storyboard/);
assert.match(styles, /video-storyboard-row/);
console.log('video storyboard UI contract passed');
