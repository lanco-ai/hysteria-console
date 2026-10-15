# LancoAI 工作台 + AI 服务（workbench-ai）

`lancoai.site` 拆分后的三个服务之一，负责：

- 今日计划、行程与日记（`/admin/plans`）
- AI 对话：工作区、项目、文档与知识库、工具（`/admin/chat`）
- AI 视频（`/admin/video`）与服务中心（`/admin/services`）
- 开源发现 · GitHub 热榜（公开页面 `/trending`）
- 为网络服务的 LancoAgent 解析自然语言意图（仅本机回环的内部接口）

另外两个服务在各自的分支：网络管理 `service/network`（登录与会话中心、用户与流量、Hysteria/Xray/TUIC、nginx、LancoAgent 的规则变更），商品 `service/shop`（门户首页、短信接码、商品管理）。整体设计见 [docs/service-split-design.md](docs/service-split-design.md)。

## 运行方式

| 项目 | 值 |
|---|---|
| 监听 | `127.0.0.1:8085`（只在本机回环，由 nginx 转发） |
| 入口 | `server/workbench_server.py`（`uvicorn workbench_server:app`） |
| systemd | `lancoai-workbench.service`（[deploy/lancoai-workbench.service](deploy/lancoai-workbench.service)） |
| 安装目录 | `/opt/lancoai/workbench`（Python 模块 + `learning_sandbox/` + `panel/releases/<id>` + `panel/current`） |
| 数据 | `/root/hysteria/state/` 下的 `plans/`、`journal/`、`chat/`、`video/`、`ai/`、`service-center.json`、`github-trending.json` |
| 配置 | `/etc/lancoai/workbench.env`（示例：[deploy/workbench.env.example](deploy/workbench.env.example)） |
| 内部令牌 | `/etc/lancoai/internal-token`（与网络服务共享，`root:hy2-xray 0640`） |
| 健康探针 | `GET http://127.0.0.1:8085/healthz` |

nginx 把这些路径转给本服务：`= /trending`、`= /admin/plans`、`= /admin/chat`、`= /admin/video`、`= /admin/services`、`^~ /api/plans`、`^~ /api/journal`、`^~ /api/chat/`、`^~ /api/video/`、`^~ /api/ai/`、`^~ /api/v1/admin/services`、`^~ /api/v1/github-trending`、`^~ /static/workbench/`。分流规则由网络服务分支维护，`/internal/` 不对外。

### 登录

本服务不保存任何密码或会话。后台请求把浏览器的 `sid`/`usid` cookie 转发给网络服务的 `GET /api/v1/session`（`server/web_api/services.py` 的 `RemoteSessionServices`），成功结果缓存 5 秒。网络服务不可用时后台返回 503，公开的 `/trending` 不受影响。

### LancoAgent 意图接口

`POST /internal/v1/agent-intent`（`server/web_api/agent_intent_routes.py`）只接受直接来自本机回环、且不带代理转发头的请求，并要求请求头 `X-Lancoai-Internal-Token` 与内部令牌一致；令牌文件缺失或过短时返回 503。它用 `state/chat/settings.json` 的模型配置把管理员的话解析成结构化意图，规则的校验与写入都在网络服务完成。

### 定时任务

`WORKBENCH_SCHEDULERS=1` 时运行 AI 视频队列和 GitHub 热榜刷新。默认关闭：切换上线前，旧的单体服务还在运行这两个任务。

## 开发与测试

```bash
npm ci
npm run build:react            # 产物在 frontend/dist，资源前缀 /static/workbench/
PYTHON=python3 bash scripts/check-quality.sh   # ruff + pytest
npm run check:frontend          # 前端 lint、类型检查、构建和浏览器用例
```

浏览器用例由 `tests/react_preview_server.py` 提供预览：它运行真实的工作台 + AI 接口（模型调用指向本地虚构网关），并用内存里的虚构会话代替网络服务的登录接口；预览禁止外部连接、服务命令和访问生产路径。

## 部署

```bash
npm ci && npm run build:react
sudo deploy/install.sh                      # 安装代码和前端 release，重启并等待 /healthz
sudo deploy/install.sh --enable-schedulers  # 仅在切换上线时
```

脚本在主机有 `hy2-lock-exec.py` 时会持有部署锁，与网络服务的部署互斥；它不会修改 nginx。知识库的向量模型和 Python 沙箱镜像分别用 `scripts/prepare-learning-model.py`、`scripts/prepare-learning-runtime.sh` 准备。

## 共享代码

部分基础代码与另外两个服务各有一份，修改时要同步，清单见 [docs/shared-code.md](docs/shared-code.md)。
