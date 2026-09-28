# AI Video Canvas-First Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 `/admin/video` 为右键添加节点的单一画布，让一个提示词生成多张图片并可选择首尾帧生成视频。

**Architecture:** FastAPI provider adapter 扩展批量图片响应和首尾帧视频请求；RunService 将生成候选图归档到已有 AssetStore，支持目标节点运行并复用已选素材；React Flow 画布负责右键添加、候选图选择和单一视频预览工作流。

**Tech Stack:** React 19.3.0、TypeScript 7.0.2、Vite 8.3.0、`@xyflow/react` 12.10.0、FastAPI 0.141.1、Python 3.12。

**Spec:** `docs/superpowers/specs/2026-09-23-ai-video-canvas-first-design.md`

## Global Constraints

- Keep React 19.3.0, React DOM 19.3.0, TypeScript 7.0.2, Vite 8.3.0 and plain CSS.
- Keep the current `@xyflow/react` graph editor; do not add a second editor or new frontend dependencies.
- Keep media credentials server-side; candidate/workflow/run data must never contain `api_key` or authorization values.
- Preserve current admin-session and same-origin checks on all write routes.
- Preserve saved workflow IDs, legacy `storyboard` metadata and existing run history data; remove only the video page's history reader and drawer.
- Follow TDD for every behavior: write the failing test, run it and verify the intended failure, implement, then rerun focused tests.
- Do not issue paid provider generation requests in tests. Use provider fakes and response fixtures.
- Do not commit, push, deploy, clear data, or alter credentials as part of this plan.

## File Map

- `hysteria/web_api/video_models.py`: provider request/job/capability value objects.
- `hysteria/web_api/video_provider.py`: image batch parsing and model-gated `last_frame` request shape.
- `hysteria/web_api/video_service.py`: generated image archiving, role-output resolution and target-node DAG snapshots.
- `hysteria/web_api/video_routes.py`: public capability/run presentation, target-node request parsing, removal of the page-only assistant-draft endpoint.
- `frontend/src/features/video/VideoPage.tsx`: the single workflow page, saved graph migration, staged image/video runs and current-task feedback.
- `frontend/src/features/video/VideoCanvas.tsx`: pane context menu and mobile add menu; remove the permanent node palette.
- `frontend/src/features/video/VideoNode.tsx`: node-specific form, candidate gallery, frame-role outputs and inline run state.
- `frontend/src/features/video/videoApi.ts`, `videoTypes.ts`: API and persisted workflow types.
- `frontend/src/styles/sections/21-video.css`, `22-video-storyboard.css`: canvas-first layout, node gallery and mobile behavior; remove dead storyboard/assistant styles.
- `frontend/src/features/video/VideoStoryboard.tsx`, `VideoCreativeAssistant.tsx`, `VideoTemplates.ts`: remove only after references and migration code have moved or been deleted.
- Tests: `tests/test_video_provider.py`, `tests/test_video_runs.py`, `tests/test_video_routes.py`, `tests/test_video_ui_contract.py`, `tests/test_video_storyboard_ui.cjs`, `tests/react_video_primary_canvas_browser.cjs`.

---

### Task 1: Batch image and model capability contracts

**Files:**
- Modify: `hysteria/web_api/video_models.py`
- Modify: `hysteria/web_api/video_provider.py`
- Test: `tests/test_video_provider.py`
- Test: `tests/test_video_routes.py`

**Interfaces:**
- `ImageRequest` gains `n: int = 1`; reject boolean or integer values outside 1–10 before sending a request.
- `ProviderJob` gains `asset_urls: list[str]`; existing `asset_url` remains for video-job compatibility.
- `Capabilities` gains `image_batch_models: list[str]` and `first_last_frame_models: list[str]`; `first_last_frame.supported` remains as the compatibility summary.
- `GrokVideoProvider.generate_image()` sends JSON `n` and parses every valid `data[].url` in response order.
- `GrokVideoProvider.generate_video()` sends `image: {url}` and `last_frame: {url}` only for model IDs advertised in `first_last_frame_models`; unsupported IDs raise `ProviderError('first_last_frame_unsupported')`.

- [x] **Step 1: Add failing tests for image batches and role-frame requests**

