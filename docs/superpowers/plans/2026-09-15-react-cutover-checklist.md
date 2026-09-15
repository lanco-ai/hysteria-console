# React panel cutover and rollback checklist

状态：迁移页面、隔离预览和生产 React 路由切换已完成；订阅及兼容路径继续由 legacy 服务提供。

## 当前已具备

- React/Vite 构建产物覆盖首页、登录/退出、管理员总览、日志、设置、
  流量分析、健康状态、事故处理、模板配置、路由规则、家宽出口，以及
  鉴权用户面板和改密页。
- FastAPI `/api/v1` 读写适配器、严格响应模型、生命周期拒绝、CSRF/请求
  大小/并发边界，以及隔离预览和浏览器合同测试。
- 预发布 ASGI 入口 `hysteria/react_server.py`、单 worker loopback systemd
  模板和 `requirements-web.txt` 的 Uvicorn 版本已固定；入口会优先读取受控的
  `panel/current` 发布指针，并在无指针时回退到源码 dist。`deploy.sh` 在显式
  设置 `HY_ENABLE_REACT_PANEL=1` 时会安装 web runtime、入口、`web_api` 模块、
  已校验的 dist 发布和 systemd 单元；构建目录可由 `HY_REACT_DIST_DIR` 指定，
  默认值 `0` 不改变 legacy 运行路径。
- 已准备未启用的双后端 Nginx 模板 `nginx/hysteria-panel-react.conf` 与
  `nginx/hysteria-panel-react-https.conf`：React 文档/API/构建资源指向 8083，
  订阅、二维码、legacy 下载/表单和未知路径保留 8081；路由清单合同测试会检查
  两个入口及 legacy 读取边界的一致性。
- 已增加 `scripts/hy2_panel_release.py`，可在临时根目录校验、安装并原子切换
  React `dist` 发布指针；它会拒绝符号链接、源码映射、越界路径和缺失的
  manifest 资源。`deploy.sh` 的显式 React 部署路径会调用它，并在失败恢复时
  清理本次新建的发布目录。
- 已增加 `scripts/hy2-react-cutover.sh`：默认仅报告状态，`apply`/`rollback`
  必须显式设置 `HY_REACT_CUTOVER_APPROVED=1`；它只替换面板 80/9444 vhost，
  先检查 8083 loopback，再以备份和 `nginx -t` 保护切换，并比较前后 443
  `listen` 指令以及 9444 的监听、`server_name`、证书路径，确保无关入口和
  TLS 身份不漂移。
- 旧 Codex 额度入口、接口和静态资源保持不可用；未改动代理配置、证书、
  域名或现有 443/9444 nginx 监听。

## 切换前阻塞项

以下事项完成前不得把 `/admin` 或 `/user/panel` 指向 React：

1. 生产运行时仍由 `systemd/hysteria-subscription.service` 启动
   `hysteria/subscription_service.py`（127.0.0.1:8081）。即使启用
   `HY_ENABLE_REACT_PANEL=1`，部署脚本也只会启动 8083 loopback ASGI 服务并保留
   active nginx vhost 为 legacy；公开路由切换仍需单独批准。
2. 需要在临时副本验证所有 legacy/React 方法、深链接、下载、订阅/二维码、
   证据、CSV、改密、登出及四种用户生命周期，并保存同一版本的资产与服务
   单元，才能形成可回滚发布包。
3. 需要取得明确的生产切换批准；本清单不执行 nginx、证书、systemd 或线上
   数据修改。

## 预发布验证

在与生产隔离的临时目录中运行：

```bash
PYTHON=/tmp/hy2-quality-venv/bin/python bash scripts/check-quality.sh
npm run check:frontend
PYTHONPATH=hysteria /tmp/hy2-quality-venv/bin/pytest -q \
  tests/test_react_deploy_wiring.py tests/test_react_cutover_script.py \
  tests/test_react_cutover_runtime.py tests/test_react_route_parity.py \
  tests/test_react_cutover_config.py tests/test_react_release.py
PYTHONPATH=hysteria /tmp/hy2-quality-venv/bin/python tests/run_react_browser.py
bash -n deploy.sh
```

记录构建产物的 git revision、manifest、文件哈希、systemd 单元、nginx
server_name/listen/proxy_pass，以及域名证书指纹。浏览器检查必须确认：

- `/admin`、`/user/panel`、`/` 的旧入口在切换前仍可用；
- 未知 React/API/静态资源、旧 Codex 路由不会 SPA fallback；
- 管理员 cookie 不能读取用户数据，用户 cookie 不能读取管理员 API；
- 443 与 9444 的现有监听、证书和域名没有漂移。

## 可逆发布步骤（已执行）

1. 备份精确的 nginx vhost、systemd 单元、旧静态目录和服务版本，生成带
   时间戳的发布目录；不复制凭证或持久化用户数据到仓库。
