# ComfyUI 风格视频工作流设计

**日期：** 2026-09-18
**状态：** 已获执行授权，进入实现计划阶段
**范围：** `/root/hy2` React/Vite 前端、FastAPI 视频工作流接口和一个经验证的第三方 OpenAI-compatible 媒体供应商

## 目标

在现有 Hysteria React 工作台中增加管理员专用 `/admin/video` 页面。页面使用 React Flow 提供 ComfyUI 风格的节点画布，但不迁移 ComfyUI 的 Vue 页面、后端协议或部署系统。所有图片、视频和任务查询都经由现有 FastAPI `:8083` 转发到当前已部署的 Grok2API `:13004`，不新增服务或端口。

第一版必须支持可验证的提示词生图、图生视频、任务查询、结果预览/下载、工作流保存恢复和有向无环执行。首尾帧和视频合成只有在供应商协议和真实请求通过验证后才显示为可用；没有证据的能力以 `unsupported_capability` 返回，不能在 UI 中伪造支持。

## 不变的边界

- React 19.3.0、React DOM 19.3.0、TypeScript 7.0.2、Vite 8.3.0、普通 CSS保持不变。
- 保留自研 `window.history.pushState`/`popstate` 路由，不引入 React Router、Vue、Next.js、Tailwind、Node 后端或第二个 FastAPI/Uvicorn。
- FastAPI/Uvicorn 继续由 `react_server:app` 监听 `127.0.0.1:8083`；生产链路仍为 Nginx 443/9444 → 8083。
- 不修改或提交现有 Agent 未提交文件，不修改 `docs/superpowers/specs/2026-09-12-http-cutover-notes.md` 或 `.codex-guard/`。
- 不读取、记录或返回 API Key；不把完整第三方请求、响应、提示词、Authorization 或媒体正文写入日志。
- 所有视频接口仅允许管理员 Session；写操作必须通过现有 same-origin/CSRF 校验。

## 供应商与实际能力边界

第一版使用当前管理员配置的 Grok2API OpenAI-compatible 地址作为唯一供应商。服务端保存 Base URL 和 Key，浏览器只看到脱敏配置及能力清单。

截至本设计基线的运行时证据：

- `GET /v1/models` 能返回图像和视频模型目录，但目录只表示路由存在，不代表账号有额度。
- `POST /v1/images/generations` 已验证 `grok-imagine-image`、`grok-imagine-image-2.0` 和 `grok-imagine-image-lite` 返回成功。
- `POST /v1/videos/generations` + `GET /v1/videos/{id}` 已验证 `grok-imagine-video` 能创建并完成任务。
- `grok-imagine-video-1.5` 当前返回额度耗尽，因此能力清单保留模型标识但标记当前不可调度，不把 429 当作代码成功。
- 首帧/尾帧字段、取消任务语义、视频拼接/转场/音频合成尚未有足够协议证据，Phase A 通过后才开放相应节点。

供应商适配器必须在能力查询中返回每项能力的 `supported`、模型 ID、参数限制和原因；上游 401/403/404/429/5xx、超时和未知字段统一转成脱敏错误代码。

## 前端架构

### 路由与导航

- `main.tsx` 增加 `/admin/video` 文档路由、页面元数据和管理员门控。
- `document_routes.py` 增加精确 `/admin/video` React 文档入口；匿名访问按现有规则重定向 `/login?next=/admin/video`。
- `navigation.ts` 在网络配置/工具区域增加“AI 视频”，目标固定为 `/admin/video`。
- 只扩展当前自研路由集合，不添加宽泛 SPA fallback；未知路径继续 404。

### 页面组成

新增 `frontend/src/features/video/`：

- `VideoPage.tsx`：页面状态、工具栏、画布和任务抽屉的组合。
- `videoApi.ts`：严格解析 `/api/video/*` 响应，不保存 Key。
- `videoTypes.ts`：节点、边、工作流、能力和运行状态类型。
- `VideoCanvas.tsx`：动态加载 React Flow 和节点渲染器。
- `VideoNode.tsx`：提示词、图片素材、文生图、图生视频、首尾帧和结果节点。
- `VideoTemplates.ts`：三个预置 DAG 模板。
- `VideoSettingsDrawer.tsx`、`VideoRunPanel.tsx`、`VideoAssetPreview.tsx`：设置、任务状态和媒体预览。

新增 `frontend/src/styles/sections/21-video.css`，只使用当前 CSS token、边框、阴影、圆角和响应式断点。桌面保持三栏画布；iPad 竖屏与手机将节点库、属性面板和任务列表改为抽屉，使用 `100dvh` 与安全区内边距。

React Flow 作为按路由加载的独立 Vite chunk，其他管理员页面不加载画布依赖。节点坐标、选择和连线只保存在当前工作流状态，真正的任务提交必须由显式“运行”操作触发。

## 后端模块

新增 `hysteria/web_api/video_models.py`、`video_provider.py`、`video_service.py`、`video_routes.py`，在 `web_api/app.py` 中以现有 `register_*_routes` 模式注册。

### 供应商适配器接口

适配器提供以下窄接口，具体 HTTP 字段只在 Phase A 验证后实现：

