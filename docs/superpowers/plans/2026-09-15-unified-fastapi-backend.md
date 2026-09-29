# 统一 FastAPI 后端迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or
> superpowers:subagent-driven-development when a worker is available). Steps use
> checkbox syntax and each task has its own verification gate.

**Goal:** 将现有 8081 订阅兼容 HTTP 服务和 8082 Hysteria HTTP 认证服务迁入
8083 的 FastAPI/Uvicorn 应用，同时保持 URL、响应合同、认证边界和 React 前端不变。

**Architecture:** FastAPI 是唯一应用 HTTP 入口；`LegacyPanelServices` 继续调用
现有同步领域函数，新的订阅路由只负责将其结果转换为 `Response`/重定向/下载响应。
Hysteria HTTP 认证复用 `auth_backend` 的校验和限流逻辑，在 FastAPI 线程边界内运行。
Nginx 和 Hysteria 配置只指向 8083；8081/8082 的旧监听单元在完整回归通过后停用，
旧模块保留至验收结束并由备份支持回滚。

**Tech Stack:** Python 3、FastAPI、Uvicorn、AnyIO、Pydantic、React/Vite、pytest、
Nginx、systemd。

**Spec:** 用户提供的“统一 FastAPI 后端”迁移任务（pasted-text.txt）。

## Global Constraints

- 不读取、输出或记录生产凭证、Cookie、Token、私钥或 Session 密钥。
- 不修改工作区外的用户文件；`docs/superpowers/specs/2026-09-12-http-cutover-notes.md`
  和 `.codex-guard/` 不触碰、不提交。
- 不使用 `git reset --hard`、`git checkout --`、强制推送或破坏性回滚。
- 先在独立分支和隔离预览验证，再做版本化生产发布和原子回滚。
- 复用订阅、认证、二维码、会话、CSRF、限流和状态锁领域逻辑，不复制第二套算法。

### Task 1: 建立基线、路由合同和迁移备份

**Files:**
- Create: `tests/test_unified_fastapi_contracts.py`
- Create: `docs/superpowers/plans/2026-09-15-unified-fastapi-backend.md`
- Modify: none in user-owned paths
- Runtime backup: `/var/lib/hysteria/unified-fastapi-migration/<UTC>`

- [ ] 记录分支、HEAD、工作区脏文件、8081/8082/8083 监听、systemd 和 Nginx 路由。
- [ ] 为 `/auth`、`/livez`、`/readyz`、`/healthz`、`/sub/*`、`/panel/*`、旧表单路径
  和 React API 固定方法、状态码、Content-Type、Location、Cookie 属性、下载头及匿名边界。
- [ ] 仅备份本轮会修改的 systemd、Nginx、启动代码、旧入口和当前 release；不复制秘密。
- [ ] 运行现有后端/前端门禁，记录已有失败并确保新增合同测试先按旧服务通过。

### Task 2: 将 Hysteria HTTP 认证接入 FastAPI

**Files:**
- Create: `hysteria/web_api/auth_routes.py`
- Modify: `hysteria/web_api/app.py`, `hysteria/auth_service.py`（仅提取可复用纯函数/常量）
- Test: `tests/test_unified_fastapi_auth.py`, existing `tests/test_auth_service.py`

- [ ] 用 Pydantic/原始字节边界复用 `decode_auth_request`、`normalize_client_addr` 和
  `auth_backend.authenticate_payload`，保留 400/411/413/415/200/503 合同。
- [ ] 认证计算通过 `dispatch`/线程池运行，使用进程级 `PasswordWorkLimiter`，不阻塞事件循环。
- [ ] `/auth`、`/livez`、`/readyz`、`/healthz` 只返回 JSON 和安全头；不记录请求体。
- [ ] 为 malformed、重复 JSON 字段、限流、不可用状态和错误方法补回归测试。

### Task 3: 将订阅、面板交换、二维码和兼容下载接入 FastAPI

**Files:**
- Create: `hysteria/web_api/subscription_routes.py`
- Modify: `hysteria/web_api/services.py`, `hysteria/web_api/app.py`
- Test: `tests/test_unified_fastapi_subscriptions.py`, `tests/test_web_api_documents.py`

- [ ] 增加 `/sub/{user}`、`/panel/{user}`、`/panel/{user}.json`、
  `/panel/{user}/qr.svg` 路由，调用 `subscription_routes.Context` 对应的现有领域函数。
- [ ] 使用 `Response`、`RedirectResponse`、`JSONResponse` 保留 YAML/JSON/SVG、文件名、
  `subscription-userinfo`、profile 和缓存头，以及无效/禁用/过期 Token 的 403。
- [ ] 将 `/user/panel`、`/user/panel.json`、旧 `.fragment`/CSV/证据下载路径的兼容行为
  收敛到 8083；React 文档仍由 document router 负责，不能返回旧完整 HTML。
- [ ] 对 token 交换、Cookie 的 HttpOnly/Secure/SameSite/Path/Max-Age 和重定向逐项测试。

### Task 4: 去除生产对 8081/8082 的依赖

**Files:**
- Modify: `nginx/hysteria-panel-react.conf`, `nginx/hysteria-panel-react-https.conf`,
  `hysteria/config.yaml.tpl`, `systemd/hysteria-react.service`,
  `systemd/hysteria-server.service`, `deploy.sh`, health/recovery scripts
- Create/modify tests: `tests/test_unified_fastapi_wiring.py`

- [ ] 所有公开和内部应用路径 proxy 到 127.0.0.1:8083；订阅、panel、CSV、证据不再回源 8081。
- [ ] Hysteria `auth.http.url` 改为 8083；server 单元依赖 React 服务，不再依赖 auth 单元。
- [ ] deploy/recovery/health 脚本先启动并验证 8083，再停用 8081/8082；保留可回滚旧单元文件。
- [ ] 防回归测试禁止重新监听 8081/8082、禁止 Nginx proxy_pass 到旧端口、禁止内部 HTTP 回调。

### Task 5: 隔离预览、完整回归和生产发布

**Files:**
- Modify: `README`/架构文档和本计划的验收记录；仅在测试暴露问题时修改实现文件。
- Runtime: `/var/lib/hysteria/unified-fastapi-migration/<UTC>` release and rollback script

- [ ] 临时 loopback 端口启动单 worker FastAPI，使用测试状态做新旧语义比较。
- [ ] 运行完整 Python pytest、React 类型检查/ESLint/Stylelint/Vite/浏览器矩阵。
- [ ] 运行 `nginx -t`、systemd 配置验证和 443/9444/8083 smoke；确认 8081/8082 inactive/disabled。
- [ ] 构建 dist，安装版本化 release，原子切换 `panel/current`，失败立即按备份脚本回滚。
- [ ] 只在所有合同通过后提交并推送 `refactor/unified-fastapi-backend`，不合并或强推 main。

## 验收清单

- [ ] 8083 是唯一应用 HTTP 监听，FastAPI/Uvicorn 健康、认证和订阅接口均已注册。
- [ ] 443、9444 页面和 API 响应与基线兼容；React 资源路径不变。
- [ ] 8081、8082 无监听且 systemd disabled；旧文件和回滚目录仍可恢复。
- [ ] 认证、Session、CSRF、Cookie 安全属性、权限隔离和 Hysteria auth 均有证据。
- [ ] 最终报告列出完整路由表、差异测试、生产版本、提交号、备份目录和精确回滚命令。
