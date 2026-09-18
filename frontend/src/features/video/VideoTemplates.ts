export type VideoTemplateNode = {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: Record<string, unknown>;
};

export type VideoTemplate = {
  id: string;
  title: string;
  nodes: VideoTemplateNode[];
  edges: Array<{ id: string; source: string; sourceHandle: string; target: string; targetHandle: string }>;
};

export const VIDEO_TEMPLATES: VideoTemplate[] = [
  {
    id: 'prompt-image-video',
    title: '提示词 → 文生图 → 图生视频',
    nodes: [
      { id: 'prompt', type: 'prompt', position: { x: 40, y: 120 }, data: { text: '' } },
      { id: 'image', type: 'text_to_image', position: { x: 320, y: 120 }, data: { model: '' } },
      { id: 'video', type: 'image_to_video', position: { x: 620, y: 120 }, data: { model: '' } },
      { id: 'preview', type: 'preview', position: { x: 920, y: 120 }, data: {} },
    ],
    edges: [
      { id: 'prompt-image', source: 'prompt', sourceHandle: 'text', target: 'image', targetHandle: 'prompt' },
      { id: 'image-video', source: 'image', sourceHandle: 'image', target: 'video', targetHandle: 'image' },
      { id: 'video-preview', source: 'video', sourceHandle: 'video', target: 'preview', targetHandle: 'media' },
    ],
  },
  {
    id: 'first-last-frame',
    title: '首帧图片 + 尾帧图片 → 视频',
    nodes: [
      { id: 'first', type: 'image_asset', position: { x: 40, y: 80 }, data: { label: '首帧' } },
      { id: 'last', type: 'image_asset', position: { x: 40, y: 240 }, data: { label: '尾帧' } },
      { id: 'video', type: 'first_last_frame_video', position: { x: 360, y: 160 }, data: { model: '' } },
      { id: 'preview', type: 'preview', position: { x: 720, y: 160 }, data: {} },
    ],
    edges: [
      { id: 'first-video', source: 'first', sourceHandle: 'image', target: 'video', targetHandle: 'first_frame' },
      { id: 'last-video', source: 'last', sourceHandle: 'image', target: 'video', targetHandle: 'last_frame' },
      { id: 'video-preview', source: 'video', sourceHandle: 'video', target: 'preview', targetHandle: 'media' },
    ],
  },
  {
    id: 'generated-frames',
    title: '首尾提示词 → 文生图 → 首尾帧视频',
    nodes: [
      { id: 'first-prompt', type: 'prompt', position: { x: 20, y: 40 }, data: { label: '首帧提示词' } },
      { id: 'last-prompt', type: 'prompt', position: { x: 20, y: 240 }, data: { label: '尾帧提示词' } },
      { id: 'first-image', type: 'text_to_image', position: { x: 280, y: 40 }, data: { model: '' } },
      { id: 'last-image', type: 'text_to_image', position: { x: 280, y: 240 }, data: { model: '' } },
      { id: 'video', type: 'first_last_frame_video', position: { x: 580, y: 140 }, data: { model: '' } },
      { id: 'preview', type: 'preview', position: { x: 900, y: 140 }, data: {} },
    ],
    edges: [
      { id: 'first-prompt-image', source: 'first-prompt', sourceHandle: 'text', target: 'first-image', targetHandle: 'prompt' },
      { id: 'last-prompt-image', source: 'last-prompt', sourceHandle: 'text', target: 'last-image', targetHandle: 'prompt' },
      { id: 'first-image-video', source: 'first-image', sourceHandle: 'image', target: 'video', targetHandle: 'first_frame' },
      { id: 'last-image-video', source: 'last-image', sourceHandle: 'image', target: 'video', targetHandle: 'last_frame' },
      { id: 'video-preview', source: 'video', sourceHandle: 'video', target: 'preview', targetHandle: 'media' },
    ],
  },
];
