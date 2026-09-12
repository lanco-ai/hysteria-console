import { useEffect } from 'react';
import { ConsolePreview } from './ConsolePreview';

export function HomePage() {
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window)) return;
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        entry.target.classList.add('site-reveal');
        observer.unobserve(entry.target);
      }
    }, { threshold: 0.1 });
    document.querySelectorAll('.site-feature, .site-console').forEach(element => observer.observe(element));
    const disconnect = () => observer.disconnect();
    window.addEventListener('pagehide', disconnect);
    return () => {
      window.removeEventListener('pagehide', disconnect);
      observer.disconnect();
    };
  }, []);

  return <>
    <a className="skip-link" href="#main-content">跳到主内容</a>
    <main id="main-content" tabIndex={-1}>
    <header className="site-header">
      <a href="/" className="site-brand"><span aria-hidden="true">H</span><strong>Hysteria<small>NETWORK CONSOLE</small></strong></a>
      <nav aria-label="首页导航"><a href="#services">服务能力</a><a href="#console-preview">控制台预览</a></nav>
      <a className="site-button site-button-small" href="/login">进入控制台 <span aria-hidden="true">↗</span></a>
    </header>
    <main className="site-main">
      <section className="site-hero">
        <div className="site-hero-copy">
          <p className="site-eyebrow"><span/> YOUR NETWORK, IN FOCUS</p>
          <h1>连接网络，<br/><em>掌控全局。</em></h1>
          <p className="site-lead">从多协议接入到流量洞察，<br/>在一个清晰的控制台里，管理你的网络。</p>
          <div className="site-actions"><a className="site-button" href="/login">进入控制台 <span aria-hidden="true">→</span></a><a className="site-text-link" href="#services">查看服务能力 <span aria-hidden="true">↓</span></a></div>
          <div className="site-protocols"><span>接入协议</span><b>Hysteria2</b><b>VLESS Reality</b><b>TUIC</b></div>
        </div>
        <div className="site-topology" role="img" aria-label="概念示意：Hysteria 控制台连接多协议接入、流量统计和健康监测">
          <div className="site-topology-grid"/>
          <div className="site-topology-caption"><span>NETWORK ARCHITECTURE</span><span>连接架构示意</span></div>
          <svg className="site-topology-lines" viewBox="0 0 520 460" fill="none" aria-hidden="true">
            <circle cx="260" cy="230" r="155" stroke="#cbdde2" strokeDasharray="3 9"/>
            <circle cx="260" cy="230" r="108" stroke="#dde7e9"/>
            <path d="M260 103V180M116 305H185L220 263M404 305H335L300 263" stroke="#85afbe" strokeWidth="1.5"/>
            <circle cx="260" cy="147" r="4" fill="#6798aa"/><circle cx="157" cy="305" r="4" fill="#6798aa"/><circle cx="363" cy="305" r="4" fill="#6798aa"/>
          </svg>
          <div className="site-hub"><span>H</span><strong>Hysteria</strong><small>NETWORK CONSOLE</small></div>
          <div className="site-node site-node-top"><span>01 / ACCESS</span><strong>多协议接入</strong><small>Hysteria2 · VLESS · TUIC</small></div>
          <div className="site-node site-node-left"><span>02 / INSIGHT</span><strong>流量统计</strong><small>用量 · 配额 · 周期</small></div>
          <div className="site-node site-node-right"><span>03 / HEALTH</span><strong>健康监测</strong><small>状态 · 告警 · 维护</small></div>
          <div className="site-topology-footer"><i/> 一个入口，清晰连接每一环</div>
        </div>
      </section>

      <section className="site-services" id="services" aria-labelledby="services-title">
        <div className="site-section-heading"><div><p className="site-eyebrow">01 / CAPABILITIES</p><h2 id="services-title">复杂的网络，清晰的管理。</h2></div><p>把连接、用量和维护，<br/>放在同一个工作空间。</p></div>
        <div className="site-feature-grid">
          <article className="site-feature"><span className="site-feature-icon" aria-hidden="true">↗</span><span className="site-feature-index">01</span><h3>多协议接入</h3><p>集中管理接入协议与订阅模板，让不同使用场景拥有合适的连接方式。</p><div className="site-feature-tags"><span>Hysteria2</span><span>VLESS Reality</span><span>TUIC</span></div></article>
          <article className="site-feature"><span className="site-feature-icon" aria-hidden="true">▥</span><span className="site-feature-index">02</span><h3>流量与配额</h3><p>查看流量变化，管理用户配额与账期，了解每一份用量的去向。</p><div className="site-feature-tags"><span>趋势分析</span><span>用户配额</span><span>周期管理</span></div></article>
          <article className="site-feature"><span className="site-feature-icon" aria-hidden="true">⌁</span><span className="site-feature-index">03</span><h3>健康与维护</h3><p>汇总服务状态、证书与更新记录，为日常维护提供清晰的检查入口。</p><div className="site-feature-tags"><span>服务探测</span><span>健康状态</span><span>更新管理</span></div></article>
        </div>
      </section>

      <section className="site-preview-section" id="console-preview" aria-labelledby="preview-title">
        <div className="site-section-heading"><div><p className="site-eyebrow">02 / WORKSPACE</p><h2 id="preview-title">全局在眼前，操作有条理。</h2></div><p>从细节到全貌，<br/>无需在多个工具间切换。</p></div>
        <ConsolePreview/>
      </section>

      <section className="site-closing"><div><p className="site-eyebrow">LESS FRICTION. MORE CLARITY.</p><h2>让网络管理，回归简单。</h2><p>从一个清晰的控制台开始。</p></div><a href="/login" className="site-button">进入控制台 <span aria-hidden="true">→</span></a></section>
    </main>
    <footer className="site-footer"><a href="/" className="site-footer-brand">Hysteria <span>Network Console</span></a><span>连接 · 洞察 · 管理</span></footer>
    </main>
  </>;
}
