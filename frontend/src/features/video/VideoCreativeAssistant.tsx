import { useState, type ReactElement } from 'react';
import { draftVideoStoryboard } from './videoApi';
import type { VideoAssistantDraft } from './videoTypes';

type VideoCreativeAssistantProps = {
  aspectRatio: VideoAssistantDraft['aspect_ratio'];
  stylePrompt: string;
  onClose: () => void;
  onApply: (draft: VideoAssistantDraft) => void;
};

function friendlyError(value: unknown): string {
  const code = value instanceof Error ? value.message : '';
  if (code === 'service_not_configured') return '请先到服务中心配置视频创意助手服务。';
  if (code === 'model_not_selected') return '请先到服务中心为视频创意助手选择模型。';
  if (code === 'model_not_available') return '所选模型已不可用，请刷新模型列表并重新选择。';
  if (code === 'authentication_failed') return 'AI 服务认证失败，请检查服务中心中的 API Key。';
  if (code === 'permission_denied') return '当前 AI 服务没有所选模型的访问权限。';
  if (code === 'rate_limited') return 'AI 服务请求频率受限，请稍后重试。';
  if (code === 'timeout') return 'AI 服务响应超时，请稍后重试。';
  if (code === 'invalid_model_response') return 'AI 返回的分镜格式无法安全解析，请调整创意后重试。';
  return 'AI 服务暂时无法生成分镜草稿，请稍后重试。';
}

export function VideoCreativeAssistant({ aspectRatio, stylePrompt, onClose, onApply }: VideoCreativeAssistantProps): ReactElement {
  const [idea, setIdea] = useState('');
  const [style, setStyle] = useState(stylePrompt);
  const [ratio, setRatio] = useState<VideoAssistantDraft['aspect_ratio']>(aspectRatio);
  const [shotCount, setShotCount] = useState(3);
  const [duration, setDuration] = useState(5);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState<VideoAssistantDraft | null>(null);

  const generate = async () => {
    if (!idea.trim() || busy) return;
    setBusy(true); setError(''); setDraft(null);
    try {
      setDraft(await draftVideoStoryboard({
        idea: idea.trim(), style_prompt: style.trim(), aspect_ratio: ratio,
        shot_count: shotCount, shot_duration: duration,
      }));
    } catch (value) { setError(friendlyError(value)); }
    finally { setBusy(false); }
  };

  const updateDraft = (patch: Partial<VideoAssistantDraft>) => setDraft(current => current ? { ...current, ...patch } : null);
  const updateShot = (index: number, patch: Partial<VideoAssistantDraft['shots'][number]>) => setDraft(current => current ? {
    ...current, shots: current.shots.map((shot, shotIndex) => shotIndex === index ? { ...shot, ...patch } : shot),
  } : null);

  return <section className="video-creative-assistant" role="dialog" aria-modal="false" aria-label="AI 创作助手">
    <header><div><span className="video-eyebrow">AI · 草稿模式</span><h2>AI 创作助手</h2><p>把创意整理成故事与分镜提示词；不会自动生成图片或视频。</p></div><button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>关闭</button></header>
    <div className="video-creative-fields">
      <label>故事创意<textarea aria-label="故事创意" value={idea} onChange={event => setIdea(event.target.value)} maxLength={6000} rows={3} placeholder="描述人物、冲突、场景和想要的结尾…" /></label>
      <label>统一视觉风格<textarea aria-label="统一视觉风格" value={style} onChange={event => setStyle(event.target.value)} maxLength={1200} rows={2} placeholder="例如：温暖的 3D 动画、柔和晨光、电影感构图" /></label>
      <div className="video-creative-controls"><label>画幅<select value={ratio} onChange={event => setRatio(event.target.value as VideoAssistantDraft['aspect_ratio'])}><option value="9:16">竖屏 9:16</option><option value="16:9">横屏 16:9</option><option value="1:1">方形 1:1</option></select></label><label>分镜数<input type="number" min={1} max={12} value={shotCount} onChange={event => setShotCount(Math.max(1, Math.min(12, Number(event.target.value) || 1)))} /></label><label>单镜时长<input type="number" min={1} max={30} value={duration} onChange={event => setDuration(Math.max(1, Math.min(30, Number(event.target.value) || 1)))} /> 秒</label></div>
      <button type="button" className="btn btn-primary" onClick={() => void generate()} disabled={busy || !idea.trim()}>{busy ? '正在整理分镜…' : '生成分镜草稿'}</button>
    </div>
    {error ? <p className="video-creative-error" role="alert">{error}</p> : null}
    {draft ? <div className="video-creative-preview">
      <p className="video-creative-model">{draft.service_name} · {draft.model} · 仅生成文字草稿 · {draft.structured_output === 'json_text_fallback' ? '结构化参数不支持，已降级为严格 JSON 文本并完成校验' : draft.structured_output === 'json_schema' ? 'JSON Schema 输出已通过校验' : 'Gemini 原生结构化输出已通过校验'}</p>
      <label>作品标题<input value={draft.title} maxLength={160} onChange={event => updateDraft({ title: event.target.value })} /></label>
      <label>整理后的故事<textarea value={draft.rewritten_text} maxLength={6000} rows={3} onChange={event => updateDraft({ rewritten_text: event.target.value })} /></label>
      <label>统一风格<textarea value={draft.style_prompt} maxLength={1200} rows={2} onChange={event => updateDraft({ style_prompt: event.target.value })} /></label>
      <div className="video-creative-shot-list">{draft.shots.map((shot, index) => <article key={`${index}-${shot.title}`}>
        <strong>分镜 {index + 1}</strong>
        <label>镜头名称<input value={shot.title} maxLength={160} onChange={event => updateShot(index, { title: event.target.value })} /></label>
        <label>画面提示词<textarea value={shot.image_prompt} maxLength={3000} rows={3} onChange={event => updateShot(index, { image_prompt: event.target.value })} /></label>
        <label>运动提示词<textarea value={shot.motion_prompt} maxLength={3000} rows={3} onChange={event => updateShot(index, { motion_prompt: event.target.value })} /></label>
        <label>场景<input value={shot.scene} maxLength={500} onChange={event => updateShot(index, { scene: event.target.value })} /></label>
        <label>对白 / 旁白<textarea value={shot.dialogue} maxLength={2000} rows={2} onChange={event => updateShot(index, { dialogue: event.target.value })} /></label>
      </article>)}</div>
      <button type="button" className="btn btn-primary" onClick={() => onApply(draft)} disabled={!draft.shots.length || draft.shots.some(shot => !shot.title.trim() || !shot.image_prompt.trim() || !shot.motion_prompt.trim())}>应用到当前工作流</button>
    </div> : null}
    <footer>AI 只提供可编辑文字建议。提交图片或视频生成任务仍需你单独操作。</footer>
  </section>;
}
