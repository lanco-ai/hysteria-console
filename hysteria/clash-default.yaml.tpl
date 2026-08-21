# 1. 基础全局配置
mixed-port: 7890
allow-lan: true
bind-address: '*'
mode: rule
log-level: info
external-controller: 127.0.0.1:9090
unified-delay: true

# 2. DNS 配置
# 目标：
#   - 中国大陆流量走 DIRECT（低延迟）
#   - 海外流量通过代理出口查询 DNS（防止 DNS 泄露）
#   - fake-ip 模式保留
#   - IPv6 关闭
dns:
  enable: true
  ipv6: false
  enhanced-mode: fake-ip
  fake-ip-range: 198.18.0.1/16
  # respect-rules: true → DNS 查询结果参与规则匹配时按代理策略出口
  # Clash Nyanpasu / Mihomo 支持
  # proxy-server-nameserver: 当 respect-rules 启用时，必须配置
  #   → 负责向 Clash Meta 内置 proxy-server 发出 DNS 请求的 upstream
  respect-rules: true
  proxy-server-nameserver:
    - https://doh.pub/dns-query
    - https://dns.alidns.com/dns-query
  # bootstrap: 解析 proxy-server-nameserver 自身的域名（如 doh.pub / dns.alidns.com）
  default-nameserver:
    - 223.5.5.5
    - 119.29.29.29

  # 默认 nameserver：统一使用海外 DoH（通过代理出口）
  # 当 domain 不匹配任何 nameserver-policy 时使用
  nameserver:
    - https://1.1.1.1/dns-query
    - https://dns.google/dns-query

  # nameserver-policy：
  #   - rule-set:direct → 国内 DoH（直连）
  #   - GitHub / OpenAI / Telegram → 海外 DoH（代理）
  #   - Steam 域名 → 国内 DoH（直连，国内 CDN 低延迟）
  nameserver-policy:
    # 国内域名用国内 DNS（直连，低延迟）
    'rule-set:direct':
      - https://doh.pub/dns-query
      - https://dns.alidns.com/dns-query

    # Steam 国内 CDN（直连，国内低延迟）
    '+.steamcontent.com':
      - https://doh.pub/dns-query
      - https://dns.alidns.com/dns-query
    '+.steamserver.net':
      - https://doh.pub/dns-query
      - https://dns.alidns.com/dns-query

    # GitHub 全系（必须海外解析才能正确路由）
    '+.github.com':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.githubusercontent.com':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.githubassets.com':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.github.io':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.githubapp.com':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.github.dev':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.ghcr.io':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.githubcopilot.com':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.github-cloud.s3.amazonaws.com':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query

    # OpenAI / ChatGPT
    '+.openai.com':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.chatgpt.com':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.oaistatic.com':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.oaiusercontent.com':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.openaiusercontent.com':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.ai.com':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.auth0.com':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.arkoselabs.com':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.statsigapi.net':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.featuregates.org':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query

    # Telegram
    '+.telegram.org':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.telegram.me':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.telegram.dog':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.t.me':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.telegra.ph':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query
    '+.tdesktop.com':
      - https://1.1.1.1/dns-query
      - https://dns.google/dns-query

  # fake-ip-filter（同前）
  fake-ip-filter:
    - "*.msftconnecttest.com"
    - "*.msftncsi.com"
    - "*.microsoft.com"
    - "*.microsoftonline.com"
    - "*.windows.com"
    - "*.windowsupdate.com"
    - "*.mp.microsoft.com"
    - "*.xboxlive.com"
    - "*.xboxservices.com"
    - "*.gamepass.com"
    - "*.sciencedirect.com"
    - "*.sciencedirectassets.com"
    - "*.els-cdn.com"
    - "*.elsevier.com"
    - "*.elsevier-ae.com"
    - "*.elsevier.io"
    - "*.scopus.com"
    - "*.springer.com"
    - "*.springernature.com"
    - "*.nature.com"
    - "*.wiley.com"
    - "*.tandfonline.com"
    - "*.jstor.org"
    - "*.ieee.org"
    - "*.acs.org"
    - "*.rsc.org"
    - "*.sagepub.com"
    - "*.science.org"
    - "*.cell.com"
    - "*.thelancet.com"
    - "*.bmj.com"
    - "*.oup.com"
    - "*.cambridge.org"
  use-hosts: true

