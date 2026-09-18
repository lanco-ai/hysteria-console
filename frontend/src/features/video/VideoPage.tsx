import type { ReactElement } from 'react';
import { CodexShell } from '../../shared/CodexShell';

export function VideoPage({ publicHost }: { publicHost: string }): ReactElement {
  return <CodexShell active="video" pageTitle="AI 视频" authStatus="authenticated"><section className="card"><h2>AI 视频</h2><p>工作流画布正在加载。</p></section></CodexShell>;
}
