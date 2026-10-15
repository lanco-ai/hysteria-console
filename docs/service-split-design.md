# LancoAI 拆分设计：网络管理 / 商品 / 工作台 + AI

状态：三服务已上线。2026-10-15 用户进一步指定三个独立网站入口：商品 `https://lancoai.site/`，网络管理 `https://lancoai.site:8444/`，工作台 + AI `https://lancoai.site:8445/`。下述原“网址不变”方案由本段更新；后台回环端口仍为 8083/8084/8085。

页面 GET/HEAD 会转到各自的 HTTPS 入口；网络入口根路径进入用户管理，工作台入口根路径进入今日计划。三个入口均在本 origin 提供登录、会话与跨服务组件所需的 API，使用已有的同主机 cookie；不放宽 CORS 或 CSRF 校验。原订阅地址和 API 继续兼容。三份 `frontend/src/shared/serviceHref.ts` 必须同步，侧栏和登录后导航使用它选择入口。外部业务端口 8444/8445 由 nginx 提供 TLS，Python 服务继续只绑定回环。

拆分基线：`main` `dd6be5a`。

## 1. 背景

拆分前，`lancoai.site` 由一个进程提供：`hysteria-react.service`（uvicorn，`127.0.0.1:8083`，入口 `hysteria/react_server.py`）。
`web_api.create_app()` 一次注册约 20 组路由，所有功能共用：

- 一个管理员登录会话（`sid` cookie → `state/panel_sessions.json`）；
- 一个并发池（32 个请求，满了返回 503 `server_busy`）；
- 一个生命周期：商品源刷新、AI 视频、GitHub 热榜三个后台任务都在这个进程里。

结果是任何一块出问题（例如商品源抓取卡住、AI 视频占满并发）都会拖累网络管理和 Hysteria 认证，而且任何改动都要整体重启。

## 2. 已确认的决定

| 问题 | 决定 |
|---|---|
| 拆分程度 | 三个独立服务：各自的进程、端口、systemd 单元，可单独部署和重启 |
| 分支组织 | 每个服务一个长期分支，只保留本服务代码：`service/network`、`service/shop`、`service/workbench-ai` |
| 开源发现（GitHub 热榜） | 归工作台 + AI 服务 |
| 网址 | 商品使用主域名；网络管理使用 HTTPS 8444；工作台 + AI 使用 HTTPS 8445。旧页面地址跳转到对应入口，API 和订阅继续兼容 |
| 登录 | 登录一次三处通用，网络服务是唯一的登录和会话中心 |

## 3. 三个服务

| 服务 | 分支 | 监听 | systemd 单元 | 负责的功能 |
|---|---|---|---|---|
| 网络管理 network | `service/network` | `127.0.0.1:8083`（不变） | `hysteria-react.service`（名字不变） | 用户、流量分析、路由与出口、运维、设置与告警、登录/登出/改密、用户面板、订阅链接、Hysteria 认证（`POST /auth`）、LancoAgent 智能助手；以及整台服务器的代理基础设施（Hysteria、Xray、TUIC、流量限额、备份、健康检查、`deploy.sh`） |
| 商品 shop | `service/shop` | `127.0.0.1:8084` | `lancoai-shop.service` | 公开门户首页（购物）、商品详情与购买、短信接码、商品管理后台、商品源定时刷新 |
| 工作台 + AI workbench-ai | `service/workbench-ai` | `127.0.0.1:8085` | `lancoai-workbench.service` | 今日计划/行程/日记、AI 对话（工作区、文档、知识库、工具）、AI 视频、服务中心、开源发现（GitHub 热榜）；AI 视频和热榜的定时任务 |

`8083` 和单元名保持不变，是因为 Hysteria 的认证地址 `http://127.0.0.1:8083/auth`、健康检查、部署恢复脚本都依赖它们。

## 4. 网址分流（nginx：443 → 9444、8444、8445）

页面入口通过 server 层的 GET/HEAD 跳转选择网站；POST 和数据下载不跳转。下表的上游分流仍按 location 的“精确匹配 / 前缀匹配”处理。现有的每客户端限速（10 r/s，突发 40）三个入口都保留。

| 转发到 | 路径 |
|---|---|
| 商品 `:8084` | `= /`（首页，含 `/?view=sms` 短信接码）、`= /admin/shop`、`^~ /api/v1/shop/`、`^~ /static/shop/` |
| 工作台 + AI `:8085` | `= /trending`、`= /admin/plans`、`= /admin/chat`、`= /admin/video`、`= /admin/services`、`^~ /api/plans`、`^~ /api/journal`、`^~ /api/chat/`、`^~ /api/video/`、`^~ /api/ai/`、`^~ /api/v1/admin/services`、`^~ /api/v1/github-trending`、`^~ /static/workbench/` |
| 网络管理 `:8083` | 其余全部（默认 `location /`）：`/admin`、`/admin/usage`、`/admin/config`、`/admin/health`、`/admin/settings` 等、`/login`、`/logout`、`/user/*`、`/sub/*`、`/panel/*`、其余 `/api/v1/*`、`/static/react/` |
| 不对外 | `^~ /internal/` 一律 404，只允许本机回环访问 |

