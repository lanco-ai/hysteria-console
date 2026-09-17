# 统一管理布局、聊天入口与安全检查方案

> **状态：已按方案实施并完成线上回归；保留回滚备份。**
> 本轮按 executing-plans 分阶段执行；未模拟或冒称其他模型/Agent。

**目标：** 所有“去网站 / AI 对话”入口进入 `/admin/chat`，聊天和管理页面维持相同外框，同时补齐已发现的认证、传输与路由缺口。

**架构：** 保留 React、TypeScript、Vite、FastAPI 与现有网络业务服务。使用一个稳定的管理 Shell、一个管理员会话门控；聊天与管理表格只替换主内容区。保留后端独立权限检查，不能用前端隐藏代替鉴权。

**设计依据：** 本轮用户截图及指示优先于 `../specs/2026-09-16-codex-app-shell-refactor-design.md` 中“根路径直接显示完整聊天背景”的旧设计。根路径现统一跳转 `/admin/chat`。

**检查时间：** 2026-09-16 America/Los_Angeles（服务器 UTC 为 2026-09-17）。检查基线提交 `e1cbc87`。

## 1. 当前问题及证据

| 项目 | 核查结果 | 影响 |
| --- | --- | --- |
| 外框样式 | 浏览器 1440px 下，聊天侧栏 240px、system-ui；切至配置页变成 224px、Inter | 侧栏、字体、背景、间距随路由变化 |
| 布局来源 | `main.tsx` 仅聊天等入口添加 `page-workbench`；`19-workbench.css` 覆盖公共 Shell 样式 | 共用 React 组件并不等于共用视觉规格 |
| 导航位置 | `ChatPage` 向 `CodexShell.sidebarTop` 插入聊天历史，其他管理页没有 | 管理菜单随页面上下移动 |
| 初始文档 | 后端 `/admin/chat` body 为 `has-shell`，前端再补 `page-workbench` | 刷新与客户端导航存在样式初始化差异 |
| 消息排版 | `ChatMessage.tsx` 只按三个反引号拆代码，其余文本直接展示 | 粗体、列表等 Markdown 未真正渲染 |
| 模型恢复 | 模型保存在组件 state；本地会话结构没有模型字段 | 返回聊天后可能看到“尚未选择模型” |
| 兼容路由 | `/auth` 在 React 和 FastAPI 声明中存在，但当前公网 GET 返回 404 | Nginx 文档路由尚未覆盖完整 |
| 测试缺口 | 原视觉测试主要覆盖工作台本身，未比较工作台与各管理页外框几何及字体 | 测试通过仍可能发生切页视觉变化 |
| 磁盘 | 根分区 9.3G，8.5G 已用，可用约 835M，92% | 构建、版本备份与日志增长空间偏紧 |

已检查的页面中 `.app` 数量为 1；没有证据说明是两个 Shell 嵌套导致的。应修正样式归属与菜单布局，避免误把全部业务页面重写。

## 2. 不输入密码能否访问

### 公网 URL 匿名实测

请求使用空 Cookie、不跟随重定向；不读取真实用户会话，不进行密码猜测，不触发真实模型生成。

| 检查对象 | 实测结果 |
| --- | --- |
| `/` | 302 到 `https://lancoai.site/admin/chat`；随后匿名跳转登录 |
| `/login`、`/auth` | 200，公开的统一登录工作台 |
| `/admin/chat` 及总览、配置、设置、规则、日志、用量、健康、事故、出口、用户详情页面 | 303 到登录入口，带安全的 next 路径 |
| `/user/panel` | 303 到登录入口 |
| `/api/v1/session`、14 个管理读取 API | 401 |
| 聊天设置 GET、模型 GET、补全 POST、连接测试 POST、设置 PUT | 401；设置写探针使用无效 JSON，避免任何实际写入 |
| 聊天跨站 POST | 403 |
| 伪造 `sid`、无效管理员 token | 未获授权；分别 401 / 跳转登录 |
| `/.env`、`/.git/config`、`/state/chat/settings.json`、`/openapi.json`、`/docs` | 404 |
| 9444 的聊天文档、设置与模型 API | 同样要求登录 |
| 真实无痕浏览器打开首页并切到配置页 | 登录框存在、聊天输入禁用、未发起 `/api/chat/*` 请求 |