# 3. 节点 (password 和 uuid 由 subscription_service.py 在下发订阅时按用户注入)
proxies:
  - name: 🇺🇸 美国 UDP (端口跳跃)
    type: hysteria2
    server: __HY_SERVER_HOST__
    port: 443
    ports: 20000-40000
    password: PLACEHOLDER
    obfs: salamander
    obfs-password: __HY_OBFS_PASSWORD__
    sni: hysteria2
    skip-cert-verify: true
    udp: true
    up: 100 Mbps
    down: 400 Mbps
    transport:
      type: udp
      hopInterval: 30s

  - name: 🇺🇸 美国 UDP TUIC
    type: tuic
    server: __HY_SERVER_HOST__
    port: 9443
    uuid: 00000000-0000-0000-0000-000000000000
    password: TUIC_PASSWORD_PLACEHOLDER
    alpn:
      - h3
    disable-sni: true
    reduce-rtt: true
    request-timeout: 8000
    udp-relay-mode: native
    congestion-controller: bbr
    skip-cert-verify: true
    udp: true

  - name: 🇺🇸 美国 TCP (VLESS+REALITY)
    type: vless
    server: __HY_SERVER_HOST__
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    network: tcp
    tls: true
    udp: false
    flow: xtls-rprx-vision
    reality-opts:
      public-key: __XRAY_REALITY_PUBLIC_KEY__
      short-id: __XRAY_REALITY_SHORT_ID__
    servername: www.bing.com
    client-fingerprint: chrome
    skip-cert-verify: true

  - name: 🇺🇸 美国 TCP 备用 (VLESS+REALITY)
    type: vless
    server: __HY_SERVER_HOST__
    port: 8443
    uuid: 00000000-0000-0000-0000-000000000000
    network: tcp
    tls: true
    udp: false
    flow: xtls-rprx-vision
    reality-opts:
      public-key: __XRAY_REALITY_PUBLIC_KEY__
      short-id: __XRAY_REALITY_SHORT_ID__
    servername: www.bing.com
    client-fingerprint: chrome
    skip-cert-verify: true

