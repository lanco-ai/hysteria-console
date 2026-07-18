<h1 align="center">hy2</h1>

<p align="center">
  <b>一键部署 Hysteria2 + Xray (VLESS&nbsp;Reality)，自带订阅与管理面板。</b>
</p>

<p align="center">
  <a href="#特性"><img alt="status" src="https://img.shields.io/badge/状态-就绪-4ade80?style=flat-square"></a>
  <a href="#一键部署"><img alt="platform" src="https://img.shields.io/badge/平台-Debian%20%7C%20Ubuntu-60a5fa?style=flat-square"></a>
  <img alt="license" src="https://img.shields.io/badge/license-MIT-d1d5db?style=flat-square">
  <a href="README.md"><img alt="lang" src="https://img.shields.io/badge/lang-English-f87171?style=flat-square"></a>
</p>

<p align="center">
  <a href="#特性">特性</a> ·
  <a href="#架构">架构</a> ·
  <a href="#一键部署">部署</a> ·
  <a href="#管理面板">面板</a> ·
  <a href="#配置">配置</a> ·
  <a href="#安全说明">安全</a>
</p>

---

## 特性

- ⚡  **Hysteria2** 监听 `:443/udp`，启用 Salamander 混淆 + UDP 端口跳跃 `20000-40000/udp`。
- 🛡️ **Xray VLESS + Reality** 主端口 `:443/tcp`，备用端口 `:8443/tcp`，伪装成 `www.bing.com`。
- 🎛️ **内置管理面板** —— 浏览器里创建用户、查看实时流量、维护订阅模板和路由规则；侧边栏布局，深色主题，移动端自适应。
- 📊 **每用户配额 + 设备数限制** —— 90 秒周期任务拉取 Hysteria + Xray 流量统计，超限自动踢人，按可配置账期自动重置（默认 12 号锚定、30 天周期）。
- 🔐 **持久 HTTP 鉴权** —— Hysteria 通过仅回环可达、固定容量的常驻服务认证，不再为每次连接冷启动 Python；应急 command CLI 仅兼容 token，绝不在独立进程里执行 PBKDF2。
- ⏱️ **Codex 额度面板** —— 复用服务器已有的 Codex 登录态，每 3 分钟采集 5 小时/周额度；提供重置倒计时、日/周/月/年趋势和节点悬停详情，不保存登录凭据或原始响应。
- 🔗 **每用户独立订阅 URL**，按请求渲染 Clash YAML，自动注入对应密码与 UUID。
- 🚀 **一键部署** —— 填好 `.env`，跑 `./deploy.sh`，一分钟内完成。

## 架构

```
        ┌──────────────────────────────────────────────┐
        │  客户端（Clash / sing-box / mihomo）          │
        └───────────────────┬──────────────────────────┘
                            │ 订阅 → http://host/sub/<user>?token=…
                            ▼
        ┌──────────────────────────────────────────────┐
        │  nginx :80   →   subscription_service.py     │
        │                  127.0.0.1:8081              │
        └───────┬────────────────┬─────────────────────┘
                │ /admin         │ /sub/<user>
                ▼                ▼ （渲染 template.yaml）
        ┌──────────────┐    ┌──────────────────────────┐
        │   面板 UI    │    │  users.json + usage.json │
        └──────────────┘    └──────────────────────────┘

                  数据面
        ┌──────────────────────────────────────────────┐
        │  hysteria2 :443/udp  +  端口跳跃 20000-40000  │
        │  xray vless+reality  :443/tcp 与 :8443/tcp   │
        └──────────────────────────────────────────────┘
                 │ 鉴权 POST（仅回环）
                 ▼
        ┌──────────────────────────────────────────────┐
        │ auth_service.py 127.0.0.1:8082 → 用户/状态   │
        └──────────────────────────────────────────────┘
```

## 一键部署

