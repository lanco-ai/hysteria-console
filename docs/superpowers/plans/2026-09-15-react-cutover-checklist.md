# React panel cutover and rollback checklist

状态：迁移页面和隔离预览已完成；生产路由仍保持 legacy，尚未执行切换。

## 当前已具备

- React/Vite 构建产物覆盖首页、登录/退出、管理员总览、日志、设置、
  流量分析、健康状态、事故处理、模板配置、路由规则、家宽出口，以及
  鉴权用户面板和改密页。
- FastAPI `/api/v1` 读写适配器、严格响应模型、生命周期拒绝、CSRF/请求
  大小/并发边界，以及隔离预览和浏览器合同测试。
- 预发布 ASGI 入口 `hysteria/react_server.py`、单 worker loopback systemd
  模板和 `requirements-web.txt` 的 Uvicorn 版本已固定；这些文件尚未由部署
  脚本安装或启用。
- 已准备未启用的双后端 Nginx 模板 `nginx/hysteria-panel-react.conf` 与
  `nginx/hysteria-panel-react-https.conf`：React 文档/API/构建资源指向 8083，
  订阅、二维码、legacy 下载/表单和未知路径保留 8081；路由清单合同测试会检查
  两个入口及 legacy 读取边界的一致性。
- 旧 Codex 额度入口、接口和静态资源保持不可用；未改动代理配置、证书、
  域名或现有 443/9444 nginx 监听。

## 切换前阻塞项

以下事项完成前不得把 `/admin` 或 `/user/panel` 指向 React：

1. 生产运行时仍由 `systemd/hysteria-subscription.service` 启动
   `hysteria/subscription_service.py`（127.0.0.1:8081）。ASGI 入口和单元目前
   只作为预发布模板存在，`deploy.sh` 尚未安装 web 运行环境、入口或单元，
   也没有启用 8083 loopback 服务。
2. `deploy.sh` 尚未安装 `hysteria/web_api/`、`frontend/dist` 或配套静态
   资源，因此直接执行部署不会带上新面板。
3. 需要在临时副本验证所有 legacy/React 方法、深链接、下载、订阅/二维码、
   证据、CSV、改密、登出及四种用户生命周期，并保存同一版本的资产与服务
   单元，才能形成可回滚发布包。
4. 需要取得明确的生产切换批准；本清单不执行 nginx、证书、systemd 或线上
   数据修改。

## 预发布验证

在与生产隔离的临时目录中运行：

```bash
PYTHON=/tmp/hy2-quality-venv/bin/python bash scripts/check-quality.sh
npm run check:frontend
PYTHONPATH=hysteria /tmp/hy2-quality-venv/bin/pytest -q \
  tests/test_react_route_parity.py tests/test_react_cutover_config.py
PYTHONPATH=hysteria /tmp/hy2-quality-venv/bin/python tests/run_react_browser.py
bash -n deploy.sh
```

记录构建产物的 git revision、manifest、文件哈希、systemd 单元、nginx
server_name/listen/proxy_pass，以及域名证书指纹。浏览器检查必须确认：

- `/admin`、`/user/panel`、`/` 的旧入口在切换前仍可用；
- 未知 React/API/静态资源、旧 Codex 路由不会 SPA fallback；
- 管理员 cookie 不能读取用户数据，用户 cookie 不能读取管理员 API；
- 443 与 9444 的现有监听、证书和域名没有漂移。

## 可逆发布步骤（待批准）

1. 备份精确的 nginx vhost、systemd 单元、旧静态目录和服务版本，生成带
   时间戳的发布目录；不复制凭证或持久化用户数据到仓库。
2. 将 FastAPI 服务、匹配的 React manifest/静态资产和路由配置作为一个版本
   原子安装；先启动新服务并在 loopback 上完成健康检查。
3. 只在健康检查和完整 smoke test 通过后切换对应 `proxy_pass`；保留
   `/api/v1` 非 SPA 边界和 legacy 兼容端点。
4. 记录 `/`, `/admin`, `/user/panel` 以及 443/9444 的状态码、证书指纹、
   响应头和关键浏览器流程。

## 回滚

如果任一 smoke test 失败：停止新 ASGI 单元，恢复同一备份中的 nginx
vhost、legacy 静态目录和 `hysteria-subscription.service` 单元，执行
`nginx -t && systemctl daemon-reload && systemctl reload nginx`，再验证
`127.0.0.1:8081/healthz`、443、9444 和订阅/二维码接口。回滚必须恢复匹配的
服务与资产，不得只替换单个 JS/CSS 文件。

## 批准记录

- 生产切换批准人：待确认
- 计划窗口：待确认
- 发布 revision：待填写
- 回滚验证记录：待填写
