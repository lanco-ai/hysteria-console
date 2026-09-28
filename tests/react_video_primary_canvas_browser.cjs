const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { expect } = require('@playwright/test');

const baseUrl = process.env.PREVIEW_BASE_URL;
const candidates = ['candidatea', 'candidateb', 'candidatec', 'candidated'];
const workflow = {
  id: 'saved-custom-workflow', title: 'Saved custom workflow',
  storyboard: { title: 'Legacy storyboard', shots: [{ id: 'legacy-shot' }] },
  nodes: [
    { id: 'saved-prompt', type: 'prompt', position: { x: 60, y: 160 }, data: { text: 'A forest in morning light', label: 'Restored prompt node' } },
    { id: 'saved-image', type: 'text_to_image', position: { x: 390, y: 160 }, data: { model: 'grok-imagine-image', n: 4, aspect_ratio: '9:16' } },
    { id: 'saved-video', type: 'first_last_frame_video', position: { x: 790, y: 160 }, data: { model: 'grok-imagine-video-1.5', prompt: 'Camera moves slowly', duration: 5 } },
    { id: 'saved-preview', type: 'preview', position: { x: 1100, y: 160 }, data: {} },
  ],
  edges: [
    { id: 'prompt-image', source: 'saved-prompt', sourceHandle: 'text', target: 'saved-image', targetHandle: 'prompt' },
    { id: 'image-first', source: 'saved-image', sourceHandle: 'first_frame', target: 'saved-video', targetHandle: 'first_frame' },
    { id: 'image-last', source: 'saved-image', sourceHandle: 'last_frame', target: 'saved-video', targetHandle: 'last_frame' },
    { id: 'video-preview', source: 'saved-video', sourceHandle: 'video', target: 'saved-preview', targetHandle: 'media' },
  ],
};
const alternateWorkflow = {
  ...workflow,
  id: 'alternate-same-node-workflow',
  title: 'Alternate saved work',
  nodes: workflow.nodes.map(node => node.id === 'saved-image'
    ? { ...node, data: { ...node.data, candidate_asset_refs: ['asset://candidatec', 'asset://candidated'], selected_first_asset_ref: 'asset://candidatec', selected_last_asset_ref: 'asset://candidated' } }
    : { ...node, data: { ...node.data } }),
  edges: workflow.edges.map(edge => ({ ...edge })),
};