```python
def test_generate_image_sends_n_and_returns_all_data_urls():
    seen = {}
    response = _Response({'data': [
        {'url': 'https://provider.test/v1/media/images/a'},
        {'url': 'https://provider.test/v1/media/images/b'},
        {'url': 'https://provider.test/v1/media/images/c'},
        {'url': 'https://provider.test/v1/media/images/d'},
    ]})
    provider = GrokVideoProvider(opener=_opener(response, seen))
    job = provider.generate_image(
        ImageRequest(prompt='forest', model='grok-imagine-image', n=4),
        VideoSettings('https://provider.test/v1', 'secret'),
    )
    assert seen['body']['n'] == 4
    assert job.asset_urls == [
        'https://provider.test/v1/media/images/a',
        'https://provider.test/v1/media/images/b',
        'https://provider.test/v1/media/images/c',
        'https://provider.test/v1/media/images/d',
    ]

def test_video_provider_sends_last_frame_for_verified_model():
    seen = {}
    provider = GrokVideoProvider(opener=_opener(_Response({'id': 'job-1'}), seen))
    provider.generate_video(
        VideoRequest(prompt='camera moves', model='grok-imagine-video-1.5',
                     first_frame_url='asset://first', last_frame_url='asset://last'),
        VideoSettings('https://provider.test/v1', 'secret'),
    )
    assert seen['body']['image'] == {'url': 'asset://first'}
    assert seen['body']['last_frame'] == {'url': 'asset://last'}
```

- [x] **Step 2: Run `PYTHONPATH=hysteria /root/hysteria/.venv-web/bin/python -m pytest -q tests/test_video_provider.py` and confirm the failures are missing batch/capability behavior**
- [x] **Step 3: Implement bounded image `n`, ordered list parsing, and model-scoped frame capabilities while preserving the singular video asset field**
- [x] **Step 4: Add boundary tests for `n=1`, `n=10`, invalid counts, empty/invalid image entries, unadvertised video model rejection, and redacted provider failures; run `PYTHONPATH=hysteria /root/hysteria/.venv-web/bin/python -m pytest -q tests/test_video_provider.py tests/test_video_routes.py`**

### Task 2: Archive candidates and execute only the selected target branch

**Files:**
- Modify: `hysteria/web_api/video_service.py`
- Modify: `hysteria/web_api/video_routes.py`
- Test: `tests/test_video_runs.py`
- Test: `tests/test_video_routes.py`
- Test: `tests/test_video_workflows.py`

**Interfaces:**
- `RunService.submit(workflow_id, ..., target_node_id=None)` preserves current full-workflow behavior; when `target_node_id` is given it snapshots only that node and its transitive ancestors.
- Image-run completion archives each provider image via the existing same-origin `provider.open_asset()` boundary into `AssetStore.save_upload()` and persists `asset://<id>` references.
- A completed `text_to_image` node with `data.candidate_asset_refs` is treated as a cached source and does not submit a second image request.
- `text_to_image` keeps the `image` output and adds `first_frame` and `last_frame`; those outputs resolve from `selected_first_asset_ref` and `selected_last_asset_ref`.
- `POST /api/video/runs` accepts an optional `target_node_id`; `present_run()` converts a node's candidate asset list to authenticated `/api/video/assets/{asset_id}/content` URLs.

- [x] **Step 1: Add failing tests for candidate archiving and target-node execution**

Add this `CandidateProvider` test fixture, importing `io` and `ProviderMedia` and using a `VideoSettings('https://provider.test/v1', 'secret')` value:

```python
class CandidateProvider(FakeProvider):
    def __init__(self, image_count):
        super().__init__()
        self.image_urls = [
            f'https://provider.test/v1/media/images/candidate-{index}'
            for index in range(image_count)
        ]

    def generate_image(self, request, settings):
        del settings
        self.image_requests.append(request)
        self.image_calls += 1
        return ProviderJob('', state='succeeded', asset_urls=self.image_urls)

    def open_asset(self, asset_url, settings, *, range_header=None):
        del asset_url, settings, range_header
        png = b'png'
        return ProviderMedia(io.BytesIO(png), 200, 'image/png', str(len(png)), None)
```