# 4. 策略组
proxy-groups:
  - name: 🚀 节点选择
    type: select
    proxies:
      - ⚡ GitHub 加速
      - 🤖 GPT 优化
      - 🌐 Google 优化
      - 📚 学术访问
      - ✈️ Telegram 优化
      - 🔄 自动选择
      - 🇺🇸 美国 UDP (端口跳跃)
      - 🇺🇸 美国 UDP TUIC
      - 🇺🇸 美国 TCP (VLESS+REALITY)
      - 🇺🇸 美国 TCP 备用 (VLESS+REALITY)
      - DIRECT

  - name: 🔄 自动选择
    type: fallback
    proxies:
      - 🇺🇸 美国 UDP (端口跳跃)
      - 🇺🇸 美国 UDP TUIC
      - 🇺🇸 美国 TCP (VLESS+REALITY)
      - 🇺🇸 美国 TCP 备用 (VLESS+REALITY)
    url: https://www.gstatic.com/generate_204
    interval: 30
    timeout: 5000

  - name: ⚡ GitHub 加速
    type: url-test
    proxies:
      - 🇺🇸 美国 UDP (端口跳跃)
      - 🇺🇸 美国 UDP TUIC
      - 🇺🇸 美国 TCP (VLESS+REALITY)
      - 🇺🇸 美国 TCP 备用 (VLESS+REALITY)
    url: https://github.com/favicon.ico
    interval: 120
    timeout: 5000
    tolerance: 100

  - name: 🤖 GPT 优化
    type: url-test
    proxies:
      - 🇺🇸 美国 UDP (端口跳跃)
      - 🇺🇸 美国 UDP TUIC
      - 🇺🇸 美国 TCP (VLESS+REALITY)
      - 🇺🇸 美国 TCP 备用 (VLESS+REALITY)
    url: https://chatgpt.com/cdn-cgi/trace
    interval: 60
    timeout: 5000
    tolerance: 100

  - name: 🌐 Google 优化
    type: fallback
    proxies:
      - 🇺🇸 美国 TCP (VLESS+REALITY)
      - 🇺🇸 美国 TCP 备用 (VLESS+REALITY)
      - 🇺🇸 美国 UDP (端口跳跃)
      - 🇺🇸 美国 UDP TUIC
    url: https://www.gstatic.com/generate_204
    interval: 60
    timeout: 3000

  - name: 📚 学术访问
    type: select
    proxies:
      - DIRECT
      - 🇺🇸 美国 TCP (VLESS+REALITY)
      - 🇺🇸 美国 TCP 备用 (VLESS+REALITY)
      - 🇺🇸 美国 UDP (端口跳跃)
      - 🇺🇸 美国 UDP TUIC

  - name: ✈️ Telegram 优化
    type: url-test
    proxies:
      - 🇺🇸 美国 UDP (端口跳跃)
      - 🇺🇸 美国 UDP TUIC
      - 🇺🇸 美国 TCP (VLESS+REALITY)
      - 🇺🇸 美国 TCP 备用 (VLESS+REALITY)
    url: https://telegram.org/img/website_icon.svg
    interval: 60
    timeout: 5000
    tolerance: 100

  # WebRTC / STUN / TURN 隐私专用组
  # 只允许 UDP 代理节点：HY2 端口跳跃 + TUIC
  # 禁止：VLESS TCP（不支持 UDP）、DIRECT（暴露国内 IP）
  # TUIC disabled 时 subscription_profiles.py 会删除 TUIC 节点，
  # 但 HY2 始终存在，该组始终非空。
  - name: 🔒 WebRTC 隐私
    type: select
    proxies:
      - 🇺🇸 美国 UDP (端口跳跃)
      - 🇺🇸 美国 UDP TUIC

# 5. 规则集（每天自动更新）
rule-providers:
  private:
    type: http
    behavior: domain
    url: https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/private.txt
    path: ./ruleset/private.yaml
    interval: 86400

  reject:
    type: http
    behavior: domain
    url: https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/reject.txt
    path: ./ruleset/reject.yaml
    interval: 86400

  icloud:
    type: http
    behavior: domain
    url: https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/icloud.txt
    path: ./ruleset/icloud.yaml
    interval: 86400

  apple:
    type: http
    behavior: domain
    url: https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/apple.txt
    path: ./ruleset/apple.yaml
    interval: 86400

  proxy:
    type: http
    behavior: domain
    url: https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/proxy.txt
    path: ./ruleset/proxy.yaml
    interval: 86400

  direct:
    type: http
    behavior: domain
    url: https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/direct.txt
    path: ./ruleset/direct.yaml
    interval: 86400

  telegramcidr:
    type: http
    behavior: ipcidr
    url: https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/telegramcidr.txt
    path: ./ruleset/telegramcidr.yaml
    interval: 86400

  cncidr:
    type: http
    behavior: ipcidr
    url: https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/cncidr.txt
    path: ./ruleset/cncidr.yaml
    interval: 86400

  lancidr:
    type: http
    behavior: ipcidr
    url: https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/lancidr.txt
    path: ./ruleset/lancidr.yaml
    interval: 86400