```bash
git clone https://github.com/lhzyyds666/hy2.git
cd hy2
cp .env.example .env
$EDITOR .env             # 按行内提示填好每一项
chmod 600 .env           # 部署会拒绝组内/全局可读的密钥文件
sudo ./deploy.sh
```

`deploy.sh` 做的事：

1. 安装固定摘要且与机器架构匹配的 Hysteria、Xray 与 TUIC 二进制。
2. 按字面值原子渲染 `.env` 到所有配置模板（不经过 shell/sed 替换语义）。
3. 文件分发到 `/root/hysteria/`、`/usr/local/etc/xray/`、`/etc/systemd/system/`。
4. 不存在则生成 Hysteria 用的自签名 TLS 证书。
5. `systemctl daemon-reload`，先通过回环鉴权服务的浅层存活检查，再启动 Hysteria 与其余 unit。
6. 安装 nginx 反向代理，并在提交部署事务前要求连续三轮深度全栈就绪观测。备份年龄属于部署后 timer 检查的运营健康信号，不作为新主机首次部署门槛。
7. 为完整静态产物集合维护仅 root 可读写的预写恢复日志。只有关键读写单元被权威确认停稳后才冻结快照；启动门与同一启动周期 watchdog 会在消费者获准启动前恢复被中断或遭 `SIGKILL` 的部署。

### `.env` 必填项

| 变量 | 生成方式 |
|---|---|
| `HY_SERVER_HOST` | 你的 VPS 公网 IP 或域名 |
| `HY_API_SECRET` | `openssl rand -hex 24` |
| `HY_OBFS_PASSWORD` | `openssl rand -base64 24 \| tr -d '/+='` |
| `XRAY_REALITY_PRIVATE_KEY` / `_PUBLIC_KEY` | `xray x25519` |
| `XRAY_REALITY_SHORT_ID` | `openssl rand -hex 8` |

初始 Xray 配置有意不创建独立的客户端 UUID。请始终在管理面板中创建用户，
确保 Xray/TUIC 的授权、配额限制与撤销都使用同一份用户数据。

### `.env` 可选项

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `HY_DISPLAY_MULTIPLIER` | `2.28` | 作用于 Hysteria/Xray/TUIC 原始流量计数的展示/计费倍率。请按服务商实际账单校准；`/admin/health` 在样本足够后会给出建议倍率。合法范围：`0.1`-`20.0`。 |
| `HY_HYSTERIA_VERSION` | `v2.9.3` | `deploy.sh` 安装并校验摘要的 Hysteria 固定版本。 |
| `HY_XRAY_VERSION` | `v26.6.27` | 从仓库固定校验值归档安装的 Xray 精确版本。 |
| `HY_ENABLE_HTTPS` | `1` | 仅在明确采用私网 HTTP 部署时设为 `0`。 |
| `HY_CERTBOT_EMAIL` | 空 | 可选的 Let's Encrypt 账户邮箱。 |
| `HY_HTTPS_PORT` | `9444` | nginx TLS 端口，不得与 Xray/TUIC 冲突。 |

## 管理面板

部署完成后：