```python
def test_image_run_archives_every_candidate_and_exposes_asset_refs(tmp_path):
    workflows = workflow_store(tmp_path)
    saved = workflows.save({'title': 'batch', 'nodes': [
        {'id': 'prompt-node', 'type': 'prompt', 'data': {'text': 'forest'}},
        {'id': 'image-node', 'type': 'text_to_image', 'data': {
            'model': 'grok-imagine-image', 'n': 3,
        }},
    ], 'edges': [
        {'source': 'prompt-node', 'sourceHandle': 'text',
         'target': 'image-node', 'targetHandle': 'prompt'},
    ]})
    provider = CandidateProvider(image_count=3)
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'),
                         provider, tmp_path / 'runs.json',
                         asset_store=AssetStore(tmp_path / 'assets'))
    result = service.tick(service.submit(saved['id'], target_node_id='image-node')['id'])
    refs = result['assets']['image-node']
    assert len(refs) == 3
    assert all(ref.startswith('asset://') for ref in refs)

def test_video_target_reuses_archived_candidates(tmp_path):
    workflows = workflow_store(tmp_path)
    assets = AssetStore(tmp_path / 'assets')
    first = assets.save_upload('first.png', 'image/png', b'first-frame')
    last = assets.save_upload('last.png', 'image/png', b'last-frame')
    saved = workflows.save({'title': 'reuse', 'nodes': [
        {'id': 'prompt-node', 'type': 'prompt', 'data': {'text': 'forest'}},
        {'id': 'image-node', 'type': 'text_to_image', 'data': {
            'model': 'grok-imagine-image',
            'candidate_asset_refs': [f"asset://{first['id']}", f"asset://{last['id']}"],
            'selected_first_asset_ref': f"asset://{first['id']}",
            'selected_last_asset_ref': f"asset://{last['id']}",
        }},
        {'id': 'video-node', 'type': 'first_last_frame_video', 'data': {
            'model': 'grok-imagine-video-1.5', 'prompt': 'camera moves',
        }},
    ], 'edges': [
        {'source': 'prompt-node', 'sourceHandle': 'text',
         'target': 'image-node', 'targetHandle': 'prompt'},
        {'source': 'image-node', 'sourceHandle': 'first_frame',
         'target': 'video-node', 'targetHandle': 'first_frame'},
        {'source': 'image-node', 'sourceHandle': 'last_frame',
         'target': 'video-node', 'targetHandle': 'last_frame'},
    ]})
    provider = CandidateProvider(image_count=4)
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'),
                         provider, tmp_path / 'runs.json', asset_store=assets)
    service.tick(service.submit(saved['id'], target_node_id='video-node')['id'])
    assert provider.image_calls == 0
    assert provider.video_calls == 1
    assert provider.video_requests[0].first_frame_url.startswith('data:image/png;base64,')
    assert provider.video_requests[0].last_frame_url.startswith('data:image/png;base64,')
```

- [x] **Step 2: Run `PYTHONPATH=hysteria /root/hysteria/.venv-web/bin/python -m pytest -q tests/test_video_runs.py tests/test_video_workflows.py` and verify failures occur on missing target selection/asset-list behavior**
- [x] **Step 3: Implement transitive ancestor filtering, selected-role port resolution, cached image-node readiness, and bounded archival using the existing provider media boundary and AssetStore size limits**
- [x] **Step 4: Add tests that reject unknown target IDs, missing first/last frame selections, cycles, asset-storage-full responses and provider media URLs outside the configured same-origin media path; verify existing full-run and old single-image tests still pass**
- [x] **Step 5: Extend `present_run()` and the route tests for image-asset arrays; verify unauthenticated asset reads remain denied and admin responses contain no upstream credential or raw external URL**

### Task 3: Right-click canvas and node-level candidate selection

**Files:**
- Modify: `frontend/src/features/video/VideoCanvas.tsx`
- Modify: `frontend/src/features/video/VideoNode.tsx`
- Modify: `frontend/src/features/video/videoTypes.ts`
- Modify: `frontend/src/features/video/VideoPage.tsx`
- Test: `tests/react_video_primary_canvas_browser.cjs`

**Interfaces:**
- `VideoCanvas` accepts `onAddNode(type, flowPosition, selectedNode)` and reports `onSelect(node)`; right-click pane coordinates are converted with `screenToFlowPosition()`.
- If the selected node is a `prompt`, adding `text_to_image` places it to the prompt's right and creates the valid `text` → `prompt` edge; otherwise the node uses the requested canvas position.
- A touch-visible `＋ 添加节点` button opens the same node list on narrow screens.
- A `text_to_image` node displays candidate thumbnails and supports one selected first-frame ID and one selected last-frame ID; its outputs expose `image`, `first_frame`, and `last_frame` handles.

- [x] **Step 1: Update the browser test with a failing right-click test**

```js
await page.locator('.react-flow__node[data-id="saved-prompt"]').click();
await page.locator('.react-flow__pane').click({ button: 'right', position: { x: 1160, y: 440 } });
await page.getByRole('menuitem', { name: '文生图' }).click();
await expect(page.locator('.react-flow__edge')).toHaveCount(2);
await expect(page.locator('.react-flow__node')).toHaveCount(3);
```

- [x] **Step 2: Run `npm run build:react`, then `TMPDIR=/dev/shm VIDEO_TEST_DIST="$PWD/frontend/dist" PYTHONPATH=hysteria /root/hysteria/.venv-web/bin/python tests/run_video_primary_canvas_browser.py`; confirm the updated right-click assertion fails because the pane menu is absent**
- [x] **Step 3: Implement an accessible pane menu anchored to the right-click point, escape/outside dismissal, node-context delete/duplicate actions, prompt auto-connect, and the mobile add menu; remove the permanent palette**
- [x] **Step 4: Render the generated-image candidate gallery inside its node, with disabled/selected state for “设为首帧” and “设为尾帧”; run the focused browser case and TypeScript typecheck**

