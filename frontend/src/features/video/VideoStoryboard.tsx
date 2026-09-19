import type { ReactElement } from 'react';
import type { VideoCapabilities, VideoStoryboard, VideoStoryboardShot } from './videoTypes';

type VideoStoryboardProps = {
  storyboard: VideoStoryboard;
  capabilities: VideoCapabilities | null;
  saveState: 'saved' | 'dirty' | 'saving' | 'error';
  busy: boolean;
  onChange: (next: VideoStoryboard) => void;
  onSave: () => void;
  onRunShot: (shot: VideoStoryboardShot) => void;
  onUploadImage: (shot: VideoStoryboardShot, file: File) => void;
  onRunAll: () => void;
  onOpenCanvas: () => void;
};

const statusLabels: Record<VideoStoryboardShot['image_state'], string> = {
  idle: '待生成', queued: '排队中', running: '生成中', succeeded: '已完成', failed: '失败', stale: '需要更新',
};

export function createStoryboardShot(index: number): VideoStoryboardShot {
  return {
    id: `shot-${Date.now()}-${index}`,
    title: `分镜 ${String(index + 1).padStart(2, '0')}`,
    script: '', shot_type: '中景', character: '', scene: '', duration: 5,
    image_prompt: '', motion_prompt: '', dialogue: '', image_model: 'grok-imagine-image',
    video_model: 'grok-imagine-video', image_state: 'idle', video_state: 'idle',
  };
}

export function createDefaultStoryboard(): VideoStoryboard {
  return {
    title: '未命名漫剧', source_text: '', rewritten_text: '', aspect_ratio: '9:16',
    style_prompt: '', shots: [createStoryboardShot(0), createStoryboardShot(1), createStoryboardShot(2)],
  };
}

function updateShot(storyboard: VideoStoryboard, id: string, patch: Partial<VideoStoryboardShot>): VideoStoryboard {
  return { ...storyboard, shots: storyboard.shots.map(shot => shot.id === id ? { ...shot, ...patch } : shot) };
}

function field(
  storyboard: VideoStoryboard,
  id: string,
  key: keyof VideoStoryboardShot,
  value: string | number,
  onChange: (next: VideoStoryboard) => void,
): void {
  onChange(updateShot(storyboard, id, { [key]: value } as Partial<VideoStoryboardShot>));
}

function stateClass(state: VideoStoryboardShot['image_state']): string {
  return `video-shot-state video-shot-state-${state}`;
}

