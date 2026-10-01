import { useContext } from 'react';
import { Icon } from '../../shared/icons';
import { PortalSessionContext, PortalShell } from './PortalShell';

export function VideoAccessState() {
  const session = useContext(PortalSessionContext);
  return <PortalShell active="video" pageTitle="AI 视频">
    <section className="portal-video-access portal-card">
      <div className="portal-video-mark" aria-hidden="true"><Icon name="video"/></div>
      <p className="portal-eyebrow">AI VIDEO STUDIO</p>
      <h2>把想法，变成画面。</h2>
      <p>从提示词到候选图片，再到视频。<br/>登录后继续你的作品，或开始一次新的创作。</p>
      <button className="portal-primary" type="button" onClick={session.onLogin} disabled={session.status === 'loading'}>{session.status === 'loading' ? '正在确认登录状态…' : '登录后开始创作'}</button>
      <div className="portal-video-steps" aria-label="创作流程"><span>01 <b>描述想法</b></span><span>02 <b>选择画面</b></span><span>03 <b>生成视频</b></span></div>
    </section>
  </PortalShell>;
}