旧地址兼容：`/?view=trending`、`/?view=chat`、`/?view=video` 由商品服务分别跳转到 `/trending`、`/admin/chat`、`/admin/video`；`/admin/github-trending` 由网络服务跳转到 `/trending`。

## 5. 登录与会话

- 网络服务是唯一持有管理员密码哈希（`subscription_meta.json`）和会话文件（`state/panel_sessions.json`）的服务。`/login`、`/logout`、`/api/v1/login`、`/api/v1/logout`、`/api/v1/session` 都不变。
- 商品和工作台 + AI 用 `RemoteSessionServices` 代替原来的 `LegacyPanelServices`。它提供同名的 `read_session(headers=, path=)`，把浏览器的 `Cookie` 头转发给 `http://127.0.0.1:8083/api/v1/session`：
  - `200 {"role": "admin"}` → 管理员；`200 {"role": "user", ...}` → 普通用户；
  - `401` → `LoginRequired`；`403` → `UserAccessDenied`；超时、连接失败或 `5xx` → `StateUnavailable`（503）；
  - 超时 2 秒；按 `sid` 的摘要缓存 5 秒，减少回环请求；不缓存失败结果；
  - 不跟随跳转、不读取代理环境变量、响应体限制 4 KB。
- 三个服务在同一个域名下，浏览器会把同一个 `sid` cookie 发给它们，所以登录一次三处通用。同源校验（`http_utils.is_same_origin_post`）不变。
- 商品和工作台 + AI 的后台页面未登录时跳转到 `/login?next=<原地址>`。
- 网络服务停机时：商品和工作台的后台无法登录（返回 503），公开商品页不受影响。

## 6. 数据归属

第一阶段不移动任何数据文件，沿用现有路径（`/root/hysteria/state/...`），每个服务只读写自己的那部分，systemd 单元也只给本服务的数据目录写权限。以后迁移到 `/var/lib/lancoai/<服务>/` 时再把路径改成可配置。

| 服务 | 数据 |
|---|---|
| 网络管理 | `users.json`、用量与在线快照、`subscription_meta.json`、`state/panel_sessions.json`、用户面板会话、凭据轮换与撤销队列、告警配置、出口与模板规则、LancoAgent 的变更记录 |
| 商品 | `state/shop/`（整个目录） |
| 工作台 + AI | `state/plans/`、`state/journal/`、`state/chat/`（含 `workspace.sqlite3`）、`state/video/`、`state/ai/`、`state/service-center.json`、`state/github-trending.json` |

备份脚本（`hy2-backup.sh`，网络分支维护）已经覆盖以上路径，第一阶段不用改。

## 7. 跨服务接口（只有两个）

1. **会话校验**：商品、工作台 + AI → 网络 `GET /api/v1/session`（见第 5 节）。
2. **智能助手意图解析**：网络 → 工作台 + AI `POST /internal/v1/agent-intent`。
   - LancoAgent 的规划、应用、撤销都留在网络服务，`users.json` 只有网络服务写。
   - 规划时要调用大模型把自然语言转成结构化意图，而模型配置（`state/chat/settings.json`）归工作台 + AI。所以网络服务把 `message`、`target_user`、`context` 发给这个内部接口，拿回意图 JSON，再自己校验并生成变更。
   - 认证：共享内部令牌文件 `/etc/lancoai/internal-token`（`root:hy2-xray 0640`），请求头 `X-Lancoai-Internal-Token`，用常量时间比较；只监听回环，nginx 对 `/internal/` 返回 404。
   - 工作台 + AI 不可用时，助手返回 `settings_unavailable`，其余网络功能不受影响。

## 8. 前端

- 每个分支一个独立的 Vite 项目（`frontend/`），只包含本服务的页面：
  - 网络管理：用户、流量分析、路由与出口、运维、设置、登录页、登出、改密、用户面板；资源前缀 `/static/react/`（不变）。
  - 商品：门户首页（购物）、商品详情与购买、短信接码、商品管理、登录弹窗；资源前缀 `/static/shop/`。
  - 工作台 + AI：今日计划、AI 对话、AI 视频、服务中心、开源发现；资源前缀 `/static/workbench/`。
- 外壳（侧边栏、顶栏）、导航、会话读取、请求工具、图标、样式在三个分支各复制一份。侧边栏菜单保持现在的样子，指向其他服务的链接是普通整页跳转；单页路由只拦截本服务自己的路径。
- 登录弹窗（门户里）直接调用网络服务的 `/api/v1/login`，同源，无需改动。
- 后台外壳里的两个跨服务组件在三个分支都保留，行为与拆分前一致：
  - 今日计划提醒（`PlanReminderCenter`）请求 `/api/plans/reminders`，由 nginx 转给工作台 + AI；该服务不可用时只显示“提醒暂时无法读取”。
  - LancoAgent 浮动助手请求 `/api/v1/admin/rules` 和 `/api/v1/admin/agent/*`，由 nginx 转给网络服务；网络服务不可用时助手报错，页面其他部分不受影响。