- **管理后台** —— `https://<server>:9444/admin` —— 在 `/login` 登录。管理员密码**不是**通过首次访问的表单设置的。首次部署时请在 `subscription_meta.json` 写入明文 `admin_pass`，下次启动会自动迁移为加盐的 PBKDF2 `admin_pass_hash` 并删除明文；若未配置任何密码，则会随机生成一个，并写入仅 root 可读的文件 `admin_initial_password.txt`（与 `subscription_meta.json` 同目录，权限 0600），你可读取后登录、轮换，再删除该文件。随时可在 `/admin/settings` 轮换密码——轮换会注销其它所有会话，同时保持当前设备登录。
- **用户登录** —— `https://<server>:9444/user/login` —— 管理员可在新增或编辑用户时设置独立的“用户面板登录密码”。管理员设置的是临时密码，用户首次登录必须先在 `/user/change-password` 修改，之后也可随时从面板自行改密；改密会注销该用户在其他设备上的面板会话。密码与代理连接密码、订阅 Token 相互独立。未设置面板密码的旧用户仍可打开原 Token 面板链接，服务会立即把它换成可撤销 Cookie 会话并跳转到干净的 `/user/panel` 地址。
- **创建用户** —— 在面板里点一下，立即得到订阅链接 `https://<host>:9444/sub/<name>?token=<token>`。设备数上限填 `0` 表示不限制；若用户名已经存在，新增入口会拒绝覆盖，订阅泄露时应使用该用户行的“重置订阅”操作，以保留撤销与审计链路。
- **用户操作** —— 每个用户行可编辑套餐、设置到期日、添加加量包、保留运营备注、清/刷流量、**重置订阅令牌**（泄露时一键作废旧链接）、**暂停/启用**（不删号临时停用：拒绝新连接、移除 xray 入站并断开现有会话），以及删除。所有修改都携带打开页面时的用户版本；若另一位管理员已先修改同一用户，旧页面会收到 HTTP 409 而不会覆盖新数据，编辑冲突页会保留非敏感草稿但绝不回显密码。过期用户会被认证拒绝，并从 Xray/TUIC 静态用户计划里移除，续费后恢复。若 Token 已提交但 Xray/TUIC 同步失败，静态认证服务会 fail-closed 停止，后台显示可恢复警告，而不会用一个模糊的失败页掩盖已经发生的轮换。
- **用户面板** —— `https://<server>:9444/panel/<user>?token=<token>` —— 首次验证 Token 后跳转到不含 Token 的会话地址，提供单用户流量与设备统计、配额重置倒计时、近 30 天用量趋势、链接复制、多 profile 导出及按需二维码，并每 30 秒自动刷新用量（标签页隐藏时暂停）。自助轮换必须同时持有该干净面板的有效 `usid` 与隐藏幂等键；在修改 `users.json` 前，会先写入权限 0600、绑定原浏览器会话、有效期 5 分钟的恢复 receipt。这样即使进程崩溃或响应丢失，同一请求也只能取回同一枚新 Token，不会把凭据放进 URL、日志或异常。receipt 最多 256 条，到期后由后台主动擦除，且不进入备份。撤销副作用使用独立、最多 512 条未完成任务的持久队列：已提交的轮换与账号删除不会因墙钟时间被静默丢弃；只有过了重放窗口且旧代际仍是 canonical 的未提交轮换意图才会安全退出。Hysteria 通过不读取代理环境变量的 loopback 直连先立即 kick，再延迟二次 kick；静态停止或重载安排失败也会持久重试，界面严格区分“已确认暂停”与“尚未确认、正在重试”。若新会话无法保存，最终 `no-store` 恢复页使用 HTTP 200，并只在该恢复流程中显示当前新链接。
- **订阅 profile** —— `/sub/<user>?token=...` 可追加 `profile=game|work|lowdata|safe`。`game` 优先 UDP/低延迟，`work` 优先 TCP 与备用线路稳定性，`lowdata` 未命中规则的流量直连省流，`safe` 除局域网/私有地址外尽量全代理。不传 `profile` 时保持后台共享模板原样。
- **模板配置** —— 在线编辑全局 Clash YAML 模板（JSON 视图，带结构与语法校验、格式化、折叠/展开）；保存使用文件版本校验，陈旧草稿返回 HTTP 409 并保留输入。
- **路由规则** —— 增删改 proxy/direct/reject 规则，与模板实时同步；增删与整包保存同样使用版本校验，陈旧序号不会误删已移动的规则。
- **清零日志** —— 每一次流量清零的完整审计记录。
- **设置** —— `https://<server>:9444/admin/settings` —— 在线修改管理员密码（校验旧密码后写入新的 PBKDF2 哈希）。修改后会注销所有已有会话，并为当前设备重新签发会话 cookie。
- **健康状态** —— 6 张状态卡之外，提供「发送测试告警」按钮，验证 `alerts.json` 的 Telegram / webhook 是否配通；页面还会展示线路质量雷达，对比近期 Hysteria/Xray/TUIC 协议流量并推荐订阅 profile，并提供成本校准器，对比公网网卡计数与 App 原始流量后建议 `HY_DISPLAY_MULTIPLIER`。TUIC 流量是端口级总量计量，不参与单用户额度扣减。告警测试在后台线程发送（不阻塞请求）；webhook URL 由运营者自行配置（视为管理员级信任），不做 URL 白名单限制，请在接收端确认是否收到。
- **Codex 额度** —— `https://<server>:9444/admin/codex` —— 读取本机 Codex app-server 的账户额度，显示当前余量、重置倒计时与日/周/月/年趋势。采集器是每 3 分钟启动一次的 one-shot 进程，历史按 3 分钟、15 分钟、2 小时分层压缩；若上游没有提供某个额度窗口，面板会明确显示“未提供”而不是推算旧值。

