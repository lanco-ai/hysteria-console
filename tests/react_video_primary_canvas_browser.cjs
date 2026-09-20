const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { expect } = require('@playwright/test');

const baseUrl = process.env.PREVIEW_BASE_URL;

const workflow = {
  id: 'saved-custom-workflow',
  title: 'Saved custom workflow',
  nodes: [
    { id: 'saved-prompt', type: 'prompt', position: { x: 480, y: 160 }, data: { text: 'Saved prompt text', label: 'Restored prompt node' } },
    { id: 'saved-image', type: 'text_to_image', position: { x: 820, y: 160 }, data: { model: 'grok-imagine-image', prompt: 'Saved image prompt', aspect_ratio: '9:16' } },
  ],
  edges: [
    { id: 'saved-edge', source: 'saved-prompt', sourceHandle: 'text', target: 'saved-image', targetHandle: 'prompt' },
  ],
  storyboard: {
    title: 'Saved custom workflow', source_text: '', rewritten_text: '', aspect_ratio: '9:16', style_prompt: '',
    shots: [{ id: 'saved-shot', title: 'Saved shot', script: '', shot_type: '中景', character: '', scene: '', duration: 5,
      image_prompt: 'Saved image prompt', motion_prompt: 'Saved motion prompt', dialogue: '', image_model: 'grok-imagine-image',
      video_model: 'grok-imagine-video', image_state: 'idle', video_state: 'idle' }],
  },
};

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const viewport of [{ width: 1440, height: 960 }, { width: 820, height: 1180 }, { width: 390, height: 844 }]) {
      const context = await browser.newContext({ viewport });
      await context.addCookies([{ name: 'sid', value: process.env.REACT_PREVIEW_ADMIN_COOKIE, url: baseUrl }]);
      const page = await context.newPage();
      let savedPayload = null;
      let saveRequests = 0;
      let uploadRequests = 0;
      let runRequests = 0;
      await page.route('**/api/video/workflows', async route => {
        if (route.request().method() === 'POST') {
          saveRequests += 1;
          savedPayload = route.request().postDataJSON();
          await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...savedPayload, id: workflow.id }) });
          return;
        }
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ workflows: [workflow] }) });
      });
      await page.route('**/api/video/capabilities', route => route.fulfill({
        contentType: 'application/json', body: JSON.stringify({
          image_models: ['grok-imagine-image'], video_models: ['grok-imagine-video'],
          first_last_frame: { supported: false, reason: 'unverified' },
          video_composition: { supported: false, reason: 'unverified' },
        }),
      }));
      await page.route('**/api/video/runs', route => {
        if (route.request().method() === 'POST') runRequests += 1;
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ runs: [] }) });
      });
      await page.route('**/api/video/assistant/draft', async route => {
        const input = route.request().postDataJSON();
        assert.equal(input.shot_count, 1);
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify({
          model: 'gemini-preview-fast', service_name: 'Gemini', structured_output: 'gemini_native_schema', title: 'AI 草稿',
          rewritten_text: '一段整理后的故事。', style_prompt: '柔和电影光线。', aspect_ratio: '9:16',
          shots: [{ title: '发现线索', script: '主角发现线索。', shot_type: '近景', character: '主角', scene: '花园', duration: 5,
            image_prompt: '花园里主角发现闪光的线索。', motion_prompt: '镜头缓慢推进，主角拾起线索。', dialogue: '找到了！' }],
        }) });
      });
      await page.route('**/api/video/assets', async route => {
        if (route.request().method() !== 'POST') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ assets: [] }) });
        uploadRequests += 1;
        assert.match(route.request().headers()['content-type'] || '', /^image\/png/);
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify({
          id: 'uploaded-frame-1', filename: 'first-frame.png', content_type: 'image/png', size: 3,
        }) });
      });
      await page.route('**/api/video/assets/*/content', route => route.fulfill({
        contentType: 'image/png',
        body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/nYUAAAAASUVORK5CYII=', 'base64'),
      }));

      await page.goto(`${baseUrl}/__react/admin/video`);
      await expect(page.getByRole('heading', { name: 'AI 视频' })).toBeVisible();
      await expect(page.locator('.video-canvas-editor-primary')).toBeVisible();
      await expect(page.locator('.video-primary-toolbar-controls')).toBeVisible();
      await expect(page.getByRole('tab', { name: '节点工作流' })).toHaveAttribute('aria-selected', 'true');
      await expect(page.getByRole('button', { name: '运行工作流', exact: true })).toHaveClass(/btn-primary/);
      await expect(page.getByRole('button', { name: '保存', exact: true })).toHaveClass(/btn-secondary/);
      await expect(page.getByLabel('作品名称')).toHaveClass(/input/);
      await expect(page.locator('.video-primary-toolbar-meta > .video-workflow-select')).toHaveCSS('white-space', 'nowrap');
      await expect(page.getByText('Restored prompt node', { exact: true })).toBeVisible();
      if (viewport.width === 1440) {
        await page.waitForTimeout(1400);
        assert.equal(saveRequests, 0, 'initial React Flow measurement must not autosave or replace the restored graph');
        await expect(page.getByText('Restored prompt node', { exact: true })).toBeVisible();
      }
      await expect(page.locator('.video-node-palette')).toBeVisible();
      if (viewport.width === 1440 && process.env.VIDEO_PRIMARY_CANVAS_SCREENSHOT) {
        await page.screenshot({ path: process.env.VIDEO_PRIMARY_CANVAS_SCREENSHOT, fullPage: true });
      }
      const restoredEdge = await page.locator('.react-flow__edge').first().evaluate(element => ({
        visibility: getComputedStyle(element).visibility,
        path: element.querySelector('path')?.getAttribute('d') || '',
      }));
      assert.equal(restoredEdge.visibility, 'visible', 'saved edge should be visible in the React Flow graph');
      assert.match(restoredEdge.path, /^M.+C.+/, 'saved edge should have a rendered connector path');
      const edgeSvgWidth = await page.locator('.react-flow__edge-path').first().evaluate(element => element.closest('svg')?.getBoundingClientRect().width || 0);
      assert(edgeSvgWidth > 100, `edge SVG should have a drawable viewport; got ${edgeSvgWidth}px`);
      await expect(page.locator('.react-flow__edge-path').first()).toHaveCSS('stroke-width', '2px');
      const restoredNodeTransform = await page.locator('.react-flow__node[data-id="saved-prompt"]').getAttribute('style');
      assert.match(restoredNodeTransform || '', /translate\(480px, 160px\)/, 'saved node position should be restored');
      if (viewport.width === 1440) {
        await page.getByRole('button', { name: '保存', exact: true }).click();
        await expect(page.getByText('作品已保存')).toBeVisible();
        assert.equal(saveRequests, 1, 'explicit save should submit once');
        assert.deepEqual(savedPayload.nodes.find(node => node.id === 'saved-prompt').position, { x: 480, y: 160 });
        assert.equal(savedPayload.edges.find(edge => edge.id === 'saved-edge').targetHandle, 'prompt');

        await page.getByRole('button', { name: /图片素材/ }).click();
        await page.getByLabel('上传图片素材').setInputFiles({
          name: 'first-frame.png', mimeType: 'image/png', buffer: Buffer.from('png'),
        });
        await expect(page.locator('.video-node-inspector').getByText('first-frame.png', { exact: true })).toBeVisible();
        const assetPreview = page.getByRole('img', { name: 'first-frame.png' });
        await expect(assetPreview).toBeVisible();
        await expect.poll(() => assetPreview.evaluate(image => image.naturalWidth)).toBeGreaterThan(0);
        assert.equal(uploadRequests, 1, 'image asset node should upload its selected image exactly once');
        await expect.poll(() => savedPayload?.nodes.find(node => node.type === 'image_asset')?.data?.asset_ref)
          .toBe('asset://uploaded-frame-1');
      }
      if (viewport.width === 1440) {
        const saveBounds = await page.getByRole('button', { name: '保存', exact: true }).boundingBox();
        const runBounds = await page.getByRole('button', { name: '运行工作流', exact: true }).boundingBox();
        assert(saveBounds && runBounds && Math.abs(saveBounds.y - runBounds.y) < 8,
          'save and run controls should stay together in the canvas action row');
      }

      const canvas = page.locator('.video-canvas');
      const canvasBounds = await canvas.boundingBox();
      const layout = viewport.width <= 390 ? await page.evaluate(() => ['.video-canvas-editor-primary', '.video-canvas-shell', '.video-node-palette', '.video-canvas'].map(selector => {
        const element = document.querySelector(selector);
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return { selector, x: rect.x, width: rect.width, height: rect.height, display: style.display, columns: style.gridTemplateColumns, rows: style.gridTemplateRows };
      })) : undefined;
      assert(canvasBounds && canvasBounds.width > 160 && canvasBounds.x + canvasBounds.width <= viewport.width,
        `canvas should be usable and remain within ${viewport.width}px viewport: ${JSON.stringify({ canvasBounds, layout })}`);
      const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
      assert.equal(horizontalOverflow, false, `video workflow must not overflow horizontally at ${viewport.width}px`);

      await page.getByRole('tab', { name: '分镜编辑' }).click();
      await expect(page.locator('.video-storyboard')).toBeVisible();
      await expect(page.getByRole('button', { name: '展开画布' })).toHaveClass(/btn-ghost/);
      await expect(page.locator('.video-storyboard-header-actions').getByRole('button', { name: '运行全部', exact: true })).toHaveClass(/btn-primary/);
      if (viewport.width === 1440) {
        await page.getByRole('button', { name: 'AI 创作助手' }).click();
        await page.getByLabel('故事创意').fill('主角在花园寻找神秘线索');
        await page.getByLabel('分镜数').fill('1');
        await page.getByRole('button', { name: '生成分镜草稿' }).click();
        await expect(page.getByText('Gemini · gemini-preview-fast · 仅生成文字草稿 · Gemini 原生结构化输出已通过校验')).toBeVisible();
        await expect(page.getByLabel('画面提示词')).toHaveValue('花园里主角发现闪光的线索。');
        assert.equal(runRequests, 0, 'AI drafting must not submit a paid media run');
        await page.getByRole('button', { name: '应用到当前工作流' }).click();
        await expect(page.locator('.video-storyboard-row')).toHaveCount(2);
        await expect(page.locator('.video-storyboard-row').nth(1).getByLabel('图片提示词'))
          .toHaveValue('花园里主角发现闪光的线索。');
        assert.equal(runRequests, 0, 'applying an AI draft must not start image or video generation');
        await page.getByRole('tab', { name: '节点工作流' }).click();
        await expect(page.getByText('Restored prompt node', { exact: true })).toBeVisible();
      }
      await page.getByRole('tab', { name: '节点工作流' }).click();
      await expect(page.locator('.video-canvas-editor-primary')).toBeVisible();
      await expect(page.getByText('Restored prompt node', { exact: true })).toBeVisible();
      await context.close();
    }
    console.log('Primary workflow canvas, graph restore, view switching and responsive bounds passed');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