**结论：没有发现“普通陌生人不带凭据即可进入管理数据或调用聊天”的情况。** 看得到公开页面外观不等于已获得管理员权限。

以下免输密码情况确实存在：

1. 浏览器已有有效登录 Cookie：属于正常会话复用。
2. 旧版非 React 兼容接口可能仍保留订阅/维护 token 语义；React 管理文档已停用 `?token=` 管理员会话交换，带 token 的管理 URL 仍跳转登录。用户订阅 token 与管理员登录凭据分开处理。
3. 本机聊天历史持久保存在 localStorage；匿名页面目前不读取，但退出登录不会清除磁盘上的历史。与他人共用同一浏览器配置文件时，应将它视为本地隐私风险，而非公网匿名 API 漏洞。

React 管理入口现在只接受账号密码建立的有效管理员会话；不要误删用户订阅 token 或网络认证凭据。旧版兼容接口仍需在独立下线计划中逐项迁移，避免影响订阅下载。

## 3. 安全与框架待处理项

### P1：先收紧明文入口

- `http://lancoai.site/login` 现返回 308 到 HTTPS；ACME challenge 仍保留明文入口，其余页面和登录/API 写入口均在认证前转到 HTTPS。
- Antigravity 已收紧为 `127.0.0.1:8088`，公网管理入口仅经 `https://lancoai.site:9445/` 反代；`/v1/models` 无 Key 返回 401，`/health` 的账户统计已由 Nginx 隐藏。其管理员密码、API Key、JWT secret 均已配置；不能据此认定其全部管理 API 都已审计。
- 当前聊天后端已改用 `http://127.0.0.1:8088/v1`；远程管理使用受保护的 HTTPS 入口，8088 不再直连公网。
- 本轮没有从独立外网探测云防火墙，也没有自动关闭端口或修改凭据。

### P2：认证策略与前端隐私

- React 管理文档已停用管理员 token 登录，默认使用账号密码 + 有效会话；legacy 兼容接口的 token 迁移另行处理。
- 退出、过期和角色变化时清除内存中的敏感状态；聊天历史只保存在当前浏览器 origin 的管理员工作台，并提供清空入口。当前部署只有一个管理员身份，若未来支持多管理员，应先按账户命名空间隔离后再开放持久化。
- 443 和 9444 的 Cookie 可以共享，但 localStorage 按 origin 隔离。主入口统一到 443；迁移历史应由用户在旧入口明确导出/导入，不能假装服务器能读取两端浏览器数据。
- `settings.json` 和代理 `.env` 实测权限为 0600；浏览器设置响应已有 Key 掩码策略。继续保留，部署验收增加泄漏哨兵测试。
- 聊天 Markdown 禁用原始 HTML，限制链接协议；不得直接把模型输出插入 `innerHTML`。
- 保留现有 CSRF、Host 检查、Secure/HttpOnly/SameSite Cookie、限流和角色隔离；不通过移除鉴权来修复页面跳转。

### P2：路由与部署完整性

- 补齐 `/auth` 的 GET/HEAD 文档入口；显式保留认证服务 POST `/auth` 的原有语义，不做宽泛代理覆盖。
- 对齐前端路由、后端文档白名单、Nginx 精确转发，以及登录 next 白名单。
- 保留 8081 订阅/兼容服务、8082 网络认证与 8083 React/API 的当前边界。三个服务均在 loopback 监听，相关 systemd 服务为 active。
- 保持前一轮已修正的公网 origin 端口转发；验收 443 和 9444，不把内部监听端口当作浏览器端口。
- 后续磁盘清理单列变更，先确认旧 release/日志保留规则，不删除项目、运行版本或用户数据。

