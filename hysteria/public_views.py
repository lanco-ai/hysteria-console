"""Public, illustrative pages; no runtime state access or service imports."""

from typing import Callable


def render_home(*, html_page: Callable[..., str], asset_version: str) -> str:
    """Public product overview with explicitly illustrative, non-live previews."""
    body = """<header class="site-header">
  <a href="/" class="site-brand"><span aria-hidden="true">H</span><strong>Hysteria<small>NETWORK CONSOLE</small></strong></a>
  <nav aria-label="首页导航"><a href="#services">服务能力</a><a href="#console-preview">控制台预览</a></nav>
  <a class="site-button site-button-small" href="/login">进入控制台 <span aria-hidden="true">↗</span></a>
</header>
<main class="site-main">
<section class="site-hero">
  <div class="site-hero-copy">
    <p class="site-eyebrow"><span></span> YOUR NETWORK, IN FOCUS</p>
    <h1>连接网络，<br><em>掌控全局。</em></h1>
    <p class="site-lead">从多协议接入到流量洞察，<br>在一个清晰的控制台里，管理你的网络。</p>
    <div class="site-actions"><a class="site-button" href="/login">进入控制台 <span aria-hidden="true">→</span></a><a class="site-text-link" href="#services">查看服务能力 <span aria-hidden="true">↓</span></a></div>
    <div class="site-protocols"><span>接入协议</span><b>Hysteria2</b><b>VLESS Reality</b><b>TUIC</b></div>
  </div>
  <div class="site-topology" role="img" aria-label="概念示意：Hysteria 控制台连接多协议接入、流量统计和健康监测">
    <div class="site-topology-grid"></div>
    <div class="site-topology-caption"><span>NETWORK ARCHITECTURE</span><span>连接架构示意</span></div>
    <svg class="site-topology-lines" viewBox="0 0 520 460" fill="none" aria-hidden="true">
      <circle cx="260" cy="230" r="155" stroke="#cbdde2" stroke-dasharray="3 9"/>
      <circle cx="260" cy="230" r="108" stroke="#dde7e9"/>
      <path d="M260 103V180M116 305H185L220 263M404 305H335L300 263" stroke="#85afbe" stroke-width="1.5"/>
      <circle cx="260" cy="147" r="4" fill="#6798aa"/><circle cx="157" cy="305" r="4" fill="#6798aa"/><circle cx="363" cy="305" r="4" fill="#6798aa"/>
    </svg>
    <div class="site-hub"><span>H</span><strong>Hysteria</strong><small>NETWORK CONSOLE</small></div>
    <div class="site-node site-node-top"><span>01 / ACCESS</span><strong>多协议接入</strong><small>Hysteria2 · VLESS · TUIC</small></div>
    <div class="site-node site-node-left"><span>02 / INSIGHT</span><strong>流量统计</strong><small>用量 · 配额 · 周期</small></div>
    <div class="site-node site-node-right"><span>03 / HEALTH</span><strong>健康监测</strong><small>状态 · 告警 · 维护</small></div>
    <div class="site-topology-footer"><i></i> 一个入口，清晰连接每一环</div>
  </div>
</section>

<section class="site-services" id="services" aria-labelledby="services-title">
  <div class="site-section-heading"><div><p class="site-eyebrow">01 / CAPABILITIES</p><h2 id="services-title">复杂的网络，清晰的管理。</h2></div><p>把连接、用量和维护，<br>放在同一个工作空间。</p></div>
  <div class="site-feature-grid">
    <article class="site-feature"><span class="site-feature-icon" aria-hidden="true">↗</span><span class="site-feature-index">01</span><h3>多协议接入</h3><p>集中管理接入协议与订阅模板，让不同使用场景拥有合适的连接方式。</p><div class="site-feature-tags"><span>Hysteria2</span><span>VLESS Reality</span><span>TUIC</span></div></article>
    <article class="site-feature"><span class="site-feature-icon" aria-hidden="true">▥</span><span class="site-feature-index">02</span><h3>流量与配额</h3><p>查看流量变化，管理用户配额与账期，了解每一份用量的去向。</p><div class="site-feature-tags"><span>趋势分析</span><span>用户配额</span><span>周期管理</span></div></article>
    <article class="site-feature"><span class="site-feature-icon" aria-hidden="true">⌁</span><span class="site-feature-index">03</span><h3>健康与维护</h3><p>汇总服务状态、证书与更新记录，为日常维护提供清晰的检查入口。</p><div class="site-feature-tags"><span>服务探测</span><span>健康状态</span><span>更新管理</span></div></article>
  </div>
</section>

<section class="site-preview-section" id="console-preview" aria-labelledby="preview-title">
  <div class="site-section-heading"><div><p class="site-eyebrow">02 / WORKSPACE</p><h2 id="preview-title">全局在眼前，操作有条理。</h2></div><p>从细节到全貌，<br>无需在多个工具间切换。</p></div>
  <div class="site-console">
    <div class="site-console-top"><strong><span aria-hidden="true">H /</span> 控制台预览</strong><span class="site-demo-label">界面示意 · 非实时数据</span></div>
    <div class="site-preview-nav" aria-label="预览内容"><a id="demo-tab-traffic" href="#demo-traffic" data-demo="traffic">流量分析</a><a id="demo-tab-users" href="#demo-users" data-demo="users">用户管理</a><a id="demo-tab-health" href="#demo-health" data-demo="health">健康状态</a></div>
    <section class="site-demo-panel" id="demo-traffic" aria-labelledby="demo-tab-traffic">
      <div class="site-demo-heading"><div><h3>流量趋势</h3><p>观察用量变化，合理分配资源。</p></div><span>最近 7 天 · 示例</span></div>
      <div class="site-demo-stats"><div><span>示例总用量</span><strong>60.2 <small>GB</small></strong></div><div><span>示例上行</span><strong>12.4 <small>GB</small></strong></div><div><span>示例下行</span><strong>47.8 <small>GB</small></strong></div></div>
      <figure class="site-chart"><svg viewBox="0 0 900 200" role="img" aria-label="示意折线图：一周内流量上下波动，并在周五达到高点，不代表实际用量">
        <defs><linearGradient id="site-chart-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#87b5c5" stop-opacity=".3"/><stop offset="1" stop-color="#87b5c5" stop-opacity="0"/></linearGradient></defs>
        <path d="M0 25H900M0 80H900M0 135H900M0 190H900" stroke="#e8edef" fill="none"/>
        <path d="M0 160C60 160 85 91 150 104S245 158 300 118S400 99 450 68S530 108 600 40S700 118 750 81S850 72 900 48V195H0Z" fill="url(#site-chart-fill)"/>
        <path d="M0 160C60 160 85 91 150 104S245 158 300 118S400 99 450 68S530 108 600 40S700 118 750 81S850 72 900 48" stroke="#6598ac" stroke-width="2.5" fill="none"/>
      </svg><figcaption><span>周一</span><span>周二</span><span>周三</span><span>周四</span><span>周五</span><span>周六</span><span>周日</span></figcaption></figure>
    </section>
    <section class="site-demo-panel" id="demo-users" aria-labelledby="demo-tab-users">
      <div class="site-demo-heading"><div><h3>用户与配额</h3><p>查看使用情况，维护每位用户的连接权限。</p></div><span>示例账号 · 非真实用户</span></div>
      <div class="site-demo-user"><span class="site-demo-avatar">A</span><div><strong>示例用户 A</strong><small>本周期用量</small></div><meter min="0" max="100" value="42" aria-label="示例用户 A 已用 42% 配额">42%</meter><span>42 / 100 GB</span></div>
      <div class="site-demo-user"><span class="site-demo-avatar">B</span><div><strong>示例用户 B</strong><small>本周期用量</small></div><meter min="0" max="100" value="68" aria-label="示例用户 B 已用 68% 配额">68%</meter><span>68 / 100 GB</span></div>
      <div class="site-demo-user"><span class="site-demo-avatar">C</span><div><strong>示例用户 C</strong><small>本周期用量</small></div><meter min="0" max="100" value="15" aria-label="示例用户 C 已用 15% 配额">15%</meter><span>15 / 100 GB</span></div>
      <p class="site-preview-note">实际用户、订阅链接与套餐信息仅在管理员登录后展示。</p>
    </section>
    <section class="site-demo-panel" id="demo-health" aria-labelledby="demo-tab-health">
      <div class="site-demo-heading"><div><h3>服务健康</h3><p>将服务检查和维护入口集中呈现。</p></div><span>状态展示示例</span></div>
      <div class="site-demo-health"><span>协议服务</span><span>Hysteria2 / Xray / TUIC</span><b>正常 · 示例</b></div>
      <div class="site-demo-health"><span>认证服务</span><span>连接认证与访问控制</span><b>正常 · 示例</b></div>
      <div class="site-demo-health"><span>证书与备份</span><span>有效期与最近备份检查</span><b>已检查 · 示例</b></div>
      <div class="site-demo-health"><span>版本维护</span><span>检查更新与历史记录</span><b>待检查 · 示例</b></div>
      <p class="site-preview-note">这里不提供实时运行状态，实际检查结果请进入控制台查看。</p>
    </section>
  </div>
</section>

<section class="site-closing"><div><p class="site-eyebrow">LESS FRICTION. MORE CLARITY.</p><h2>让网络管理，回归简单。</h2><p>从一个清晰的控制台开始。</p></div><a href="/login" class="site-button">进入控制台 <span aria-hidden="true">→</span></a></section>
</main>
<footer class="site-footer"><a href="/" class="site-footer-brand">Hysteria <span>Network Console</span></a><span>连接 · 洞察 · 管理</span></footer>"""
    body += '<script src="/static/home.js?v=' + asset_version + '" defer></script>'
    return html_page('Hysteria · 连接网络，掌控全局', body, body_class='page-home page-site')
