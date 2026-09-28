import { Handle, Position, type NodeProps } from '@xyflow/react';

const labels: Record<string, string> = {
  prompt: '提示词', image_asset: '图片素材', text_to_image: '文生图', image_to_video: '图生视频',
  first_last_frame_video: '首尾帧视频', preview: '结果预览',
};
type NodeData = Record<string, unknown> & {
  onChange?: (patch: Record<string, unknown>) => void;
  onRun?: () => void;
  candidate_urls?: string[];
  candidate_asset_refs?: string[];
  invalid_asset_refs?: string[];
  onAssetError?: (ref: string) => void;
  run_disabled?: boolean;
  run_reason?: string;
  run_state?: string;
};

export function VideoNode({ data, type }: NodeProps) {
  const values = data as NodeData;
  const nodeType = type || 'prompt';
  const inputs = nodeType === 'text_to_image' ? ['prompt'] : nodeType === 'image_to_video' ? ['image'] : nodeType === 'first_last_frame_video' ? ['first_frame', 'last_frame'] : nodeType === 'preview' ? ['media'] : [];
  const outputs = nodeType === 'prompt' ? ['text'] : nodeType === 'image_asset' ? ['image'] : nodeType === 'text_to_image' ? ['image', 'first_frame', 'last_frame'] : nodeType === 'image_to_video' || nodeType === 'first_last_frame_video' ? ['video'] : [];
  const refs = Array.isArray(values.candidate_asset_refs) ? values.candidate_asset_refs : [];
  const urls = Array.isArray(values.candidate_urls) ? values.candidate_urls : [];
  const first = String(values.selected_first_asset_ref || '');
  const last = String(values.selected_last_asset_ref || '');
  return <div className={`video-node video-node-${nodeType}`} data-state={String(values.run_state || 'idle')}>
    {inputs.map((id, index) => <Handle key={id} id={id} type="target" position={Position.Left} style={{ top: `${((index + 1) * 100) / (inputs.length + 1)}%` }} />)}
    <div className="video-node-heading"><strong>{labels[nodeType] || nodeType}</strong>{values.run_state ? <small>{String(values.run_state)}</small> : null}</div>
    {typeof values.label === 'string' && values.label ? <small>{values.label}</small> : null}
    {nodeType === 'prompt' ? <textarea className="nodrag nopan" aria-label="提示词" value={String(values.text || '')} onChange={event => values.onChange?.({ text: event.target.value })} rows={4} placeholder="描述想生成的画面…" /> : null}
    {nodeType === 'text_to_image' ? <>
      <button type="button" className="btn btn-primary btn-sm nodrag nopan video-node-run" onClick={() => values.onRun?.()} disabled={values.run_disabled}>生成候选图</button>
      {values.run_reason ? <small className="video-node-warning">{values.run_reason}</small> : null}
      {refs.length ? <div className="video-candidate-gallery" aria-label="候选图片">
        {refs.map((ref, index) => <div className="video-candidate" key={`${ref}-${index}`}>
          <img src={urls[index]} alt={`候选图 ${index + 1}`} onError={() => values.onAssetError?.(ref)} />
          <div className="video-candidate-badges">{first === ref ? <span>首帧</span> : null}{last === ref ? <span>尾帧</span> : null}</div>
          {values.invalid_asset_refs?.includes(ref) ? <small className="video-node-warning">素材不可用</small> : null}
          <div className="video-candidate-actions">
            <button type="button" className="nodrag nopan" disabled={values.invalid_asset_refs?.includes(ref)} aria-pressed={first === ref} onClick={() => values.onChange?.({ selected_first_asset_ref: ref, ...(last === ref ? { selected_last_asset_ref: '' } : {}) })}>设为首帧</button>
            <button type="button" className="nodrag nopan" disabled={values.invalid_asset_refs?.includes(ref)} aria-pressed={last === ref} onClick={() => values.onChange?.({ selected_last_asset_ref: ref, ...(first === ref ? { selected_first_asset_ref: '' } : {}) })}>设为尾帧</button>
          </div>
        </div>)}
      </div> : <small>先生成候选图，再分别选择首帧和尾帧。</small>}
      <div className="video-node-port-labels"><span>首帧</span><span>尾帧</span></div>
    </> : null}
    {nodeType === 'first_last_frame_video' || nodeType === 'image_to_video' ? <>
      <small>{String(values.prompt || '填写运动提示词')}</small>
      <button type="button" className="btn btn-primary btn-sm nodrag nopan video-node-run" onClick={() => values.onRun?.()} disabled={values.run_disabled}>生成视频</button>
      {values.run_reason ? <small className="video-node-warning">{values.run_reason}</small> : null}
    </> : null}
    {nodeType === 'preview' ? (typeof values.video_url === 'string' && values.video_url ? <>
      <video className="nodrag nopan" src={values.video_url} controls preload="metadata" />
      <a className="nodrag nopan" href={values.video_url} download>下载视频</a>
    </> : <small>视频结果会显示在这里。</small>) : null}
    {outputs.map((id, index) => <Handle key={id} id={id} type="source" position={Position.Right} style={{ top: `${((index + 1) * 100) / (outputs.length + 1)}%` }} />)}
  </div>;
}