## 4. 设计方案比较

| 方案 | 特点 | 建议 |
| --- | --- | --- |
| A：固定管理导航 + 聊天内容区历史抽屉 | 页面外框不变，聊天成为正常管理页面，入口统一 | **推荐，最符合本轮诉求** |
| B：全站侧栏始终保留聊天历史 | 历史不会消失，但表格管理页仍被大量聊天控件占用 | 适合聊天绝对优先，不推荐此轮 |
| C：聊天另开独立站点/页面体系 | 可独立设计，但切换仍有明显割裂 | 不解决本次问题 |

### 推荐方案 A 的交互与布局

- 所有“去网站 / AI 对话”链接目标均为 `https://lancoai.site/admin/chat`，站内使用 `/admin/chat` 相对链接。本仓库当前未找到“去网站”这一按钮文案；若按钮在外部启动器或其他项目，实施时先定位它的源码，不虚构已修改。
- `/` 作为入口引导到 `/admin/chat`。未登录显示同一设计语言的简洁登录界面，登录后回到原目标；已登录直接进入聊天。
- 左侧管理导航固定 240px；折叠后 64px。品牌、菜单顺序、底部设置/退出、字体、选中状态、背景不随路由改变。
- 顶栏固定 60px。只替换页面标题与该页面操作，不切换整套字体或配色。
- 聊天历史从主导航移出，改为内容区的可收起历史面板；手机使用抽屉。新对话、历史、搜索属于聊天页面，不再把全站菜单向下挤。
- 对话线程与输入框共享内容中心线，阅读宽度约 880px；输入框贴内容区底部，只有消息区滚动。聊天与管理表格允许不同内容宽度，但全站外框一致。
- 沿用现有浅暖灰背景、深色文字与低饱和选中态，统一到公共设计变量，不再为某个 route 硬编码第二套全站颜色。
- 会话保留模型和推理级别；返回聊天恢复草稿/选择。已有模型不再可用时明确提示，不静默替换模型。
- Markdown 支持段落、列表、粗体、链接与代码块；模型容量不足、未选模型、登录过期分别提供可操作提示，不只显示 502。
- 同一会话退出或认证失效后立即阻止请求、移除内存历史；普通用户面板维持原权限，不能误获得管理员 Chat。

## 5. 实施顺序与验收

### 阶段 1：入口与认证策略

涉及：`nginx/hysteria-panel*.conf`、`frontend/src/main.tsx`、`hysteria/web_api/document_routes.py`、登录返回路径处理；token 策略涉及 `services.py` 和 legacy 管理入口。

- [x] 先固定匿名、用户、管理员、有效会话、无效 token 的路由期望矩阵。
- [x] 处理 HTTP→HTTPS 和代理访问路径，并停用 React 管理文档的管理员 token 免密码交换。
- [x] 统一 `/`、`/admin/chat`、登录 next 与 `/auth` 兼容规则。
- [x] 验收：无会话不能读管理数据、改配置或调用模型；用户角色不能进入管理员 Chat；外站 next 被拒绝；网络认证/订阅行为未回归。

### 阶段 2：统一外框

涉及：`frontend/src/shared/CodexShell.tsx`、`AdminShell.tsx`、`navigation.ts`、`main.tsx`、样式 `02-tokens-base.css`、`06-shell.css`、`13-responsive.css`、`19-workbench.css`。

- [x] 将字体、背景、侧栏宽度、顶栏高度归到公共变量与 Shell 规则。
- [x] 删除聊天 route 对全局 Shell 的重复覆盖；前后端 body class 初始化一致。
- [x] 固定导航结构，避免页面切换时插入/撤出不同高度内容。
- [x] 验收：浏览器聊天、配置、总览、日志、规则、健康等页面回归通过；桌面与移动布局测试通过，只有一个 Shell。