管理总览与分析摘要每 30 秒更新；图表序列每 90 秒更新，与上游采集节奏一致，并仅在数据变化时重绘。标签页隐藏时会暂停轮询。

## 配置

### 端口分布

| 端口 | 服务 |
|---|---|
| `80/tcp` | nginx → 反向代理到 `127.0.0.1:8081`（面板 + 订阅） |
| `127.0.0.1:8082/tcp` | Hysteria 持久 HTTP 鉴权（绝不对公网暴露） |
| `443/tcp` | Xray —— VLESS + Reality |
| `443/udp` | Hysteria2 |
| `8443/tcp` | Xray —— VLESS + Reality（备用） |
| `9443/udp` | TUIC v5 |
| `9444/tcp` | nginx —— HTTPS 管理/订阅面板 |
| `20000-40000/udp` | iptables REDIRECT → `443/udp`（端口跳跃） |

### 备份

部署后可随时运行：

```bash
sudo /usr/local/sbin/hy2-backup.sh
```

备份包默认写到 `/root/hysteria/backups/`，权限 0600，并生成 `.sha256` 校验文件。内容包括用户、管理员元数据、订阅模板、告警配置、运行状态（不包含实时管理员和用户面板会话）、TLS/API 密钥、TUIC 与 Xray 配置。

可选加密：

```bash
sudo install -m 600 /dev/null /root/hysteria/backup.pass
sudo sh -c 'openssl rand -base64 32 > /root/hysteria/backup.pass'
sudo HY2_BACKUP_PASSPHRASE_FILE=/root/hysteria/backup.pass /usr/local/sbin/hy2-backup.sh
```

异机备份可使用 rclone 目标，脚本会拒绝上传明文归档。把
`HY2_BACKUP_PASSPHRASE_FILE` 和 `HY2_BACKUP_REMOTE` 写入仅 root 可读的
`/root/hysteria/backup.env`，并单独配置 rclone。未配置远端时仍正常保留每日本地备份。

也支持独立 Private Git 仓库。上传器会验证每份校验和，只保留
`*.tar.gz.enc` 与可移植 SHA-256 文件，保存最近 14 份，并强制推送为单提交
滚动快照，避免加密二进制的 Git 历史无限增长：

```bash
HY2_BACKUP_PASSPHRASE_FILE=/root/hysteria/backup.pass
HY2_BACKUP_GIT_REPO=https://github.com/OWNER/hy2-encrypted-backups.git
HY2_BACKUP_GIT_KEEP=14
```

解密口令必须保存在 GitHub 之外；定时器使用的 Git 凭据应仅能访问该私有备份仓库。

真正恢复前，先做 dry-run 检查：

