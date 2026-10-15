# 与其他服务共享的代码

三个服务各在自己的分支（`service/network`、`service/shop`、`service/workbench-ai`），下面这些文件在各分支里各有一份，来源都是拆分前的 `main` `dd6be5a`。修改其中任何一个时，另外两个分支里的对应文件也要同步修改（`git cherry-pick` 或手动改）。

## 后端

| 本分支文件 | 说明 | 网络分支中的对应位置 |
|---|---|---|
| `server/http_utils.py` | 安全响应头、表单解析、同源校验 | `hysteria/http_utils.py` |
| `server/state_store.py` | 原子写入与文件锁 | `hysteria/state_store.py` |
| `server/timeutil.py` | `Asia/Shanghai` 时间桶 | `hysteria/timeutil.py` |
| `server/web_api/requests.py` | 请求头与表单读取 | `hysteria/web_api/requests.py` |
| `server/web_api/app.py` | 安全响应头中间件、并发池 `dispatch`、错误映射（只注册本服务路由） | `hysteria/web_api/app.py` |
| `server/web_api/document_routes.py` | 页面文档渲染与登录守卫（只含本服务页面） | `hysteria/web_api/document_routes.py` |
| `server/web_api/services.py` | `RemoteSessionServices`：向网络服务查询会话 | 工作台 + AI 分支同名文件；网络服务是会话的提供方 |
| `server/shop_server.py` | release 指针校验（`resolve_react_dist`） | `hysteria/react_server.py` |
| `scripts/hy2_panel_release.py` | 前端 release 安装与切换 | 同名文件 |

## 前端

| 本分支文件 | 说明 |
|---|---|
| `frontend/index.html` | 页面模板（标题、`body` 类、`data-public-host` 标记） |
| `frontend/src/shared/*` | 后台外壳、侧边栏导航、会话读取、请求工具、图标 |
| `frontend/src/styles/*` | 全部样式（门户和后台共用类名，暂未按服务裁剪） |
| `frontend/src/features/auth/LoginModal.tsx` 等 | 登录弹窗与登出（调用网络服务的 `/api/v1/login`、`/api/v1/logout`） |
| `frontend/src/features/plans/PlanReminderCenter.tsx` | 今日计划提醒（调用工作台 + AI 的 `/api/plans/reminders`） |
| `frontend/src/features/agent/*` | LancoAgent 浮动助手（调用网络服务的 `/api/v1/admin/rules`、`/api/v1/admin/agent/*`） |

侧边栏菜单（`frontend/src/shared/navigation.ts`）按服务独立维护，不能再同步成全站菜单。各站只展示自己的页面；商品后台侧栏仅提供商品管理和商城首页，短信接码保留在公开门户；商品门户的后台按钮进入商品管理，开源发现属于工作台。`CodexShell.tsx` / `PortalShell.tsx` 的菜单和组件挂载同样按服务裁剪，计划提醒只在工作台挂载。

## 测试与工具

`tests/conftest.py`、`tests/preview_http_server.py`、`tsconfig.json`、`vite.config.ts`（资源前缀不同）、`eslint.config.cjs`、`stylelint.config.cjs`、`package.json`/`package-lock.json`（依赖版本保持一致）、`pyproject.toml`、`pytest.ini`、`.github/workflows/backend-quality.yml`。

商品和工作台分支另共享 `scripts/install_service.py`（事务安装器）、`server/standby.py`（无业务处理器的预装待机入口），修改时须同步。

独立网站入口共享 `frontend/src/shared/serviceHref.ts`（商品 443、网络 8444、工作台 8445），保留旧链接和登录返回地址兼容；侧边栏使用本服务相对路径，不提供跨站菜单。
