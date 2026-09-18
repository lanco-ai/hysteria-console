import { Handle, Position, type NodeProps } from '@xyflow/react';

const labels: Record<string, string> = {
  prompt: '提示词', image_asset: '图片素材', text_to_image: '文生图', image_to_video: '图生视频',
  first_last_frame_video: '首尾帧视频', preview: '结果预览',
};

export function VideoNode({ data, type }: NodeProps) {
  const nodeType = type || 'prompt';
  const title = labels[nodeType] || nodeType;
  const inputs = nodeType === 'text_to_image' ? ['prompt'] : nodeType === 'image_to_video' ? ['image'] : nodeType === 'first_last_frame_video' ? ['first_frame', 'last_frame'] : nodeType === 'preview' ? ['media'] : [];
  const outputs = nodeType === 'prompt' ? ['text'] : nodeType === 'image_asset' ? ['image'] : nodeType === 'text_to_image' ? ['image'] : nodeType === 'image_to_video' || nodeType === 'first_last_frame_video' ? ['video'] : [];
  return <div className="video-node">
    {inputs.map((id, index) => <Handle key={id} id={id} type="target" position={Position.Left} style={{ top: `${((index + 1) * 100) / (inputs.length + 1)}%` }} />)}
    <strong>{title}</strong><small>{typeof data?.label === 'string' ? data.label : '节点参数'}</small>
    {outputs.map((id, index) => <Handle key={id} id={id} type="source" position={Position.Right} style={{ top: `${((index + 1) * 100) / (outputs.length + 1)}%` }} />)}
  </div>;
}