```bash
sudo /usr/local/sbin/hy2-restore-check.sh /root/hysteria/backups/hy2-backup-YYYYMMDDTHHMMSSZ.tar.gz
sudo HY2_RESTORE_PASSPHRASE_FILE=/root/hysteria/backup.pass /usr/local/sbin/hy2-restore-check.sh /root/hysteria/backups/hy2-backup-YYYYMMDDTHHMMSSZ.tar.gz.enc
```

dry-run 会按需解密、校验归档路径、检查 JSON/YAML 是否可解析，并报告会覆盖多少现有运行态文件；它不会写回 `/root/hysteria`。

### 管理面板 HTTPS

脚本同时支持已解析域名和服务器公网 IPv4。域名使用普通 Let's Encrypt
证书；IPv4 使用约 6 天有效的公网可信短期证书，因此必须保持自动续期正常。
由于 Xray 已占用 TCP/443 与 TCP/8443，面板默认使用 TCP/9444。

```bash
sudo /usr/local/sbin/hy2-enable-https.sh panel.example.com you@example.com
sudo /usr/local/sbin/hy2-enable-https.sh 203.0.113.10 '' 9444
```

也可以在运行 `deploy.sh` 前设置 `HY_ENABLE_HTTPS=1`。脚本会安装新版
Certbot、保留 HTTP ACME 验证路径、把其他 HTTP 流量重定向到 HTTPS、安装
续期重载钩子并启用 snap 续期定时器。`HY_CERTBOT_EMAIL` 推荐填写但不是必填。

### 不进 git 的文件（已在 `.gitignore`）

这些是单机密钥或运行时状态，**绝不提交**：

- `.env` —— 真实密钥
- `server.crt`、`server.key` —— TLS 证书（`deploy.sh` 自动生成）
- `users.json` —— 用户名册（含密码哈希与订阅 token）
- `subscription_meta.json` —— 管理员密码哈希
- `state/` —— 流量计数、在线快照、清零日志
- `state/codex_quota.json` —— 脱敏后的 Codex 额度历史（最长 400 天、分层压缩）

## 安全说明

- 订阅服务**只**绑定 `127.0.0.1:8081`；nginx 在 `:80` 提供 ACME/跳转，在 `:9444` 提供加密面板。
- `hysteria-auth.service` **只**绑定 `127.0.0.1:8082`，严格校验并规范化 Hysteria HTTP 鉴权结构，并限制工作线程、待处理请求和过载响应。一个绝对截止时间覆盖慢请求头、请求体、策略计算以及在线 API 的每次 I/O。Token 保持常量时间快路径；PBKDF2 兼容密码只在该常驻服务内执行，并经过有界的单来源/全局速率限制和独立的非排队 CPU 并发槽。准备返回成功时会重读并终检授权代际，因此并发发生的 token/密码轮换、停用、过期或策略修改都会 fail closed。`/livez` 只检查进程；`/readyz` 必须直连实时在线 API，并在不占设备名额的前提下检查准入账本。20 秒内的新鲜快照只可辅助单次受限用户登录，不能让 readiness 变绿。Hysteria 仍与 auth 绑定生命周期：深度启动成功后写入仅 root 可读的运行意图；auth 导致的停止保留该意图，auth 健康时的手工停止则清除。恢复逻辑只会拉起“已启用且先前确实在运行”的服务，不会撤销管理员停机。凭据错误与被节流的密码尝试都按协议返回通用 HTTP 200 拒绝，服务不会把密钥或请求体写入日志；`auth_backend.py` 的应急 command CLI 只接受 `sub_token`，明确拒绝旧 PBKDF2 密码。
- 所有模板文件用 `__PLACEHOLDER__` 占位，**真实密钥只存在于 `.env` 和 `/root/hysteria/` 下渲染后的文件**，两者都已 gitignore。渲染器从环境而非 argv 读取值，按字面保留 `|`、`&` 和反斜杠，拒绝 NUL/多行值、未知或残留占位符，完成 fsync 后再原子替换目标文件。
- Hysteria 2.9.3、Xray 26.6.27 与 TUIC 1.0.0 都使用明确的 `amd64`/`arm64` 映射和仓库内固定 SHA-256。部署只在 owner、权限、链接数与摘要全部吻合后信任旧二进制，绝不会先以 root 执行未经验证的旧文件。
- Hysteria 管理 API 仅监听本地，由 `HY_API_SECRET` 鉴权——把它当 SSH key 看待。
- 管理员认证使用 PBKDF2-SHA256（20 万轮 + per-secret 盐），会话用 HttpOnly + `SameSite=Lax` cookie。
- 每用户的 `sub_token` 是 24 字节 URL-safe 随机串；轮换 token 即可让泄露的订阅链接立即失效，且不影响用户本身。
- 暴露到公网前请为管理/订阅面板配置可信 TLS 证书。可用 `/usr/local/sbin/hy2-enable-https.sh <domain-or-ip> [email] [port]`；内置自签证书只用于 Hysteria/TUIC 端点。

