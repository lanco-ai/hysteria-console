# LancoAI 商品服务（shop）

`lancoai.site` 拆分后的三个服务之一，负责：

- 公开门户首页（购物）、商品详情与购买、短信接码（`/`、`/?view=sms`）
- 商品管理后台（`/admin/shop`）
- 商品源定时刷新（16688、Anli、ProdSeller、Lorry、Fanai、短信接码）

另外两个服务在各自的分支：网络管理 `service/network`（登录与会话中心、用户与流量、Hysteria/Xray/TUIC、nginx），工作台 + AI `service/workbench-ai`（今日计划、AI 对话与视频、服务中心、开源发现）。整体设计见 [docs/service-split-design.md](docs/service-split-design.md)。

## 运行方式

| 项目 | 值 |
|---|---|
| 监听 | `127.0.0.1:8084`（只在本机回环，由 nginx 转发） |
| 入口 | `server/shop_server.py`（`uvicorn shop_server:app`） |
| systemd | `lancoai-shop.service`（[deploy/lancoai-shop.service](deploy/lancoai-shop.service)） |
| 安装目录 | `/opt/lancoai/shop`（Python 模块 + `panel/releases/<id>` + `panel/current`） |
| 数据 | `/root/hysteria/state/shop/`（只写这个目录） |
| 配置 | `/etc/lancoai/shop.env`（示例：[deploy/shop.env.example](deploy/shop.env.example)） |
| 健康探针 | `GET http://127.0.0.1:8084/healthz` |

nginx 把这些路径转给本服务：`= /`、`= /admin/shop`、`^~ /api/v1/shop/`、`^~ /static/shop/`。分流规则由网络服务分支维护。

### 登录

本服务不保存任何密码或会话。后台请求把浏览器的 `sid`/`usid` cookie 转发给网络服务的 `GET /api/v1/session`（`server/web_api/services.py` 的 `RemoteSessionServices`），成功结果缓存 5 秒。网络服务不可用时后台返回 503，公开商品页不受影响。

### 商品源刷新

`SHOP_SCHEDULER=1` 时每分钟刷新一次商品源。默认关闭：切换上线前，旧的单体服务还在刷新同一份数据，两个进程不能同时写。

## 开发与测试

```bash
npm ci
npm run build:react            # 产物在 frontend/dist，资源前缀 /static/shop/
PYTHON=python3 bash scripts/check-quality.sh   # ruff + pytest
npm run check:frontend          # 前端 lint、类型检查、构建和浏览器用例
```

浏览器用例由 `tests/react_preview_server.py` 提供预览：它运行真实的商品 API，并用内存里的虚构会话代替网络服务的登录接口。

## 部署

```bash
npm ci && npm run build:react
sudo deploy/install.sh                     # 安装代码和前端 release，重启并等待 /healthz
sudo deploy/install.sh --enable-scheduler  # 仅在切换上线时
```

脚本在主机有 `hy2-lock-exec.py` 时会持有部署锁，与网络服务的部署互斥；它不会修改 nginx。回滚商品存储模块见 [docs/shop-rollback.md](docs/shop-rollback.md)。

## 共享代码

部分基础代码与另外两个服务各有一份，修改时要同步，清单见 [docs/shared-code.md](docs/shared-code.md)。