```python
class VideoProvider(Protocol):
    def capabilities(self, settings: VideoSettings) -> Capabilities: ...
    def generate_image(self, request: ImageRequest) -> ProviderJob: ...
    def generate_video(self, request: VideoRequest) -> ProviderJob: ...
    def get_job(self, provider_job_id: str) -> ProviderJobStatus: ...
    def cancel_job(self, provider_job_id: str) -> CancelResult: ...
```

`VideoProvider` 不接触 HTTP 请求上下文、不记录密钥、不返回原始响应。适配器内部使用现有 Python HTTP 依赖（优先 `httpx`），连接、读取和总时长均有上限。

### API 路由

所有路由先调用管理员 Session 检查；PUT/POST/DELETE 还要检查 same-origin：

```text
GET    /api/video/settings
PUT    /api/video/settings
POST   /api/video/connection/test
GET    /api/video/capabilities

GET    /api/video/workflows
POST   /api/video/workflows
GET    /api/video/workflows/{workflow_id}
DELETE /api/video/workflows/{workflow_id}

POST   /api/video/assets
GET    /api/video/assets
GET    /api/video/assets/{asset_id}/content

POST   /api/video/runs
GET    /api/video/runs
GET    /api/video/runs/{run_id}
POST   /api/video/runs/{run_id}/cancel
```

设置响应只能包含 `base_url`、`api_key_configured`、`api_key_masked` 和非敏感的供应商选项。第三方错误只返回固定错误代码、HTTP 状态类别、重试提示和脱敏说明。

## 持久化与恢复

运行时目录使用 `/root/hysteria/state/video/`：

- `settings.json`：供应商地址、加密/受限 Key、默认参数，权限 0600。
- `workflows.json`：工作流定义和版本，权限 0600。
- `runs.json`：任务快照、节点状态、第三方任务 ID、素材引用和脱敏错误，权限 0600。
- `assets/`：管理员上传的图片或已归档媒体，目录权限 0700；单文件大小、总缓存大小和媒体类型均有限制。

写入统一复用 `state_store.file_lock` 与 `state_store.save_json` 的原子替换语义。提交任务前保存完整工作流和节点参数快照；提交超时先查询供应商任务，不自动重复付费提交。服务重启后，后台有界轮询恢复 `queued`/`running` 任务；已完成任务不重复请求。

供应商临时 URL 不直接当作永久素材。若无法安全归档，则只显示带过期提示的受保护代理 URL，不能声称永久保存。

## 工作流执行语义

工作流是 DAG，运行前执行：

1. 节点 ID、类型、端口和参数 schema 校验。
2. Kahn 拓扑排序检测循环。
3. 输入端口完整性与类型兼容性检查。
4. 上游节点成功后才提交下游节点。
5. 节点参数变化只标记下游为 `needs_regenerate`，不自动产生费用。

状态集合：`draft`、`queued`、`running`、`succeeded`、`failed`、`cancel_requested`、`cancel_unsupported`、`needs_regenerate`。

“停止后续节点”与“取消已提交任务”分开显示。供应商拒绝取消时保留真实状态和提示，禁止伪造成功取消。

## 安全与容量

- API Key 只在 FastAPI 内存中使用，绝不进入浏览器、localStorage、工作流 JSON、错误响应或日志。
- 资产上传仅允许受支持的图片/视频 MIME 类型，限制大小并拒绝路径穿越。
- 结果预览/下载通过管理员认证的 `/content` 路由；不直接暴露任意本地路径。
- 连接第三方只允许明确配置的 HTTPS/loopback HTTP 地址，禁止把浏览器任意 URL 当作代理目标。
- 请求体、提示词和响应正文不写审计日志；审计只保存模型、节点类型、耗时、状态和用量元数据。
- 运行并发、轮询频率、单任务总时长和磁盘占用都有上限。

## 测试与验收

采用 TDD，每个新增行为先写失败测试再实现。最小覆盖包括：

- 管理员/匿名/跨站访问边界和 `/admin/video` 文档门控。
- 设置脱敏、0600 权限、原子写入和 Key 不出现在响应/日志。
- Grok 图片提交、视频提交、任务查询、429/401/404/5xx/超时错误分类。
- 工作流 DAG 拓扑排序、循环拒绝、端口类型校验和失败阻断下游。
- 任务刷新恢复、提交超时不重复提交、取消不支持的真实状态。
- 资产 MIME/大小/路径校验、鉴权预览和过期 URL 提示。
- React 路由、导航、节点连线、模板加载、刷新恢复和响应式抽屉。

每个阶段至少运行：

```bash
npm run typecheck:react
npm run build:react
python3 -m pytest -q tests/test_video_*.py tests/test_react_video_*.py
git diff --check
```

完成后再运行现有前端检查、Python 全量回归和浏览器测试。生产发布必须使用版本化 dist、`nginx -t`、systemd 状态检查和 443/9444 smoke test，并保留上一版本回滚入口。

## 不在第一版内

- ComfyUI 原版 Vue 页面或后端协议。
- 语音、音频、联网、MCP、Agent、RAG、多用户计费。
- 未被供应商验证的首尾帧、取消、转场、音频合成。
- 新的数据库、Redis、Node 服务或第二个 Uvicorn。
