# React 全前端迁移设计

## 目标

让 React/Vite 成为所有网页文档（公共首页、登录、管理员控制台、用户面板及深链接）的唯一前端运行时和样式资源所有者，同时保留订阅、面板交换、CSV、证据下载及现有后端 API 的兼容合同。

## 现状与约束

- React 19 + TypeScript + Vite 已覆盖 `/`、`/login`、`/logout`、`/user/*` 和 `/admin/*` 页面；路由由 `frontend/src/main.tsx` 的显式路径表处理。
- `frontend/index.html` 仍通过 `/static/style.css` 外链 `hysteria/admin.css`；Vite 构建目前只产生 JS，因此页面运行时仍依赖 legacy 静态服务。
- `hysteria/styles/*.css` 与 `hysteria/admin.css` 同时服务旧 Python HTML；`/sub/`、`/panel/`、CSV、证据和兼容 `.json` 端点不能因迁移页面而中断。
- 当前生产 Nginx 已把 React 文档和 `/api/v1/*` 指向 8083，兼容下载与旧 JSON 指向 8081；这个边界继续保留，迁移期间支持原子回滚。

## 方案

### 样式资源

以 `hysteria/styles/manifest.json` 为唯一 CSS 顺序来源，在 `frontend/src/styles/index.css` 中按 manifest 顺序导入现有分段。`main.tsx` 导入该入口，Vite 生成带 hash 的 CSS 并写入 `frontend/dist/assets`。React 文档不再声明 `/static/style.css`；`hysteria/admin.css` 和 `/static/style.css` 暂时保留为 legacy 兼容产物，直到旧文档路由完全退役。

### 页面与后端边界

- React 文档路由继续使用显式白名单和服务端鉴权；未知路径不回退为 SPA 页面。
- 旧 HTML 渲染函数和旧页面脚本不再由 React 页面加载。兼容服务只继续提供 `/sub/*`、`/panel/*`（含二维码、JSON 和 token 交换）、CSV、证据、旧 JSON 和其它已登记的非文档接口。
- `document_routes.py` 只负责注入标题、body class、public host 和密码长度等 bootstrap 数据，不再改写 React 样式 URL。
- 只有在路由对照表证明无客户端依赖后，才删除 legacy 文档渲染和静态脚本；本阶段不删除后端领域服务、模板存储或代理配置。

## 错误与回滚

- Vite 构建必须在 dist 中产生 CSS 资源；缺失或仍含 `/static/style.css` 的构建由静态门禁拒绝发布。
- React 静态资源通过 `/static/react/assets/` 的 immutable 路径提供，旧 CSS 继续由 8081 提供，避免兼容页面白屏。
- 部署先写入新 release，再原子更新 `panel/current`；浏览器 smoke 失败时恢复上一 release 指针，不修改证书、域名、443/9444 或订阅状态。

## 验收

1. `npm run typecheck:react`、`npm run build:react`、React 单元/浏览器检查通过。
2. 构建后的 `frontend/dist/index.html` 引用 `/static/react/assets/*.css`，不引用 `/static/style.css`、legacy JS 或内联旧页面脚本。
3. 所有 React 文档路径在未登录时按既有合同重定向 `/login`；登录后页面只请求 `/api/v1/*` 与 `/static/react/assets/*`。
4. `/sub/*`、`/panel/*`、CSV、证据和兼容 JSON 仍由旧服务响应，且现有后端测试通过。
5. 生产 smoke 验证首页、登录、管理员/用户深链接、React 资源、订阅下载及回滚指针。

## 非目标

本次不更换 React/Vite、FastAPI、Uvicorn 或 Nginx，不重写业务 API，不删除订阅/下载功能，不迁移代理配置，也不引入 CSS Modules、Tailwind 或新的组件库。
