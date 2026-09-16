# Codex 风格统一 App Shell 重构设计

**日期：** 2026-09-16
**状态：** 已获设计确认，待 spec 审阅
**范围：** `/root/hy2` React/Vite 前端与 React 文档入口；保留既有 FastAPI/认证/网络业务边界

## 目标

将当前 Hysteria 宣传首页和独立登录页改造成一个统一的、浅色、极简的 Hysteria AI 工作台。访问 `/` 直接进入 AI Chat；未认证用户看到同一个工作台和登录模态框，认证成功后在原位置恢复。现有管理员页面继续工作并使用同一个 Shell，页面业务逻辑和数据接口保持不变。

视觉与交互只参考公开项目的布局思想（会话侧栏、中央线程、底部 composer、toolbar、响应式抽屉），不复制 `Yrd980/codex-web-ui-clone`、`dfones288/codex-desktop` 的源码或素材，也不使用 OpenAI 商标和品牌资源。继续使用 Hysteria/Lanco 自有文案、颜色和标识。

## 不变的边界

- React 19.3.0、React DOM 19.3.0、TypeScript 7.0.2、Vite 8.3.0、`@vitejs/plugin-react`、普通 CSS。
- 自研 `pushState`/`popstate` 路由；不引入 React Router、Tailwind、Next.js、Vue、Electron、Tauri 或 Node 后端。
- FastAPI 0.141.1 + Uvicorn 的 `react_server:app` 继续监听 `127.0.0.1:8083`。
- 浏览器只请求 `/api/chat/completions`；FastAPI 从 Chat Settings 读取服务端配置，再请求 `http://127.0.0.1:8088/v1` 或管理员配置的 API Base URL。API key 永远不进入 HTML、前端 bundle、浏览器响应、日志或 localStorage。
- `/api/chat/*` 继续要求现有管理员 Session、CSRF 和 same-origin；不增加公共 `/v1/chat/completions`。
- 不覆盖 `docs/superpowers/specs/2026-09-12-http-cutover-notes.md`、`.codex-guard/`，不使用 `reset --hard`、`clean -fd` 或 force push。

## 架构

### CodexShell

新增 `frontend/src/shared/CodexShell.tsx`，负责整个 React 工作台的外壳：

1. 固定宽度约 240px 的 Sidebar（桌面）；主区域包含 toolbar、页面内容和底部 composer 所在的 workspace。
2. Sidebar 的导航由 `frontend/src/shared/navigation.ts` 的单一数据源生成，包含“新对话、搜索、最近对话”和现有管理员分组。
3. 接收 `active`、`pageTitle`、`badge`、`subtitle`、`topbarExtra`、`authRequired` 等明确 props；不读取或修改页面业务数据。
4. 管理侧栏折叠、移动端 drawer、scrim、Esc 关闭、焦点回收和 `hy2.sidebar` 偏好仍由 Shell 统一处理。
5. 现有 `AdminShell` 变为兼容适配层，保留原导出和调用签名，内部转发至 `CodexShell`。管理员页面只保留内容区，最终 DOM 中不得出现 Shell 套 Shell。

### Chat 工作区

`frontend/src/features/chat/ChatPage.tsx` 继续承载聊天业务状态（本地会话、消息、模型、推理级别、usage、设置抽屉），但移除自己的第二层应用侧栏和 `AdminShell` 包裹，改为使用 Shell 提供的会话区域/插槽。会话存储仍使用现有本地记录机制；未认证时不读取、不写入受保护的聊天历史，也不发起聊天或设置请求。

空状态使用简洁的工作提示，例如“今天有什么需要处理的？”，不再显示“连接网络，掌控全局”“服务能力”、能力卡片或营销 CTA。composer 固定在内容底部，支持发送、停止生成（当前请求可取消时启用）、复制回复、新对话、历史、Markdown 和代码块。

### 登录模态框与会话门控

新增 `frontend/src/features/auth/LoginModal.tsx`，复用 `loginRequest.ts`、密码长度 bootstrap 和现有 `/api/v1/login`，不重新实现认证。模态框支持管理员和用户 realm，包含账号、密码、提交、忙碌态、服务错误、失败次数提示、关闭/重新打开和可访问性语义。登录失败只在模态框内显示，不做文档跳转；成功后触发 Shell 的 session refresh，关闭模态框并恢复原 route、会话选择和输入状态。

新增或复用一个小型 session hook（放在 `frontend/src/shared` 或 `frontend/src/features/auth`）调用 `/api/v1/session`，将状态区分为 `loading`、`anonymous`、`authenticated`、`unavailable`。需要管理员权限的内容在 `authenticated` 前不挂载会读取数据的页面组件；匿名访问只呈现 Shell、空内容占位和 LoginModal。API 仍作为最终权限边界，匿名请求必须得到 401/`login_required`。

`LoginPage.tsx` 不再作为独立页面入口；可以保留兼容导出，但不得被 `/login` 或 `/user/login` 路由渲染。

## 路由与文档入口

### 浏览器路由

- `/`：统一 Shell + Chat 工作区；若匿名，自动打开管理员 LoginModal。
- `/admin/chat`：兼容入口，渲染同一个 Chat 工作区，使用统一 Shell。
- `/login`：兼容旧链接，渲染根 Shell 并自动打开管理员 LoginModal，不再返回独立登录布局。
- `/auth`：新增 React 文档兼容入口，行为等同 `/login`；只处理面板 GET 文档。现有认证服务的 POST `/auth` 语义保持不变。
- `/user/login`：兼容入口，根 Shell + 用户 realm LoginModal。
- `/admin`、`/admin/usage`、`/admin/health`、`/admin/incidents`、`/admin/logs`、`/admin/settings`、`/admin/config`、`/admin/rules`、`/admin/landing-egresses` 和 `/admin/user/{uid}`：继续使用原页面内容，统一置于 CodexShell。
- `/logout`、`/user/logout`、`/user/change-password`、`/user/panel`：保留既有业务和权限语义；需要认证的文档在客户端使用统一 Shell 门控。