async function verifyDurableRecovery(browser, viewport) {
  console.log(`durable recovery ${viewport.width}`);
  const context = await browser.newContext({ viewport });
  await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
  const page = await context.newPage();
  let stored = structuredClone(workflow);
  const data = () => stored.nodes.find(node => node.id === 'saved-image').data;
  stored.nodes.push({ id: 'other-running-image', type: 'text_to_image', position: { x: 390, y: 600 },
    data: { prompt: 'other image', model: 'grok-imagine-image', n: 1, candidate_run_id: 'other-running-run', candidate_run_state: 'running', candidate_run_version: 1 } });
  let state = 'queued';
  let submissions = 0;
  Object.assign(data(), { candidate_run_id: 'recovered-run', candidate_run_state: state, candidate_run_version: 1 });
  let historyLoads = 0;
  let holdNextRead = false;
  let releaseRead = null;
  await page.route('**/api/video/workflows', async route => {
    if (route.request().method() === 'POST') {
      stored = { ...route.request().postDataJSON(), id: workflow.id };
      return route.fulfill({ json: stored });
    }
    return route.fulfill({ json: { workflows: [stored] } });
  });
  await page.route('**/api/video/workflows/*', async route => {
    const snapshot = structuredClone(stored);
    if (holdNextRead) {
      holdNextRead = false;
      await new Promise(resolve => { releaseRead = resolve; });
    }
    return route.fulfill({ json: snapshot });
  });
  await page.route('**/api/video/capabilities', route => route.fulfill({ json: {
    image_models: ['grok-imagine-image'], video_models: [], image_batch_models: ['grok-imagine-image'],
    first_last_frame_models: [], first_last_frame: { supported: false }, video_composition: { supported: false },
  } }));
  await page.route('**/api/video/runs', route => {
    if (route.request().method() === 'POST') {
      submissions += 1;
      return route.fulfill({ json: { id: 'resubmitted-run', workflow_id: workflow.id, target_node_id: 'saved-image', state: 'queued', node_status: {} } });
    }
    historyLoads += 1;
    return route.fulfill({ json: { runs: [] } });
  });
  await page.route('**/api/video/runs/*', route => route.fulfill({ json: {
    id: route.request().url().split('/').pop(), workflow_id: workflow.id, target_node_id: route.request().url().endsWith('/other-running-run') ? 'other-running-image' : 'saved-image', state,
    node_status: { 'saved-image': { state } }, assets: {},
  } }));
  await page.route('**/api/video/assets/*/content', route => route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/nYUAAAAASUVORK5CYII=', 'base64') }));
  await page.goto(`${baseUrl}/__react/admin/video`);
  await page.reload();
  await expect(page.locator('.video-run-panel')).toContainText('queued');
  state = 'running'; data().candidate_run_state = state;
  await page.reload();
  await expect(page.locator('.video-run-panel')).toContainText('running');
  // Completion is persisted by the server without any browser run assets/autosave.
  state = 'succeeded';
  Object.assign(data(), { candidate_run_state: state, candidate_batch_id: 'recovered-run', candidate_batch_version: 1,
    candidate_asset_refs: candidates.map(id => `asset://${id}`), selected_first_asset_ref: '', selected_last_asset_ref: '' });
  await expect(page.getByRole('button', { name: '设为首帧', exact: true })).toHaveCount(4);
  await page.locator('.react-flow__node[data-id="saved-image"]').getByRole('button', { name: '生成候选图' }).click();
  await expect.poll(() => submissions).toBe(1);
  await page.reload();
  await expect(page.getByRole('button', { name: '设为首帧', exact: true })).toHaveCount(4);
  await page.getByRole('button', { name: '设为首帧', exact: true }).nth(0).click();
  await page.getByRole('button', { name: '设为尾帧', exact: true }).nth(1).click();
  await expect.poll(() => data().selected_first_asset_ref).toBe('asset://candidatea');
  await expect.poll(() => data().selected_last_asset_ref).toBe('asset://candidateb');
  await page.reload();
  await expect(page.getByRole('button', { name: '设为首帧', exact: true, pressed: true })).toHaveCount(1);
  await expect(page.getByRole('button', { name: '设为尾帧', exact: true, pressed: true })).toHaveCount(1);
  holdNextRead = true;
  await expect.poll(() => Boolean(releaseRead)).toBe(true);
  Object.assign(data(), { candidate_run_version: 2, candidate_run_id: 'newer-run',
    candidate_batch_id: 'newer-run', candidate_batch_version: 2,
    candidate_asset_refs: ['asset://candidatec', 'asset://candidated'],
    selected_first_asset_ref: 'asset://candidatec', selected_last_asset_ref: 'asset://candidated' });
  await expect(page.getByRole('button', { name: '设为首帧', exact: true })).toHaveCount(2);
  await page.getByRole('button', { name: '设为首帧', exact: true }).nth(1).click();
  await page.getByRole('button', { name: '设为尾帧', exact: true }).nth(0).click();
  await expect.poll(() => data().selected_first_asset_ref).toBe('asset://candidated');
  releaseRead();
  await page.waitForTimeout(300);
  await expect(page.getByRole('button', { name: '设为首帧', exact: true })).toHaveCount(2);
  await expect(page.getByRole('button', { name: '设为首帧', exact: true }).nth(1)).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('button', { name: '设为尾帧', exact: true }).nth(0)).toHaveAttribute('aria-pressed', 'true');
  await page.locator('.react-flow__node[data-id="saved-image"]').getByRole('button', { name: '生成候选图' }).click();
  await expect.poll(() => submissions).toBe(2);
  assert.equal(historyLoads, 0);
  await context.close();
}