### 阶段 3：聊天内容体验

涉及：`ChatPage.tsx`、`ChatSidebar.tsx`、`ChatMessage.tsx`、`chatApi.ts`、`18-chat.css`，必要时新增局部 history/state 模块。

- [x] 把历史列表移入聊天内容区的可收起面板，保证消息/输入中心线一致。
- [x] 补齐 Markdown 安全渲染、会话模型和草稿恢复，以及区分认证失败/上游容量错误的文案。
- [x] 明确持久历史策略：继续使用当前站点的浏览器 `localStorage` 保存管理员聊天记录和用量；本次没有改 key 或清空旧记录，刷新回归已验证可恢复，因此不需要执行未验证的数据迁移。
- [x] 验收：聊天浏览器回归通过；Markdown 未使用 `innerHTML`，危险链接被当作文本；发送失败不会追加伪造回复。

### 阶段 4：跨页面验收及上线

涉及：现有 `tests/workbench_visual.cjs`、`react_chat_browser.cjs`、工作台/鉴权/路由测试及部署检查。

- [x] 增加跨页面外框、路由和响应式浏览器回归；测试不能只断言组件存在。
- [x] 验证直接访问、站内点击、浏览器前进后退、刷新、登录与退出，以及不同角色。
- [x] 在隔离预览使用虚构账号和数据执行管理写入回归，线上仅做安全读取与无副作用探针。
- [x] 检查磁盘余量，构建可审阅版本，备份受影响配置与当前 release，保留回滚指针。
- [x] 发布后复查所有入口及鉴权矩阵；当前 release 为 `d303dac3e386869bbc8ee853`。

**本次回滚点：** 配置与后端备份位于 `/var/backups/hy2-shell-security-20260917T014411Z/`，上一版前端 release 为 `0cffe85c1d515c2d3a1d4a1f`。需要回滚时执行：

```bash
ln -sfn releases/0cffe85c1d515c2d3a1d4a1f /root/hysteria/panel/current
cp /var/backups/hy2-shell-security-20260917T014411Z/hysteria-panel-https.conf /etc/nginx/sites-available/hysteria-panel-https.conf
cp /var/backups/hy2-shell-security-20260917T014411Z/document_routes.py /root/hysteria/web_api/document_routes.py
cp /var/backups/hy2-shell-security-20260917T014411Z/chat_routes.py /root/hysteria/web_api/chat_routes.py
nginx -t && systemctl restart hysteria-react.service && systemctl reload nginx
```

## 6. 本轮已完成验证

- 第一组：工作台/聊天/会话/授权/路由及订阅安全回归 **86 passed**。
- 第二组：React 文档保护、认证并发与后端加固回归 **81 passed**；本轮新增聊天容量错误、配置模板和 Antigravity 反代检查后，相关集合 **172 passed**，补充模板/部署测试 **47 passed**。
- 浏览器全套 React 页面（日志、首页、登录、退出、密码、总览、用量、健康、事故、配置、规则、聊天、出口、用户面板）全部通过。
- `react_chat_browser.cjs` 在隔离虚构预览中通过。
- `npm run typecheck:react` 通过。
- 公网域名 URL 匿名检查、伪造凭据、跨站请求检查已完成；仅从本服务器及其隔离浏览器发出请求。
- 无痕访问没有发送聊天 API 请求；公网根路径已切换到统一聊天入口。

这是一轮有边界的配置、代码和功能核查，不是“网站绝对安全”的证明。未穷尽所有业务写操作、第三方代理管理端点、依赖漏洞、云侧 ACL 或所有移动浏览器；本轮未使用真实管理员密码、有效 token 或现有用户 Cookie 做登录测试。

参考安全原则：[OWASP Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)、[OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)。认证页面需要安全传输，Bearer token 与会话凭据均应作为秘密保护。