# 6. 规则
# 顺序策略：从特殊到通用
#   1. 固定 IP 直连（国内服务）
#   2. IPv6 REJECT
#   3. WebRTC / STUN / TURN 防泄露（🔒 WebRTC 隐私，仅 HY2/TUIC）
#   4. MSFT NCSI（DIRECT，防止触发 IPv6 检测）
#   5. 学术 / AI / Google / GitHub / Telegram（按策略组）
#   6. Steam（DIRECT，国内低延迟）
#   7. 规则集
#   8. 中国大陆 IP 直连
#   9. 默认代理
rules:
  # 固定 IP 直连（已确认的国内服务）
  - 'IP-CIDR,47.245.53.96/32,DIRECT,no-resolve'
  - 'IP-CIDR,192.238.178.243/32,DIRECT,no-resolve'

  # IPv6 全部拒绝（当前 IPv6 关闭）
  - 'IP-CIDR6,::/0,REJECT,no-resolve'

  # === WebRTC / STUN / TURN 防泄露 ===
  # 专用组 🔒 WebRTC 隐私 只含 HY2 + TUIC（纯 UDP 代理）
  # VLESS TCP / DIRECT 不会落入该组，不会因选到非 UDP 节点而泄露
  # 规则必须在 GEOIP,CN,DIRECT 之前

  # STUN/TURN 端口范围（Mihomo AND 逻辑：UDP + 端口范围）
  - 'AND,((NETWORK,UDP),(DST-PORT,3478-3481)),🔒 WebRTC 隐私'
  - 'AND,((NETWORK,UDP),(DST-PORT,5349)),🔒 WebRTC 隐私'
  - 'AND,((NETWORK,UDP),(DST-PORT,19302-19309)),🔒 WebRTC 隐私'

  # 常见 STUN/TURN 域名（精确匹配，避免 DOMAIN-KEYWORD 误伤）
  - 'DOMAIN-SUFFIX,stun.l.google.com,🔒 WebRTC 隐私'
  - 'DOMAIN-SUFFIX,stun1.l.google.com,🔒 WebRTC 隐私'
  - 'DOMAIN-SUFFIX,stun2.l.google.com,🔒 WebRTC 隐私'
  - 'DOMAIN-SUFFIX,stun.cloudflare.com,🔒 WebRTC 隐私'
  - 'DOMAIN-SUFFIX,stun.hitv.com,🔒 WebRTC 隐私'
  - 'DOMAIN-SUFFIX,stun.miwifi.com,🔒 WebRTC 隐私'
  - 'DOMAIN-SUFFIX,stun.chat.bilibili.com,🔒 WebRTC 隐私'
  - 'DOMAIN-SUFFIX,global.stun.twilio.com,🔒 WebRTC 隐私'
  - 'DOMAIN-SUFFIX,stun.nextcloud.com,🔒 WebRTC 隐私'
  - 'DOMAIN-SUFFIX,turn.kundun.com,🔒 WebRTC 隐私'

  # MSFT NCSI（DIRECT 防止触发 IPv6 检测）
  - 'DOMAIN-SUFFIX,msftconnecttest.com,DIRECT'
  - 'DOMAIN-SUFFIX,msftncsi.com,DIRECT'
  - 'DOMAIN-SUFFIX,microsoft.com,DIRECT'
  - 'DOMAIN-SUFFIX,microsoftonline.com,DIRECT'
  - 'DOMAIN-SUFFIX,windows.com,DIRECT'
  - 'DOMAIN-SUFFIX,windowsupdate.com,DIRECT'
  - 'DOMAIN-SUFFIX,mp.microsoft.com,DIRECT'
  - 'DOMAIN-SUFFIX,xboxlive.com,DIRECT'
  - 'DOMAIN-SUFFIX,xboxservices.com,DIRECT'
  - 'DOMAIN-SUFFIX,gamepass.com,DIRECT'
  - 'DOMAIN-SUFFIX,playfabapi.com,DIRECT'
  - 'DOMAIN,ipv6.msftconnecttest.com,REJECT'
  - 'DOMAIN,ipv6.msftncsi.com,REJECT'

  # 学术资源（📚 学术访问）
  - 'DOMAIN-SUFFIX,sciencedirect.com,📚 学术访问'
  - 'DOMAIN-SUFFIX,sciencedirectassets.com,📚 学术访问'
  - 'DOMAIN-SUFFIX,els-cdn.com,📚 学术访问'
  - 'DOMAIN-SUFFIX,elsevier.com,📚 学术访问'
  - 'DOMAIN-SUFFIX,elsevier-ae.com,📚 学术访问'
  - 'DOMAIN-SUFFIX,elsevier.io,📚 学术访问'
  - 'DOMAIN-SUFFIX,elseviercdn.cn,📚 学术访问'
  - 'DOMAIN-SUFFIX,scopus.com,📚 学术访问'
  - 'DOMAIN-SUFFIX,springer.com,📚 学术访问'
  - 'DOMAIN-SUFFIX,springernature.com,📚 学术访问'
  - 'DOMAIN-SUFFIX,nature.com,📚 学术访问'
  - 'DOMAIN-SUFFIX,wiley.com,📚 学术访问'
  - 'DOMAIN-SUFFIX,tandfonline.com,📚 学术访问'
  - 'DOMAIN-SUFFIX,jstor.org,📚 学术访问'
  - 'DOMAIN-SUFFIX,ieee.org,📚 学术访问'
  - 'DOMAIN-SUFFIX,acs.org,📚 学术访问'
  - 'DOMAIN-SUFFIX,rsc.org,📚 学术访问'
  - 'DOMAIN-SUFFIX,sagepub.com,📚 学术访问'
  - 'DOMAIN-SUFFIX,science.org,📚 学术访问'
  - 'DOMAIN-SUFFIX,cell.com,📚 学术访问'
  - 'DOMAIN-SUFFIX,thelancet.com,📚 学术访问'
  - 'DOMAIN-SUFFIX,bmj.com,📚 学术访问'
  - 'DOMAIN-SUFFIX,oup.com,📚 学术访问'
  - 'DOMAIN-SUFFIX,cambridge.org,📚 学术访问'
  - 'DOMAIN-SUFFIX,arxiv.org,📚 学术访问'
  - 'DOMAIN-SUFFIX,nih.gov,📚 学术访问'

  # OpenAI / ChatGPT（🤖 GPT 优化）
  - 'DOMAIN-SUFFIX,openai.com,🤖 GPT 优化'
  - 'DOMAIN-SUFFIX,chatgpt.com,🤖 GPT 优化'
  - 'DOMAIN-SUFFIX,oaistatic.com,🤖 GPT 优化'
  - 'DOMAIN-SUFFIX,oaiusercontent.com,🤖 GPT 优化'
  - 'DOMAIN-SUFFIX,openaiusercontent.com,🤖 GPT 优化'
  - 'DOMAIN-SUFFIX,ai.com,🤖 GPT 优化'
  - 'DOMAIN-SUFFIX,auth0.com,🤖 GPT 优化'
  - 'DOMAIN-SUFFIX,arkoselabs.com,🤖 GPT 优化'
  - 'DOMAIN-SUFFIX,statsigapi.net,🤖 GPT 优化'
  - 'DOMAIN-SUFFIX,featuregates.org,🤖 GPT 优化'
  - 'DOMAIN-SUFFIX,intercom.io,🤖 GPT 优化'
  - 'DOMAIN-SUFFIX,intercomcdn.com,🤖 GPT 优化'
  - 'DOMAIN-SUFFIX,sentry.io,🤖 GPT 优化'
  - 'DOMAIN-SUFFIX,browser-intake-datadoghq.com,🤖 GPT 优化'
  - 'DOMAIN-SUFFIX,chatgpt.livekit.cloud,🤖 GPT 优化'
  - 'DOMAIN,challenges.cloudflare.com,🤖 GPT 优化'

  # Google（🌐 Google 优化）
  - 'DOMAIN-SUFFIX,google.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,google.com.hk,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,google.com.tw,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,gmail.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,googlemail.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,googleapis.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,gstatic.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,googleusercontent.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,ggpht.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,gvt1.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,gvt2.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,gvt3.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,googlevideo.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,youtube.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,ytimg.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,youtu.be,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,withgoogle.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,googleblog.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,googleadservices.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,googlesyndication.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,google-analytics.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,googletagmanager.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,googletagservices.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,doubleclick.net,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,recaptcha.net,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,appspot.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,firebaseapp.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,firebaseio.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,blogger.com,🌐 Google 优化'
  - 'DOMAIN-SUFFIX,blogspot.com,🌐 Google 优化'

  # GitHub（⚡ GitHub 加速）
  - 'DOMAIN-SUFFIX,github.com,⚡ GitHub 加速'
  - 'DOMAIN-SUFFIX,github.io,⚡ GitHub 加速'
  - 'DOMAIN-SUFFIX,githubusercontent.com,⚡ GitHub 加速'
  - 'DOMAIN-SUFFIX,githubassets.com,⚡ GitHub 加速'
  - 'DOMAIN-SUFFIX,githubapp.com,⚡ GitHub 加速'
  - 'DOMAIN-SUFFIX,github.dev,⚡ GitHub 加速'
  - 'DOMAIN-SUFFIX,ghcr.io,⚡ GitHub 加速'
  - 'DOMAIN-SUFFIX,githubcopilot.com,⚡ GitHub 加速'
  - 'DOMAIN-SUFFIX,github-cloud.s3.amazonaws.com,⚡ GitHub 加速'

  # Telegram（✈️ Telegram 优化）
  - 'DOMAIN-SUFFIX,telegram.org,✈️ Telegram 优化'
  - 'DOMAIN-SUFFIX,telegram.me,✈️ Telegram 优化'
  - 'DOMAIN-SUFFIX,telegram.dog,✈️ Telegram 优化'
  - 'DOMAIN-SUFFIX,t.me,✈️ Telegram 优化'
  - 'DOMAIN-SUFFIX,telegra.ph,✈️ Telegram 优化'
  - 'DOMAIN-SUFFIX,tdesktop.com,✈️ Telegram 优化'
  - 'RULE-SET,telegramcidr,✈️ Telegram 优化,no-resolve'

  # CDN / 开发资源（🚀 节点选择）
  - 'DOMAIN-SUFFIX,cloudflare.com,🚀 节点选择'
  - 'DOMAIN-SUFFIX,cdnjs.com,🚀 节点选择'
  - 'DOMAIN-SUFFIX,jsdelivr.net,🚀 节点选择'
  - 'DOMAIN-SUFFIX,bootstrapcdn.com,🚀 节点选择'
  - 'DOMAIN-SUFFIX,fontawesome.com,🚀 节点选择'
  - 'DOMAIN-SUFFIX,fontawesomecdn.com,🚀 节点选择'
  - 'DOMAIN-SUFFIX,overleaf.com,🚀 节点选择'
  - 'DOMAIN-SUFFIX,overleafusercontent.com,🚀 节点选择'
  - 'DOMAIN-SUFFIX,sharelatex.com,🚀 节点选择'
  - 'DOMAIN-SUFFIX,steampowered.com,🚀 节点选择'

  # Steam / 游戏（DIRECT — 国内低延迟）
  - 'DOMAIN-SUFFIX,steamcontent.com,DIRECT'
  - 'DOMAIN-SUFFIX,steamserver.net,DIRECT'
  - 'DOMAIN-SUFFIX,rmbgame.net,DIRECT'

  # 规则集
  - 'RULE-SET,reject,REJECT'
  - 'RULE-SET,private,DIRECT'
  - 'RULE-SET,lancidr,DIRECT,no-resolve'
  - 'RULE-SET,icloud,DIRECT'
  - 'RULE-SET,apple,DIRECT'
  - 'RULE-SET,direct,DIRECT'
  - 'RULE-SET,proxy,🚀 节点选择'
  - 'RULE-SET,cncidr,DIRECT,no-resolve'

  # 国内域名 DIRECT
  - 'GEOIP,LAN,DIRECT'
  - 'DOMAIN-KEYWORD,Microsoft,DIRECT'
  - 'DOMAIN-SUFFIX,office.com,DIRECT'
  - 'DOMAIN-SUFFIX,visualstudio.com,DIRECT'
  - 'DOMAIN-SUFFIX,vscode-cdn.net,DIRECT'
  - 'DOMAIN-KEYWORD,vscode,DIRECT'
  - 'DOMAIN-SUFFIX,nvidia.com,DIRECT'

  # 中国大陆 IP 直连（最后防线）
  - 'GEOIP,CN,DIRECT'

  # 默认走代理
  - 'MATCH,🚀 节点选择'