async function verifyVideoRecovery(browser, viewport) {
  console.log(`video recovery ${viewport.width}`);
  const context = await browser.newContext({ viewport });
  await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
  let stored = structuredClone(workflow);
  const videoData = () => stored.nodes.find(node => node.id === 'saved-video').data;
  stored.nodes.find(node => node.id === 'saved-preview').data.video_url = 'https://media.test/legacy.mp4';
  Object.assign(videoData(), { video_node_token: 'video-incarnation', video_run_id: 'closed-tab-run', video_run_version: 1, video_run_state: 'running' });
  let submissions = 0;
  let saves = 0;
  await context.route('**/api/video/workflows', route => {
    if (route.request().method() === 'POST') {
      saves += 1;
      const result = route.request().postDataJSON();
      Object.assign(result.nodes.find(node => node.id === 'saved-video').data, videoData());
      stored = result;
      return route.fulfill({ json: stored });
    }
    return route.fulfill({ json: { workflows: [stored] } });
  });
  let readsBlocked = false;
  await context.route('**/api/video/workflows/*', route => readsBlocked ? route.abort() : route.fulfill({ json: stored }));
  await context.route('**/api/video/capabilities', route => route.fulfill({ json: {
    image_models: [], video_models: ['grok-imagine-video-1.5'], image_batch_models: [],
    first_last_frame_models: ['grok-imagine-video-1.5'], first_last_frame: { supported: true }, video_composition: { supported: false },
  } }));
  await context.route('**/api/video/runs', route => { submissions += 1; return route.abort(); });
  // Run polling is deliberately unavailable: workflow recovery must suffice.
  await context.route('**/api/video/runs/*', route => route.abort());
  await context.route('https://media.test/**', route => route.fulfill({ status: 200, contentType: 'video/mp4', body: '' }));
  let page = await context.newPage();
  await page.goto(`${baseUrl}/__react/admin/video`);
  await expect(page.locator('.video-run-panel')).toBeVisible();
  await expect(page.locator('.react-flow__node[data-id="saved-preview"] video')).toHaveAttribute('src', 'https://media.test/legacy.mp4');
  await page.close();
  Object.assign(videoData(), { video_run_state: 'succeeded', video_url: 'https://media.test/closed-tab.mp4' });
  readsBlocked = true;
  page = await context.newPage();
  await page.goto(`${baseUrl}/__react/admin/video`);
  const preview = page.locator('.react-flow__node[data-id="saved-preview"] video');
  await expect(preview).toHaveAttribute('src', 'https://media.test/closed-tab.mp4');
  assert.equal(saves, 0, 'initial recovery must precede autosave');
  assert.equal(submissions, 0);
  await page.getByRole('textbox', { name: '作品名称' }).fill('Concurrent video title');
  await expect.poll(() => stored.title).toBe('Concurrent video title');
  await page.reload();
  await expect(preview).toHaveAttribute('src', 'https://media.test/closed-tab.mp4');
  // An open tab also learns server completion through the scoped workflow read.
  readsBlocked = false;
  Object.assign(videoData(), { video_run_id: 'newer-video-run', video_run_version: 2, video_url: 'https://media.test/newer.mp4' });
  await expect(preview).toHaveAttribute('src', 'https://media.test/newer.mp4');
  assert.equal(submissions, 0);
  assert.equal(stored.title, 'Concurrent video title');
  await context.close();
}