> **轮换记录** —— 早期某个 commit（位于 `e7d9d3a` 之前）曾把真实 `HY_API_SECRET` 硬编码进源码。该值已于 2026-04-30 在生产环境轮换并验证旧值返回 401 失效；保留在 `e7d9d3a` 之前 git 历史里的旧值已无法对任何端点鉴权成功。

## 项目结构

```
.
├── deploy.sh                       # 一键安装脚本
├── .env.example                    # 密钥模板（复制为 .env）
├── hysteria/
│   ├── config.yaml.tpl             # hysteria2 服务端配置
│   ├── auth_service.py             # 有界回环 HTTP 鉴权服务
│   ├── auth_backend.py             # 共享策略 + token-only command CLI
│   ├── subscription_service.py     # 管理面板 + /sub 渲染
│   ├── traffic_limiter.py          # 90 秒任务：流量统计 + 自动踢人
│   └── clash-default.yaml.tpl      # 订阅模板
├── xray/config.json.tpl            # vless+reality 配置
├── nginx/hysteria-panel.conf       # :80 反向代理
├── scripts/hysteria-porthop.sh     # iptables 端口跳跃脚本
└── systemd/                        # 各服务 unit 文件
```

## 告警（可选）

放一份 `/root/hysteria/alerts.json`（chmod 600）开启 Telegram / webhook 告警：

```json
{
  "telegram": {"bot_token": "...", "chat_id": "..."},
  "webhook":  {"url": "https://example.com/hook", "secret": "可选-hmac-key"},
  "anomaly_z_threshold": 3.0,
  "anomaly_min_bytes": 1073741824
}
```

- 配额跨过 80% / 100% → 每用户每月一次推送
- 当日相对最近 7 日均值 z-score > 阈值 → 每用户每日一次推送
- 到期提醒 → 用户进入 `expiry_warn_days`（默认 3 天）窗口时推送一次，过期后再推送一次
- webhook 带 `secret` 时附 `X-Hy2-Signature: sha256=<hmac>` 头
- 去重状态采用跨进程的「claim → 锁外投递 → CAS 完成」流程：并发 tick 对同一事件只投递一次，所有已配置通道成功后才提交去重；失败会立即释放重试，崩溃遗留的 claim 在 60 秒后过期。失败日志不会记录含凭证的机器人或 webhook URL。

文件不存在时告警通道静默关闭。基础设施健康状态见 `/admin/health`（cron 心跳 / hysteria / xray / 磁盘 / TLS 证书 / 在线用户 6 张卡，30 秒自动刷新）。

## 开发

本地跑测试：

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
```

## 许可

MIT —— 详见 `LICENSE`，若无文件则默认 MIT。

---

<p align="center"><sub>English docs → <a href="README.md">README.md</a></sub></p>