### FastAPI 文档保护

React 文档入口可以为匿名用户返回不含业务数据的 Shell bootstrap，使受保护路由也能在原地址显示 LoginModal；所有实际数据 API 和 `/api/chat/*` 仍严格执行现有 Session/CSRF/same-origin 校验。带 token 的管理员交换、用户密码状态和非 React 兼容下载继续走原有服务端逻辑。若服务状态不可用，返回现有 503 文档错误，不泄露内部配置。

`document_routes.py` 的标题/body class 改为工作台语义（例如 `has-shell`/`page-workbench`），并为 `/auth` 添加精确 allowlist 项；未知路径仍返回 404，不做宽泛 SPA fallback。`main.tsx` 的客户端导航将 `/auth`、`/login`、`/user/login` 映射到 Shell 模态框，并保留 preview 路由兼容。

## 侧栏信息架构

顶部固定顺序：

1. `＋ 新对话`
2. 搜索输入
3. 最近对话（按今天、昨天、过去 7 天、更早分组）

管理员分组复用现有路由：

- 概览与用量：总览、流量分析
- 运行维护：健康状态、事故处理、清零日志、设置
- 网络配置：模板配置、路由规则、家庭出口

底部显示当前账号/退出和设置入口。所有链接指向既有路径；页面内不得再渲染旧的 Admin Sidebar。

## 模型、推理和上下文

- 不设置硬编码默认模型。
- “测试连接”通过已认证的 Chat Settings 调用服务端 `GET {base_url}/models`，缓存可用模型。
- 只有一个模型时自动选中；多个模型按当前会话选择；没有模型时显示清晰的配置/连接提示。
- toolbar 或 composer 显示模型选择、思考级别 `Auto / Low / Medium / High`、上下文 `current / max`。
- `Auto` 请求体省略 `reasoning_effort`；其他级别按现有 API 协议发送，第三方不支持时保留回复并显示友好降级提示。
- 上下文 current/max 优先使用 API usage；其次使用已知模型元数据；缺失时显示“未知”，不估算、不猜测。

## 数据流与错误处理

1. 页面加载时读取 `/api/v1/session`，期间显示 Shell loading 状态。
2. 匿名状态打开 LoginModal，禁用 composer、发送、历史读取和管理员数据加载。
3. 登录提交到 `/api/v1/login`；结构化失败（错误凭据、429、503、网络失败、超时）均转换为模态框可读错误，不改变 URL。
4. 成功响应设置既有 Session cookie；Shell 重新读取 session，解除门控，恢复原 route/active conversation。
5. 发送消息只调用 `/api/chat/completions`，沿用现有取消、回复解析、usage 和 `reasoning_unsupported` 处理；401 关闭受保护内容并重新打开 LoginModal。
6. API/模型/设置错误在对应 workspace 或 drawer 内显示，保留重试，不把服务端密钥、完整 upstream URL 中的凭据或内部异常堆栈呈现给浏览器。

## 响应式与视觉

- 桌面：固定 Sidebar + 受限最大宽度的 thread workspace。
- 平板：Sidebar 可折叠，保留 toolbar 和 composer 可用空间。
- 移动端：Sidebar 变为 drawer，scrim 与键盘 Esc 可关闭；composer 贴近 viewport 底部并适配安全区。
- 使用现有 section CSS 体系（`06-shell.css`、`18-chat.css` 及必要的新工作台 section），浅色背景、细边框、弱阴影、系统字体、足够留白；不保留营销首页作为根路由。

## 测试与验收证据

采用 TDD：每个行为先写一个会失败的最小测试，再实现并复跑。测试范围包括：

- Python 文档路由：`/`、`/auth`、`/login`、`/user/login` 的 bootstrap/标题/body class，匿名受保护文档不泄露业务数据，旧 token/session 行为不回归。
- React contract/typecheck：Shell 导航、路由兼容、LoginModal、session 状态和 Chat props。
- 浏览器回归：匿名根页面显示 Shell+LoginModal 且 composer 不可用；错误凭据留在模态框；成功登录后模态框关闭并恢复；刷新保留 Session；管理员导航全部可达；`/admin/chat` 和 `/auth` 兼容。
- 聊天代理：浏览器只访问 `/api/chat/completions`，FastAPI 转发至配置 upstream；匿名得到拒绝；Auto 不带 reasoning 字段；不支持推理时有降级提示；usage/context 缺失显示未知。
- 安全扫描：浏览器网络响应、HTML、localStorage、构建 bundle 和日志中均不得出现 API key。
- `npm run typecheck:react`、`npm run build:react`、现有 React/Python 回归测试和浏览器测试必须通过；使用 Playwright/现有视觉脚本保存桌面、平板、移动端截图并人工检查无双侧栏、无独立登录页、composer 固定和 drawer 行为。

## 完成交付物

最终报告包含：UI architecture、Routes changed、Login flow、Chat API flow、Files changed、Existing features preserved、Security verification、Build/tests、Screenshots / visual verification、Git status。所有新增提交只包含本次文件；现有 checkpoint 和并发修改保持可审阅状态。