export function VideoStoryboard({ storyboard, capabilities, saveState, busy, onChange, onSave, onRunShot, onUploadImage, onRunAll, onOpenCanvas }: VideoStoryboardProps): ReactElement {
  const firstLastSupported = Boolean(capabilities?.first_last_frame.supported);
  const addShot = () => onChange({ ...storyboard, shots: [...storyboard.shots, createStoryboardShot(storyboard.shots.length)] });
  const removeShot = (id: string) => {
    if (storyboard.shots.length <= 1) return;
    onChange({ ...storyboard, shots: storyboard.shots.filter(shot => shot.id !== id) });
  };
  const moveShot = (index: number, offset: -1 | 1) => {
    const nextIndex = index + offset;
    if (nextIndex < 0 || nextIndex >= storyboard.shots.length) return;
    const shots = [...storyboard.shots];
    const [shot] = shots.splice(index, 1);
    if (shot) shots.splice(nextIndex, 0, shot);
    onChange({ ...storyboard, shots });
  };
  const updateProject = (patch: Partial<VideoStoryboard>) => onChange({ ...storyboard, ...patch });

  return <section className="video-storyboard" aria-label="AI 漫剧分镜工作台">
    <header className="video-storyboard-header">
      <div className="video-storyboard-title-field"><span className="video-eyebrow">AI 漫剧工作台</span><input aria-label="作品名称" value={storyboard.title} onChange={event => updateProject({ title: event.target.value })} /><span className="video-save-state" data-state={saveState}>{saveState === 'saving' ? '保存中…' : saveState === 'dirty' ? '有未保存修改' : saveState === 'error' ? '保存失败' : '已保存'}</span></div>
      <div className="video-storyboard-header-actions"><label className="video-ratio-field">画幅<select value={storyboard.aspect_ratio} onChange={event => updateProject({ aspect_ratio: event.target.value as VideoStoryboard['aspect_ratio'] })}><option value="9:16">竖屏 9:16</option><option value="16:9">横屏 16:9</option><option value="1:1">方形 1:1</option></select></label><button type="button" className="button ghost" onClick={onOpenCanvas}>展开画布</button><button type="button" className="button secondary" onClick={onSave} disabled={busy}>保存</button><button type="button" className="button primary" onClick={onRunAll} disabled={busy || !storyboard.shots.length}>运行全部</button></div>
    </header>
    <div className="video-storyboard-body">
      <aside className="video-script-panel">
        <div className="video-panel-heading"><div><span className="video-eyebrow">故事文本</span><h2>文案改写</h2></div><button type="button" className="button ghost button-small" onClick={() => updateProject({ rewritten_text: storyboard.source_text })} disabled={!storyboard.source_text.trim()}>改写</button></div>
        <label><span>原始文案</span><textarea value={storyboard.source_text} onChange={event => updateProject({ source_text: event.target.value })} placeholder="输入故事、旁白或对话…" /></label>
        <label><span>改写后文案</span><textarea value={storyboard.rewritten_text} onChange={event => updateProject({ rewritten_text: event.target.value })} placeholder="改写后的文案将在这里显示…" /></label>
        <label><span>统一风格</span><textarea value={storyboard.style_prompt} onChange={event => updateProject({ style_prompt: event.target.value })} placeholder="例如：温暖的三维动画、柔和光线…" rows={3} /></label>
        <button type="button" className="button secondary video-split-button" onClick={() => updateProject({ shots: storyboard.shots.length ? storyboard.shots : [createStoryboardShot(0)] })}>按分镜整理</button>
      </aside>
      <main className="video-shot-list">
        <div className="video-shot-list-heading"><div><span className="video-eyebrow">Storyboard</span><h2>{storyboard.shots.length} 个镜头</h2></div><span className="video-shot-list-hint">先确认分镜，再生成图片和视频</span></div>
        {storyboard.shots.map((shot, index) => <article className="video-storyboard-row" data-shot-id={shot.id} key={shot.id}>
          <header className="video-shot-row-header"><div className="video-shot-index"><strong>{String(index + 1).padStart(2, '0')}</strong><input aria-label={`镜头 ${index + 1} 名称`} value={shot.title} onChange={event => field(storyboard, shot.id, 'title', event.target.value, onChange)} /></div><div className="video-shot-row-actions"><span className={stateClass(shot.image_state)}>{statusLabels[shot.image_state]}</span><button type="button" aria-label="上移分镜" onClick={() => moveShot(index, -1)} disabled={index === 0}>↑</button><button type="button" aria-label="下移分镜" onClick={() => moveShot(index, 1)} disabled={index === storyboard.shots.length - 1}>↓</button><button type="button" aria-label="删除分镜" onClick={() => removeShot(shot.id)} disabled={storyboard.shots.length <= 1}>×</button></div></header>
          <div className="video-shot-row-grid">
            <section className="video-shot-card video-shot-details"><div className="video-card-heading"><span>分镜设定</span><span>{shot.duration}s</span></div><label>景别<select value={shot.shot_type} onChange={event => field(storyboard, shot.id, 'shot_type', event.target.value, onChange)}><option>特写</option><option>近景</option><option>中景</option><option>全景</option></select></label><label>角色<input value={shot.character} onChange={event => field(storyboard, shot.id, 'character', event.target.value, onChange)} placeholder="角色名" /></label><label>场景<input value={shot.scene} onChange={event => field(storyboard, shot.id, 'scene', event.target.value, onChange)} placeholder="场景" /></label><label>时长<input type="number" min="1" max="30" value={shot.duration} onChange={event => field(storyboard, shot.id, 'duration', Number(event.target.value) || 1, onChange)} /></label></section>
            <section className="video-shot-card video-shot-image"><div className="video-card-heading"><span>文生图</span><span className={stateClass(shot.image_state)}>{statusLabels[shot.image_state]}</span></div>{shot.image_url ? <img src={shot.image_url} alt={`${shot.title} 图片`} /> : <div className="video-media-placeholder">图片预览</div>}<label>图片模型<select value={shot.image_model} onChange={event => field(storyboard, shot.id, 'image_model', event.target.value, onChange)}>{[shot.image_model, ...(capabilities?.image_models || [])].filter((model, index, models) => model && models.indexOf(model) === index).map(model => <option key={model} value={model}>{model}</option>)}</select></label><label>图片提示词<textarea value={shot.image_prompt} onChange={event => field(storyboard, shot.id, 'image_prompt', event.target.value, onChange)} placeholder="描述这一镜头的画面…" rows={3} /></label><label className="video-upload-control">上传首帧<input type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={event => { const file = event.target.files?.[0]; if (file) onUploadImage(shot, file); event.currentTarget.value = ''; }} /></label></section>
            <section className="video-shot-card video-shot-motion"><div className="video-card-heading"><span>运镜</span><span>{firstLastSupported ? '首尾帧可用' : '普通图生视频'}</span></div><label>视频模型<select value={shot.video_model} onChange={event => field(storyboard, shot.id, 'video_model', event.target.value, onChange)}>{[shot.video_model, ...(capabilities?.video_models || [])].filter((model, index, models) => model && models.indexOf(model) === index).map(model => <option key={model} value={model}>{model}</option>)}</select></label><label>运动描述<textarea value={shot.motion_prompt} onChange={event => field(storyboard, shot.id, 'motion_prompt', event.target.value, onChange)} placeholder="镜头缓慢推进，人物自然转身…" rows={4} /></label><label>对白 / 旁白<textarea value={shot.dialogue} onChange={event => field(storyboard, shot.id, 'dialogue', event.target.value, onChange)} placeholder="可选" rows={2} /></label><button type="button" className="button primary button-small" onClick={() => onRunShot(shot)} disabled={busy || !shot.image_prompt.trim()}>运行当前镜头</button></section>
            <section className="video-shot-card video-shot-video"><div className="video-card-heading"><span>图生视频</span><span className={stateClass(shot.video_state)}>{statusLabels[shot.video_state]}</span></div>{shot.video_url ? <video src={shot.video_url} controls preload="metadata" /> : <div className="video-media-placeholder video-media-placeholder-dark">视频预览</div>}<p>{shot.dialogue || '生成视频后可在这里预览和下载。'}</p></section>
          </div>
        </article>)}
        <button type="button" className="video-add-shot" onClick={addShot}>＋ 添加分镜</button>
      </main>
    </div>
    <footer className="video-storyboard-footer"><span>素材生成由当前配置的第三方 API 完成</span><span>{storyboard.style_prompt ? '已设置统一风格' : '建议先设置统一风格'}</span><button type="button" className="button ghost button-small" disabled>片段合成（待供应商支持）</button></footer>
  </section>;
}
