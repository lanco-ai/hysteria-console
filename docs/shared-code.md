# 与其他服务共享的代码

三个服务各在自己的分支（`service/network`、`service/shop`、`service/workbench-ai`）。下面这些文件在三个分支各有一份，来源都是拆分前的 `main` `dd6be5a`，本分支（网络管理）是它们的原始位置；商品和工作台 + AI 分支把 `hysteria/` 改名为 `server/`。修改其中任何一个时，另外两个分支里的对应文件也要同步修改（`git cherry-pick` 或手动改）。

## 后端

| 本分支文件 | 说明 | 其他分支中的对应位置 |
|---|---|---|
| `hysteria/http_utils.py` | 安全响应头、表单解析、同源校验 | `server/http_utils.py` |
| `hysteria/state_store.py` | 原子写入与文件锁 | `server/state_store.py` |
| `hysteria/timeutil.py` | `Asia/Shanghai` 时间桶 | `server/timeutil.py` |
| `hysteria/web_api/requests.py` | 请求头与表单读取 | `server/web_api/requests.py` |
| `hysteria/web_api/app.py` | 安全响应头中间件、并发池 `dispatch`、错误映射 | `server/web_api/app.py`（只注册各自路由） |
| `hysteria/web_api/document_routes.py` | 页面文档渲染与登录守卫 | `server/web_api/document_routes.py`（只含各自页面） |
| `hysteria/react_server.py` | release 指针校验（`resolve_react_dist`） | `server/shop_server.py`、`server/workbench_server.py` |
| `scripts/hy2_panel_release.py` | 前端 release 安装与切换 | 同名文件 |

## 前端

| 本分支文件 | 说明 |
|---|---|
| `frontend/index.html` | 页面模板（标题、`body` 类、`data-public-host` 标记） |
| `frontend/src/shared/*` | 后台外壳、侧边栏导航、会话读取、请求工具、图标 |
| `frontend/src/styles/*` | 全部样式（三个服务共用类名，暂未按服务裁剪） |
| `frontend/src/features/auth/LoginModal.tsx` 等 | 登录弹窗与登出 |
| `frontend/src/features/plans/PlanReminderCenter.tsx` | 今日计划提醒（调用工作台 + AI 的 `/api/plans/reminders`） |
| `frontend/src/features/agent/*` | LancoAgent 浮动助手（调用本服务的 `/api/v1/admin/rules`、`/api/v1/admin/agent/*`） |

侧边栏菜单（`frontend/src/shared/navigation.ts`）按服务独立维护，不能再同步成全站菜单。各站只展示自己的页面；商品门户的后台按钮进入商品管理，开源发现属于工作台。`CodexShell.tsx` / `PortalShell.tsx` 的菜单和组件挂载同样按服务裁剪，计划提醒只在工作台挂载。

## 跨服务接口的两端

| 接口 | 本服务一侧 | 另一侧 |
|---|---|---|
| 会话校验 `GET /api/v1/session` | 提供方（`hysteria/web_api/app.py`） | 商品、工作台 + AI 的 `RemoteSessionServices` |
| 意图解析 `POST /internal/v1/agent-intent` | 调用方（`hysteria/web_api/agent_service.py` 的 `RemoteIntentCompleter`） | 工作台 + AI 的 `agent_intent_routes.py` |

## 第一阶段的运行约定

- LancoAgent 通过 `/etc/lancoai/internal-token`（`root:hy2-xray 0640`）调用工作台 + AI；令牌由工作台 + AI 的安装脚本在缺失时创建。令牌缺失时助手返回 `settings_unavailable`，网络功能不受影响。
- 三个服务共用 `/root/hysteria/.venv-web`。`deploy.sh` 在 `lancoai-shop` 或 `lancoai-workbench` 运行时，如果 `requirements-web.txt` 会改变已安装的包，就在执行 pip 之前停止部署；先停这两个服务、部署、再启动它们。依赖不变的部署不受影响。
- 健康检查只在 `lancoai-shop.service`、`lancoai-workbench.service` 的单元文件存在时检查它们（运行状态和 `/healthz`），切换前保持现状。

## 测试与工具

`tests/conftest.py`、`tests/preview_http_server.py`、`tsconfig.json`、`vite.config.ts`（资源前缀不同）、`eslint.config.cjs`、`stylelint.config.cjs`、`package.json`/`package-lock.json`（依赖版本保持一致）、`pyproject.toml`、`pytest.ini`、`.github/workflows/backend-quality.yml`。

独立网站入口共享 `frontend/src/shared/serviceHref.ts`（商品 443、网络 8444、工作台 8445），保留旧链接和登录返回地址兼容；侧边栏使用本服务相对路径，不提供跨站菜单。