## 9. 部署

- **网络分支**沿用现有部署方式：`deploy.sh`、定向部署、`scripts/hy2_panel_release.py`、恢复脚本、备份、健康检查。变化：
  - 去掉商品和 AI 模块的部署清单（`REACT_WEB_API_MODULES` 等）；
  - nginx 配置加入第 4 节的分流规则；
  - 健康检查增加 `lancoai-shop`、`lancoai-workbench` 的运行状态和 `/healthz` 探针。
- **商品、工作台 + AI 分支**各自提供 `deploy/install.sh`：把代码装到 `/opt/lancoai/<服务>/`，用 `hy2_panel_release.py` 的同一格式发布前端（`/opt/lancoai/<服务>/panel/releases/<id>` + `current`），安装 systemd 单元，并检查 `/healthz`。
- **Python 环境**：服务器磁盘只比健康检查门槛多约 110 MB，所以第一阶段三个服务共用 `/root/hysteria/.venv-web`（102 MB）；扩容磁盘后再各自建虚拟环境。共用环境意味着升级依赖会同时影响三个服务，这是第一阶段已知的耦合。
- 三个服务都以 `root:hy2-xray` 运行，沿用现有文件权限。
- 商品和工作台 + AI 的配置分别在 `/etc/lancoai/shop.env`、`/etc/lancoai/workbench.env`（公开域名、会话地址、定时任务开关），安装脚本只在文件不存在时创建。

安装器先校验完整前端与 Python 文件，再在同一文件系统暂存候选目录。停止旧进程后统一切换后端、前端、环境和 systemd 单元；失败时恢复全部旧文件及原服务启用/运行状态，并检查旧服务健康。恢复失败则保留事务目录与 `.服务-install-pending` 标记，禁止盲目重试。`scripts/install_service.py` 和 `server/standby.py` 在商品、工作台分支保持同步。

## 10. 切换步骤（一次维护窗口，可回滚）

1. 预先部署商品和工作台 + AI（`8084`/`8085`），**待机模式**（两个服务均设置 `LANCOAI_STANDBY=1`，并保留 `SHOP_SCHEDULER=0`、`WORKBENCH_SCHEDULERS=0`）。此时只提供 `/healthz` 和静态资源，不创建业务存储、路由或后台任务；所有业务请求返回 503 `service_standby`。nginx 暂不指向它们，预装验证只检查待机探针、静态资源及业务请求被阻止。单独关闭 scheduler 不能保证无写入。
2. 维护窗口内：
   1. 停止单体 `hysteria-react`；
   2. 用网络分支的代码替换 `8083`（不再包含商品和 AI 代码、不再跑那两个定时任务），启动；
   3. 使用 `--enable-scheduler` / `--enable-schedulers` 同时设置 `LANCOAI_STANDBY=0`、开启定时任务并重启它们；
   4. 安装分流后的 nginx 配置，`nginx -t` 后重载；
   5. 逐项验证：首页、商品目录接口、商品管理、今日计划、AI 对话、开源发现、用户与流量页、订阅链接、Hysteria 认证、登录一次后三处都已登录。
3. 回滚：分别停止并禁用两个新服务，核对两者均为 inactive/dead 且 MainPID、ControlPID 为 0。任一停止或核对失败时禁止启动旧单体，保留恢复材料并报错；确认停止后再恢复环境文件、单体和 nginx。第一阶段没有移动数据，不回滚业务数据。

## 11. 分支与代码同步

- 三个分支都从 `main` `dd6be5a` 切出，第一笔提交是本设计文档，之后各自删除不属于自己的代码。
- 切换完成前，`main` 仍是线上的单体，日常修复照常进 `main`；切换前把 `main` 上的新提交按领域挑到对应分支（`git cherry-pick`）。
- 共享代码（安全中间件、请求并发池、`http_utils`、`state_store`、`timeutil`、前端外壳与样式）在三个分支各有一份。修共享代码时三个分支都要改，每个分支的 `docs/shared-code.md` 列出这些文件。

## 12. 测试与 CI

- 每个分支只保留本服务的测试，`.github/workflows/backend-quality.yml` 照常在每次推送时运行本分支的前后端检查。
- 新增测试：会话客户端（成功、401、403、超时、缓存）、内部令牌（缺失、错误、正确）、nginx 分流覆盖（每个 API 前缀只指向一个服务）、跨服务链接是整页跳转。

## 13. 资源与风险

- 内存：每个 uvicorn 进程常驻约 30–60 MB。1 核、约 900 MB 内存的主机可以承受多两个进程，但工作台 + AI 的文档索引和向量任务需要继续限制并发。
- 磁盘：新增代码约 2 × 15 MB，前端 release 每个约 5 MB；不新增虚拟环境。
- 网络服务是登录中心：它停机时另外两个服务的后台不可用（公开商品页正常）。
- 共享代码三份，修复需要同步三处（分支方式的代价，已确认接受）。

## 14. 不在本次范围

- 实际上线切换（需要单独批准和维护窗口）。
- 把数据迁移到 `/var/lib/lancoai/`。
- 拆分 Python 虚拟环境。