async function verifyLegacyPreviewReconnect(browser, viewport) {
  console.log(`legacy preview reconnect ${viewport.width}`);
  const context = await browser.newContext({ viewport });
  await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
  let stored = { id: 'legacy-preview', title: 'Legacy preview', nodes: [
    { id: 'original', type: 'image_to_video', position: { x: 0, y: 0 }, data: {} },
    { id: 'empty', type: 'image_to_video', position: { x: 0, y: 240 }, data: {} },
    { id: 'preview', type: 'preview', position: { x: 380, y: 100 }, data: { video_url: 'https://media.test/legacy.mp4' } },
  ], edges: [{ id: 'original-edge', source: 'original', sourceHandle: 'video', target: 'preview', targetHandle: 'media' }] };
  await context.route('**/api/video/workflows', route => {
    if (route.request().method() === 'POST') {
      stored = { ...route.request().postDataJSON(), id: stored.id };
      return route.fulfill({ json: stored });
    }
    return route.fulfill({ json: { workflows: [stored] } });
  });
  await context.route('**/api/video/workflows/*', route => route.fulfill({ json: stored }));
  await context.route('**/api/video/capabilities', route => route.fulfill({ json: { image_models: [], video_models: [], first_last_frame: { supported: false } } }));
  await context.route('**/api/video/runs**', route => route.abort());
  await context.route('https://media.test/**', route => route.fulfill({ status: 200, contentType: 'video/mp4', body: '' }));
  const page = await context.newPage();
  await page.goto(`${baseUrl}/__react/admin/video`);
  const preview = page.locator('.react-flow__node[data-id="preview"] video');
  await expect(preview).toHaveAttribute('src', 'https://media.test/legacy.mp4');
  await page.getByRole('textbox', { name: '作品名称' }).fill('Legacy title preserved');
  await expect.poll(() => stored.title).toBe('Legacy title preserved');
  await page.reload();
  await expect(preview).toHaveAttribute('src', 'https://media.test/legacy.mp4');
  // Use the canvas edge deletion and handle gestures, including mobile layout.
  const edge = page.locator('.react-flow__edge[data-id="original-edge"]');
  await edge.focus();
  await page.keyboard.press('Enter');
  await page.keyboard.press('Backspace');
  await expect(edge).toHaveCount(0);
  const source = await page.locator('.react-flow__node[data-id="empty"] .react-flow__handle[data-handleid="video"]').boundingBox();
  const target = await page.locator('.react-flow__node[data-id="preview"] .react-flow__handle[data-handleid="media"]').boundingBox();
  assert.ok(source && target);
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
  await page.mouse.down();
  await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 12 });
  await page.mouse.up();
  await expect.poll(() => stored.edges.some(item => item.source === 'empty' && item.target === 'preview')).toBe(true);
  await expect(preview).toHaveCount(0);
  await page.reload();
  await expect(page.locator('.react-flow__node[data-id="preview"]')).toBeVisible();
  await expect(preview).toHaveCount(0);
  assert.equal(stored.nodes.find(node => node.id === 'preview').data.video_url, 'https://media.test/legacy.mp4');
  await context.close();
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const viewport of [{ width: 1440, height: 960 }, { width: 390, height: 844 }]) {
      await verifyLegacyPreviewReconnect(browser, viewport);
      await verifyVideoRecovery(browser, viewport);
      await verifyDurableRecovery(browser, viewport);
      const context = await browser.newContext({ viewport });
      await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
      const page = await context.newPage();
      let savedPayload = null;
      const savePayloads = [];
      let releaseDelayedSave = null;
      let delayedSaveStarted = false;
      let delayedSaveResultId = null;
      let delayNextImageRun = false;
      let delayedRunStarted = false;
      let releaseDelayedRun = null;
      let storedWorkflow = structuredClone(workflow);
      const runTargets = [];
      let historyLoads = 0;
      await page.route('**/api/video/workflows', async route => {
        if (route.request().method() === 'POST') {
          const payload = route.request().postDataJSON();
          savePayloads.push(payload);
          const imageData = payload.nodes.find(node => node.id === 'saved-image')?.data;
          if (viewport.width === 1440 && imageData?.selected_first_asset_ref && !imageData?.selected_last_asset_ref) {
            await new Promise(resolve => setTimeout(resolve, 1800));
          }
          if (viewport.width === 1440 && payload.title === 'Pending old save') {
            delayedSaveStarted = true;
            await new Promise(resolve => { releaseDelayedSave = resolve; });
          }
          savedPayload = payload;
          const response = { ...structuredClone(payload), id: payload.id || `new-work-${savePayloads.length}` };
          const previousImage = storedWorkflow.nodes.find(node => node.id === 'saved-image')?.data;
          const nextImage = response.nodes.find(node => node.id === 'saved-image')?.data;
          if (previousImage?.candidate_batch_id && nextImage?.candidate_batch_id === previousImage.candidate_batch_id) {
            const revision = Number(previousImage.candidate_selection_version || 0);
            const selectionKeys = ['selected_first_asset_ref', 'selected_last_asset_ref'];
            if (Number(nextImage.candidate_selection_version || 0) !== revision) {
              selectionKeys.forEach(key => { nextImage[key] = previousImage[key]; });
              nextImage.candidate_selection_version = revision;
            } else nextImage.candidate_selection_version = revision + Number(selectionKeys.some(key => nextImage[key] !== previousImage[key]));
          }
          storedWorkflow = response;
          if (payload.title === 'Pending old save') delayedSaveResultId = response.id;
          await route.fulfill({ json: response });
        } else await route.fulfill({ json: { workflows: [storedWorkflow, alternateWorkflow] } });
      });
      await page.route('**/api/video/workflows/*', route => route.fulfill({ json: storedWorkflow }));
      await page.route('**/api/video/capabilities', route => route.fulfill({ json: {
        image_models: ['grok-imagine-image'], video_models: ['grok-imagine-video-1.5'],
        image_batch_models: ['grok-imagine-image'], first_last_frame_models: ['grok-imagine-video-1.5'],
        first_last_frame: { supported: true }, video_composition: { supported: false },
      } }));
      await page.route('**/api/video/runs', async route => {
        if (route.request().method() !== 'POST') { historyLoads += 1; return route.fulfill({ json: { runs: [] } }); }
        const target = route.request().postDataJSON().target_node_id;
        runTargets.push(target);
        const delayed = delayNextImageRun && target === 'saved-image';
        if (delayed) {
          delayNextImageRun = false;
          delayedRunStarted = true;
          await new Promise(resolve => { releaseDelayedRun = resolve; });
        }
        return route.fulfill({ json: { id: delayed ? 'late-image-run' : target === 'saved-image' ? 'image-run' : 'video-run', state: 'queued', workflow_id: delayed ? alternateWorkflow.id : workflow.id, node_status: {} } });
      });
      await page.route('**/api/video/runs/*', route => {
        const runUrl = route.request().url();
        const isImage = runUrl.endsWith('/image-run') || runUrl.endsWith('/late-image-run');
        if (runUrl.endsWith('/image-run')) {
          const image = storedWorkflow.nodes.find(node => node.id === 'saved-image');
          if (image && !image.data.candidate_batch_id) Object.assign(image.data, {
            candidate_batch_id: 'image-run', candidate_batch_version: 1,
            candidate_run_id: 'image-run', candidate_run_version: 1, candidate_run_state: 'succeeded',
            candidate_asset_refs: candidates.map(id => `asset://${id}`), selected_first_asset_ref: '', selected_last_asset_ref: '',
          });
        }
        if (runUrl.endsWith('/video-run')) {
          const video = storedWorkflow.nodes.find(node => node.id === 'saved-video');
          if (video) Object.assign(video.data, { video_run_id: 'video-run', video_run_version: 1,
            video_run_state: 'succeeded', video_url: '/api/video/runs/video-run/assets/saved-video/content' });
        }
        return route.fulfill({ json: {
          id: runUrl.endsWith('/late-image-run') ? 'late-image-run' : isImage ? 'image-run' : 'video-run', state: 'succeeded', workflow_id: runUrl.endsWith('/late-image-run') ? alternateWorkflow.id : workflow.id,
          target_node_id: isImage ? 'saved-image' : 'saved-video',
          node_status: { [isImage ? 'saved-image' : 'saved-video']: { state: 'succeeded' } },
          assets: isImage ? { 'saved-image': candidates.map(id => `/api/video/assets/${id}/content`) } : {
            'saved-image': candidates.map(id => `/api/video/assets/${id}/content`),
            'saved-video': '/api/video/runs/video-run/assets/saved-video/content',
          },
          workflow: { nodes: workflow.nodes, edges: workflow.edges },
        } });
      });
      await page.route('**/api/video/assets/*/content', route => route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/nYUAAAAASUVORK5CYII=', 'base64') }));
      await page.route('**/api/video/runs/*/assets/*/content', route => route.fulfill({ contentType: 'video/mp4', body: Buffer.alloc(0) }));
      await page.goto(`${baseUrl}/__react/admin/video`);
      await expect(page.locator('.video-canvas-editor-primary')).toBeVisible();
      await expect(page.getByText('Restored prompt node', { exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: '生成候选图' })).toBeVisible();
      for (const label of ['分镜编辑', '任务记录', 'AI 创作助手', '服务配置']) {
        await expect(page.getByText(label, { exact: true })).toHaveCount(0);
      }
      assert.equal(historyLoads, 0, 'opening the page must not load historical runs');
      if (viewport.width === 1440) {
        await page.locator('.react-flow__node[data-id="saved-prompt"]').click();
        await page.locator('.react-flow__pane').click({ button: 'right', position: { x: 100, y: 440 } });
        await expect(page.getByRole('menuitem', { name: '添加提示词' })).toBeFocused();
        await page.keyboard.press('Escape');
        await expect(page.getByRole('menu', { name: '添加节点' })).toHaveCount(0);
        await page.locator('.react-flow__pane').click({ button: 'right', position: { x: 100, y: 440 } });
        await page.getByRole('menuitem', { name: '文生图' }).click();
        await expect(page.locator('.react-flow__node')).toHaveCount(5);
        await expect(page.locator('.react-flow__edge')).toHaveCount(5);
        await page.locator('.react-flow__node').last().click({ button: 'right' });
        await page.getByRole('menuitem', { name: '复制节点' }).click();
        await expect(page.locator('.react-flow__node')).toHaveCount(6);
        await page.locator('.react-flow__node').last().click({ button: 'right' });
        await page.getByRole('menuitem', { name: '删除节点' }).click();
        await expect(page.locator('.react-flow__node')).toHaveCount(5);
      } else {
        await page.getByRole('button', { name: /添加节点/ }).click();
        await page.getByRole('menuitem', { name: '文生图' }).click();
        await expect(page.locator('.react-flow__node')).toHaveCount(5);
        await page.locator('.react-flow__node[data-id="saved-image"]').click();
        await expect(page.getByRole('complementary', { name: '节点属性' })).toBeVisible();
        await page.getByRole('button', { name: '关闭属性' }).click();
        await expect(page.getByRole('complementary', { name: '节点属性' })).toBeHidden();
      }
      await page.locator('.react-flow__node[data-id="saved-image"]').getByRole('button', { name: '生成候选图' }).click();
      await expect(page.locator('.react-flow__node[data-id="saved-image"] img')).toHaveCount(4);
      assert.deepEqual(runTargets, ['saved-image'], 'candidate click should submit once');
      const gallery = page.locator('.react-flow__node[data-id="saved-image"]');
      await gallery.getByRole('button', { name: '设为首帧' }).nth(0).click();
      if (viewport.width === 1440) await page.waitForTimeout(1200);
      await gallery.getByRole('button', { name: '设为尾帧' }).nth(1).click();
      await expect(gallery.locator('.video-candidate-badges').getByText('首帧', { exact: true })).toBeVisible();
      await expect(gallery.locator('.video-candidate-badges').getByText('尾帧', { exact: true })).toBeVisible();
      await expect.poll(() => savedPayload?.nodes.find(node => node.id === 'saved-image')?.data?.selected_first_asset_ref).toBe('asset://candidatea');
      await expect.poll(() => savedPayload?.nodes.find(node => node.id === 'saved-image')?.data?.selected_last_asset_ref).toBe('asset://candidateb');
      if (viewport.width === 1440) {
        await page.waitForTimeout(1900);
        assert.equal(storedWorkflow.nodes.find(node => node.id === 'saved-image').data.selected_last_asset_ref, 'asset://candidateb', 'late save must not overwrite selected tail frame');
      }
      assert.deepEqual(savedPayload.storyboard, workflow.storyboard, 'legacy storyboard metadata must round-trip');
      await page.locator('.react-flow__node[data-id="saved-video"]').getByRole('button', { name: '生成视频' }).click();
      await expect(page.locator('.react-flow__node[data-id="saved-preview"] video')).toBeVisible();
      assert.deepEqual(runTargets, ['saved-image', 'saved-video']);
      assert.equal(historyLoads, 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'page must not overflow horizontally');
      if (viewport.width === 1440) {
        await page.getByRole('textbox', { name: '作品名称' }).fill('Unsaved switch edit');
        await page.getByRole('combobox', { name: '作品' }).selectOption(alternateWorkflow.id);
        await expect(page.getByRole('textbox', { name: '作品名称' })).toHaveValue('Alternate saved work');
        const flushedEdit = savePayloads.find(payload => payload.title === 'Unsaved switch edit');
        assert.equal(flushedEdit?.id, workflow.id, 'switching canvases must save pending edits first');

        delayNextImageRun = true;
        await page.locator('.react-flow__node[data-id="saved-image"]').getByRole('button', { name: '生成候选图' }).click();
        await expect.poll(() => delayedRunStarted).toBe(true);
        await page.getByRole('combobox', { name: '作品' }).selectOption(workflow.id);
        await expect(page.getByRole('textbox', { name: '作品名称' })).not.toHaveValue('Alternate saved work');
        releaseDelayedRun();
        await expect(page.locator('.video-run-panel').getByText('succeeded', { exact: true })).toBeVisible();
        const restoredGallery = page.locator('.react-flow__node[data-id="saved-image"] .video-candidate-badges');
        await expect(restoredGallery.getByText('首帧', { exact: true })).toBeVisible();
        await expect(restoredGallery.getByText('尾帧', { exact: true })).toBeVisible();

        await page.reload();
        await expect(page.locator('.react-flow__node[data-id="saved-image"] img')).toHaveCount(4);
        await expect(page.locator('.react-flow__node[data-id="saved-image"] .video-candidate-badges').getByText('首帧', { exact: true })).toBeVisible();
        await expect(page.locator('.react-flow__node[data-id="saved-image"] .video-candidate-badges').getByText('尾帧', { exact: true })).toBeVisible();
        await page.route('**/api/video/assets/candidateb/content', route => route.fulfill({ status: 404, body: 'missing' }));
        await page.reload();
        await expect(page.locator('.react-flow__node[data-id="saved-video"]').getByRole('button', { name: '生成视频' })).toBeDisabled();
        await expect(page.locator('.react-flow__node[data-id="saved-video"]').getByText('尾帧素材不可用')).toBeVisible();
        await page.route('**/api/video/capabilities', route => route.fulfill({ json: {
          image_models: ['grok-imagine-image'], video_models: ['grok-imagine-video-1.5'],
          image_batch_models: [], first_last_frame_models: [],
          first_last_frame: { supported: false, reason: 'provider capability not verified' },
          video_composition: { supported: false },
        } }));
        await page.reload();
        await expect(page.locator('.react-flow__node[data-id="saved-image"]').getByRole('button', { name: '生成候选图' })).toBeDisabled();
        await expect(page.locator('.react-flow__node[data-id="saved-video"]').getByRole('button', { name: '生成视频' })).toBeDisabled();
        await expect(page.getByText('provider capability not verified')).toBeVisible();
        assert.deepEqual(runTargets, ['saved-image', 'saved-video', 'saved-image']);
        await page.getByRole('combobox', { name: '作品' }).selectOption('');
        await page.locator('.react-flow__node').getByRole('textbox', { name: '提示词' }).fill('A different project');
        await page.locator('.react-flow__node').first().click();
        await page.locator('.react-flow__pane').click({ button: 'right', position: { x: 100, y: 440 } });
        await page.getByRole('menuitem', { name: '文生图' }).click();
        const singleImageNode = page.locator('.react-flow__node[data-id^="text_to_image-"]');
        await expect(singleImageNode.getByRole('button', { name: '生成候选图' })).toBeEnabled();
        await expect(page.getByRole('complementary', { name: '节点属性' }).getByRole('combobox', { name: '候选数量' })).toBeDisabled();
        await expect(page.getByRole('complementary', { name: '节点属性' }).getByText('当前模型未验证支持批量生图')).toBeVisible();
        await page.getByRole('button', { name: '保存', exact: true }).click();
        await expect.poll(() => savedPayload?.nodes?.[0]?.data?.text).toBe('A different project');
        assert.equal(savedPayload.id, undefined, 'new work must not overwrite the previous workflow ID');

        await page.getByRole('textbox', { name: '作品名称' }).fill('Pending old save');
        await page.getByRole('button', { name: '保存', exact: true }).click();
        await expect.poll(() => delayedSaveStarted).toBe(true);
        const pendingPayload = savePayloads.find(payload => payload.title === 'Pending old save');
        assert.ok(pendingPayload?.id, 'the delayed save must target an existing workflow');
        await page.getByRole('combobox', { name: '作品' }).selectOption('');
        await page.waitForTimeout(1200);
        assert.equal(savePayloads.filter(payload => payload.title === 'New work after switch').length, 0,
          'the new canvas must not appear before the prior save finishes');
        releaseDelayedSave();
        await expect(page.getByRole('textbox', { name: '作品名称' })).toHaveValue('未命名作品');
        await page.locator('.react-flow__node').getByRole('textbox', { name: '提示词' }).fill('New work after switch');
        await page.getByRole('textbox', { name: '作品名称' }).fill('New work after switch');
        await expect.poll(() => savePayloads.some(payload => payload.title === 'New work after switch')).toBe(true);
        const newWorkPayload = savePayloads.find(payload => payload.title === 'New work after switch');
        assert.equal(newWorkPayload.id, undefined, 'switching to a new canvas must not reuse the old workflow ID');
        assert.notEqual(storedWorkflow.id, delayedSaveResultId, 'saving the new canvas must create a separate workflow');
        await expect(page.getByRole('textbox', { name: '作品名称' })).toHaveValue('New work after switch');

        await page.route('**/api/video/capabilities', route => route.fulfill({ json: {
          image_models: ['grok-imagine-image'], video_models: ['grok-imagine-video-1.5'],
          image_batch_models: ['grok-imagine-image'], first_last_frame_models: ['grok-imagine-video-1.5'],
          first_last_frame: { supported: true }, video_composition: { supported: false },
        } }));
        await page.reload();
        let cancelStarted = false;
        let releaseCancel = null;
        let cancelAnswered = false;
        let submittedRuns = 0;
        await page.route('**/api/video/runs', route => {
          if (route.request().method() !== 'POST') return route.fulfill({ json: { runs: [] } });
          submittedRuns += 1;
          return route.fulfill({ json: { id: submittedRuns === 1 ? 'old-cancel-run' : 'new-live-run', state: 'queued', workflow_id: workflow.id, target_node_id: 'saved-image', node_status: {} } });
        });
        await page.route('**/api/video/runs/*', route => route.fulfill({ json: {
          id: route.request().url().endsWith('/old-cancel-run') ? 'old-cancel-run' : 'new-live-run',
          state: 'running', workflow_id: workflow.id, target_node_id: 'saved-image',
          node_status: { 'saved-image': { state: 'running' } },
        } }));
        await page.route('**/api/video/runs/old-cancel-run/cancel', async route => {
          cancelStarted = true;
          await new Promise(resolve => { releaseCancel = resolve; });
          await route.fulfill({ json: { id: 'old-cancel-run', state: 'cancelled', workflow_id: workflow.id, node_status: {}, error: 'old cancellation result' } });
          cancelAnswered = true;
        });
        await page.getByRole('combobox', { name: '作品' }).selectOption(alternateWorkflow.id);
        await expect(page.getByRole('textbox', { name: '作品名称' })).toHaveValue('Alternate saved work');
        await page.locator('.react-flow__node[data-id="saved-image"]').getByRole('button', { name: '生成候选图' }).click();
        await expect(page.locator('.video-run-panel').getByText('running', { exact: true })).toBeVisible();
        await page.locator('.video-run-panel').getByRole('button', { name: '停止' }).click();
        await expect.poll(() => cancelStarted).toBe(true);
        await page.getByRole('combobox', { name: '作品' }).selectOption('');
        await expect(page.getByRole('textbox', { name: '作品名称' })).toHaveValue('未命名作品');
        await page.getByRole('combobox', { name: '作品' }).selectOption(alternateWorkflow.id);
        await expect(page.getByRole('textbox', { name: '作品名称' })).toHaveValue('Alternate saved work');
        await page.locator('.react-flow__node[data-id="saved-image"]').getByRole('button', { name: '生成候选图' }).click();
        await expect.poll(() => submittedRuns).toBe(2);
        await expect(page.locator('.video-run-panel').getByText('running', { exact: true })).toBeVisible();
        await page.evaluate(() => {
          window.videoRunPanelChanges = [];
          const panel = document.querySelector('.video-run-panel');
          new MutationObserver(() => window.videoRunPanelChanges.push(panel.textContent)).observe(panel, { childList: true, subtree: true, characterData: true });
        });
        const cancelResponse = page.waitForResponse(response => response.url().endsWith('/api/video/runs/old-cancel-run/cancel'));
        releaseCancel();
        await cancelResponse;
        await expect.poll(() => cancelAnswered).toBe(true);
        await page.waitForTimeout(500);
        await expect(page.locator('.video-run-panel').getByText('running', { exact: true })).toBeVisible();
        assert.equal((await page.evaluate(() => window.videoRunPanelChanges)).some(text => text.includes('cancelled') || text.includes('old cancellation result')), false,
          'an old cancellation response must never appear in the new run panel');
      }
      await context.close();
    }
    console.log('Canvas-first desktop and mobile workflow passed');
  } finally { await browser.close(); }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