2. 将 FastAPI 服务、匹配的 React manifest/静态资产和路由配置作为一个版本
   原子安装；先启动新服务并在 loopback 上完成健康检查。
3. 只在健康检查和完整 smoke test 通过后，以
   `HY_REACT_CUTOVER_APPROVED=1 /usr/local/sbin/hy2-react-cutover.sh apply`
   切换对应 `proxy_pass`；保留
   `/api/v1` 非 SPA 边界和 legacy 兼容端点。
4. 记录 `/`, `/admin`, `/user/panel` 以及 443/9444 的状态码、证书指纹、
   响应头和关键浏览器流程。

## 最近一次预发布证据（2026-09-15）

- 后端完整套件：`2244 passed, 80 warnings`。
- React 浏览器矩阵：公共首页、登录/退出、改密、总览、流量分析、健康状态、
  事故处理、模板配置、路由规则、家宽出口和完整用户面板均通过。
- 前端完整质量命令（CSS/JS lint、门禁、传统浏览器矩阵、React 类型检查、构建、
  单元和 React 浏览器矩阵）通过；浏览器脚本使用包含 FastAPI 的质量虚拟环境。
- 路由/发布工具合同：23 项通过；切换工具、部署 wiring、恢复白名单和供应链
  合同：129 项通过；`scripts/check-quality.sh --lint-only`、
  `bash -n deploy.sh` 和 `git diff --check` 通过。
- 上述证据来自隔离预览和临时发布根目录；未验证线上 Nginx、域名证书、443/9444
  监听或真实回滚，因此不构成生产切换完成证明。

## 线上切换前只读基线（2026-09-15）

- `https://lancoai.site:9444/admin` 和 `https://lancoai.site/admin` 均返回
  `302 Location: /login`，当前入口仍由 legacy 服务提供。
- 9444 证书公开信息：`CN=lancoai.site`，有效期至 `2026-12-10`，SHA-256
  指纹为 `22:DA:8F:DD:06:D7:D1:CC:3D:45:F2:73:1C:81:89:57:E0:E1:2E:D8:90:98:29:BD:F9:22:FD:B5:85:EE:64:12`。
- 当前监听为 Nginx `443`、Nginx `9444`、legacy `127.0.0.1:8081`；未发现
  React `127.0.0.1:8083`。该基线只读采集，不包含私钥或凭证。

## 生产部署与切换证据（2026-09-15）

- 已在明确生产授权下启用 `hysteria-react.service`，服务为 enabled/active，
  loopback `127.0.0.1:8083` 根页返回 `200`；legacy `127.0.0.1:8081` 保持 active。
- React 构建发布指针为 `cf963e74769aa7b7dba8adf6`；部署事务和恢复日志由
  `/var/lib/hysteria/deploy-recovery` 保留，未复制凭证或持久化用户数据到仓库。
- 受保护切换于 `20260915T124931Z` 完成，备份目录为
  `/var/lib/hysteria/react-cutover/20260915T124931Z`，当前标记指向该备份。
- 线上 smoke test：`https://lancoai.site:9444/` 返回 `200`；`/admin` 和
  `/user/panel` 未登录返回 `303 Location: /login`；`/api/v1/admin/overview`
  返回 `401`；React 静态资产返回 `200`；`/sub/` 仍由 legacy 处理。
- Nginx 语法检查通过；443 监听指令未改变，9444 仍监听原端口和域名；公开证书
  `CN=lancoai.site`，有效期至 `2026-12-14`，指纹为
  `84:61:EC:CA:15:54:77:27:8D:81:99:C2:5F:2D:3F:F1:B1:96:86:7C:24:5B:B0:BF:D3:26:AF:69:6A:62:6B:95`。
- 已验证回滚所需的 root-only Nginx 备份、切换标记和 `rollback` 路径；本次生产
  未执行破坏性回滚，未验证在真实公网流量下的回滚时延。

## 回滚

如果任一 smoke test 失败：停止新 ASGI 单元，恢复同一备份中的 nginx
vhost、legacy 静态目录和 `hysteria-subscription.service` 单元，执行
`nginx -t && systemctl daemon-reload && systemctl reload nginx`，再验证
`127.0.0.1:8081/healthz`、443、9444 和订阅/二维码接口。回滚必须恢复匹配的
服务与资产，不得只替换单个 JS/CSS 文件。

## 批准记录

- 生产切换批准人：用户（本次会话明确授权）
- 计划窗口：2026-09-15（UTC）
- 发布 revision：`3246fb3` 基线 + 本次 React Nginx 渲染 wiring 修复（待提交）
- 回滚验证记录：隔离环境 apply→rollback 已通过；生产备份已创建并由当前标记引用，未执行线上回滚
