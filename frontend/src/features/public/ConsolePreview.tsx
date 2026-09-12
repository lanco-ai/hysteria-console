import { useState, type KeyboardEvent, type MouseEvent } from 'react';

type Demo = 'traffic' | 'users' | 'health';

const demos: readonly Demo[] = ['traffic', 'users', 'health'];

function initialDemo(): Demo {
  return demos.find(demo => window.location.hash === `#demo-${demo}`) ?? 'traffic';
}

export function ConsolePreview() {
  const [selected, setSelected] = useState<Demo>(initialDemo);

  const activate = (demo: Demo, focus: boolean) => {
    setSelected(demo);
    if (focus) document.getElementById(`demo-tab-${demo}`)?.focus();
  };

  const clickTab = (event: MouseEvent<HTMLAnchorElement>, demo: Demo) => {
    event.preventDefault();
    activate(demo, false);
  };

  const keyTab = (event: KeyboardEvent<HTMLAnchorElement>, index: number) => {
    let target: number | undefined;
    if (event.key === 'ArrowRight') target = (index + 1) % demos.length;
    if (event.key === 'ArrowLeft') target = (index + demos.length - 1) % demos.length;
    if (event.key === 'Home') target = 0;
    if (event.key === 'End') target = demos.length - 1;
    if (event.key === ' ') target = index;
    if (target === undefined) return;
    const demo = demos[target];
    if (!demo) return;
    event.preventDefault();
    activate(demo, true);
  };

  return <div className="site-console">
    <div className="site-console-top"><strong><span aria-hidden="true">H /</span> 控制台预览</strong><span className="site-demo-label">界面示意 · 非实时数据</span></div>
    <div className="site-preview-nav" aria-label="预览内容" role="tablist">
      {demos.map((demo, index) => <a
        id={`demo-tab-${demo}`}
        href={`#demo-${demo}`}
        data-demo={demo}
        role="tab"
        aria-selected={selected === demo}
        aria-controls={`demo-${demo}`}
        tabIndex={selected === demo ? 0 : -1}
        key={demo}
        onClick={event => clickTab(event, demo)}
        onKeyDown={event => keyTab(event, index)}
      >{{ traffic: '流量分析', users: '用户管理', health: '健康状态' }[demo]}</a>)}
    </div>
    <section className="site-demo-panel" id="demo-traffic" aria-labelledby="demo-tab-traffic" role="tabpanel" tabIndex={0} hidden={selected !== 'traffic'}>
      <div className="site-demo-heading"><div><h3>流量趋势</h3><p>观察用量变化，合理分配资源。</p></div><span>最近 7 天 · 示例</span></div>
      <div className="site-demo-stats"><div><span>示例总用量</span><strong>60.2 <small>GB</small></strong></div><div><span>示例上行</span><strong>12.4 <small>GB</small></strong></div><div><span>示例下行</span><strong>47.8 <small>GB</small></strong></div></div>
      <figure className="site-chart"><svg viewBox="0 0 900 200" role="img" aria-label="示意折线图：一周内流量上下波动，并在周五达到高点，不代表实际用量">
        <defs><linearGradient id="site-chart-fill" x1="0" y1="0" x2="0" y2="1"><stop stopColor="#87b5c5" stopOpacity=".3"/><stop offset="1" stopColor="#87b5c5" stopOpacity="0"/></linearGradient></defs>
        <path d="M0 25H900M0 80H900M0 135H900M0 190H900" stroke="#e8edef" fill="none"/>
        <path d="M0 160C60 160 85 91 150 104S245 158 300 118S400 99 450 68S530 108 600 40S700 118 750 81S850 72 900 48V195H0Z" fill="url(#site-chart-fill)"/>
        <path d="M0 160C60 160 85 91 150 104S245 158 300 118S400 99 450 68S530 108 600 40S700 118 750 81S850 72 900 48" stroke="#6598ac" strokeWidth="2.5" fill="none"/>
      </svg><figcaption><span>周一</span><span>周二</span><span>周三</span><span>周四</span><span>周五</span><span>周六</span><span>周日</span></figcaption></figure>
    </section>
    <section className="site-demo-panel" id="demo-users" aria-labelledby="demo-tab-users" role="tabpanel" tabIndex={0} hidden={selected !== 'users'}>
      <div className="site-demo-heading"><div><h3>用户与配额</h3><p>查看使用情况，维护每位用户的连接权限。</p></div><span>示例账号 · 非真实用户</span></div>
      <div className="site-demo-user"><span className="site-demo-avatar">A</span><div><strong>示例用户 A</strong><small>本周期用量</small></div><meter min="0" max="100" value="42" aria-label="示例用户 A 已用 42% 配额">42%</meter><span>42 / 100 GB</span></div>
      <div className="site-demo-user"><span className="site-demo-avatar">B</span><div><strong>示例用户 B</strong><small>本周期用量</small></div><meter min="0" max="100" value="68" aria-label="示例用户 B 已用 68% 配额">68%</meter><span>68 / 100 GB</span></div>
      <div className="site-demo-user"><span className="site-demo-avatar">C</span><div><strong>示例用户 C</strong><small>本周期用量</small></div><meter min="0" max="100" value="15" aria-label="示例用户 C 已用 15% 配额">15%</meter><span>15 / 100 GB</span></div>
      <p className="site-preview-note">实际用户、订阅链接与套餐信息仅在管理员登录后展示。</p>
    </section>
    <section className="site-demo-panel" id="demo-health" aria-labelledby="demo-tab-health" role="tabpanel" tabIndex={0} hidden={selected !== 'health'}>
      <div className="site-demo-heading"><div><h3>服务健康</h3><p>将服务检查和维护入口集中呈现。</p></div><span>状态展示示例</span></div>
      <div className="site-demo-health"><span>协议服务</span><span>Hysteria2 / Xray / TUIC</span><b>正常 · 示例</b></div>
      <div className="site-demo-health"><span>认证服务</span><span>连接认证与访问控制</span><b>正常 · 示例</b></div>
      <div className="site-demo-health"><span>证书与备份</span><span>有效期与最近备份检查</span><b>已检查 · 示例</b></div>
      <div className="site-demo-health"><span>版本维护</span><span>检查更新与历史记录</span><b>待检查 · 示例</b></div>
      <p className="site-preview-note">这里不提供实时运行状态，实际检查结果请进入控制台查看。</p>
    </section>
  </div>;
}