### Task 4: Collapse the page to one workflow and wire staged image/video runs

**Files:**
- Modify: `frontend/src/features/video/VideoPage.tsx`
- Modify: `frontend/src/features/video/videoApi.ts`
- Modify: `frontend/src/features/video/videoTypes.ts`
- Modify: `frontend/src/features/video/VideoRunPanel.tsx`
- Modify: `hysteria/web_api/video_routes.py`
- Modify: `tests/test_video_ui_contract.py`
- Modify: `tests/test_video_storyboard_ui.cjs`
- Modify: `tests/test_video_routes.py`

**Interfaces:**
- `createRun(workflowId, targetNodeId?)` sends `{workflow_id, target_node_id}` and does not request the historical run collection.
- Clicking “生成候选图” runs only the selected text-to-image node; successful candidates are copied into its persisted node data and workflow save state.
- “设为首帧/尾帧” persists the selected `asset://` ID on the text-to-image node; video run submits only its dependency branch and does not repeat image generation.
- `VideoRunPanel` remains for the current run only and renders success/failure inline; historical run list state, drawer, and `loadVideoRuns()` page call are removed.
- The page removes the storyboard tab, assistant UI, direct `/api/video/assistant/draft` route/client call, and `/admin/services?tab=ai` link. Keep `VideoAssistantResult` and shared AI-service schema validation used by `hysteria/web_api/ai/routes.py`.
- Loading/saving a workflow preserves its `id`, `nodes`, `edges`, and legacy `storyboard` object; no workflow, run, asset, or global configuration deletion occurs.

- [x] **Step 1: Change existing static UI contract tests and route tests to assert the old page entries and direct video assistant-draft endpoint are absent while the shared `VideoAssistantResult` schema remains available**
- [ ] **Step 2: Run `PYTHONPATH=hysteria /root/hysteria/.venv-web/bin/python -m pytest -q tests/test_video_ui_contract.py tests/test_video_routes.py` and `node --test tests/test_video_storyboard_ui.cjs`; verify they fail on the current duplicated page**
- [x] **Step 3: Remove the two-view state, storyboard page rendering, run-history loader/drawer, creative-assistant import/handler, service link, and now-unreferenced UI files/types/styles without deleting shared API schema or stored workflow data**
- [x] **Step 4: Implement staged target-node run actions, selected asset persistence, workflow reuse, and compact current-run status; keep save/restore/autosave behavior**
- [x] **Step 5: Run the focused route/UI tests and TypeScript typecheck; correct regressions while keeping the legacy workflow shape round-trip intact**

### Task 5: Responsive acceptance, full verification and cleanup

**Files:**
- Modify: `frontend/src/styles/sections/21-video.css`
- Modify: `frontend/src/styles/sections/22-video-storyboard.css`
- Modify: `tests/react_video_primary_canvas_browser.cjs`
- Modify: relevant video UI contract tests from Tasks 3–4

- [x] **Step 1: Add browser assertions for selecting separate first/last candidates, then clicking “生成视频” and confirming only the video target run is submitted**
- [x] **Step 2: Add a mobile viewport assertion that the plus menu can add “文生图”, the inspector can be dismissed, and candidate selection controls are reachable without horizontal page overflow**
- [x] **Step 3: Add page-level assertions that no storyboard tab, task history, creative assistant or service configuration entry renders; confirm a legacy workflow with `storyboard` metadata still loads and saves that metadata**
- [x] **Step 4: Implement responsive canvas, node gallery, selection badges, current-run feedback and keyboard-accessible context menu styling using existing design tokens**
- [x] **Step 5: Run `npm run typecheck:react`, `npm run build:react`, `npm run test:react-assets`, `npm run lint:css`, `PYTHONPATH=hysteria /root/hysteria/.venv-web/bin/python -m pytest -q tests/test_video_provider.py tests/test_video_runs.py tests/test_video_workflows.py tests/test_video_routes.py tests/test_video_ui_contract.py`, and the focused video browser tests**
- [x] **Step 6: Run `git diff --check`; inspect the final diff for credential exposure, any accidental deletion of `runs.json`/workflow data logic, stale assistant/history imports, and dead storyboard selectors**

## Completion Review

- Have a fresh GPT-6 Sol / high reviewer compare the final diff against the design spec and test evidence. The reviewer is read-only and must report `PASS`, `REWORK`, `REPLAN`, `BLOCKED`, or `NEEDS_INPUT` with evidence.
- Do not deploy or commit unless the user separately authorizes those release actions.
