import type { ReactElement } from 'react';

export function VideoAssetPreview({ url, contentType }: { url?: string; contentType?: string }): ReactElement {
  if (!url) return <div className="video-preview empty">暂无结果</div>;
  if (contentType?.startsWith('video/')) return <video className="video-preview" src={url} controls preload="metadata" />;
  return <img className="video-preview" src={url} alt="生成结果" />;
}
