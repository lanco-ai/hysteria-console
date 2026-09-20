export type VideoCapabilities = {
  image_models: string[];
  video_models: string[];
  first_last_frame: { supported: boolean; reason?: string | null };
  video_composition: { supported: boolean; reason?: string | null };
};

export type VideoAssistantShotDraft = {
  title: string;
  script: string;
  shot_type: string;
  character: string;
  scene: string;
  duration: number;
  image_prompt: string;
  motion_prompt: string;
  dialogue: string;
};

export type VideoAssistantDraft = {
  model: string;
  service_name: string;
  structured_output?: 'gemini_native_schema' | 'json_schema' | 'json_text_fallback';
  title: string;
  rewritten_text: string;
  style_prompt: string;
  aspect_ratio: '16:9' | '9:16' | '1:1';
  shots: VideoAssistantShotDraft[];
};

export type VideoWorkflow = {
  id: string;
  title?: string;
  nodes: Array<Record<string, unknown>>;
  edges: Array<Record<string, unknown>>;
  version?: number;
  storyboard?: VideoStoryboard;
};

export type VideoStoryboardShot = {
  id: string;
  title: string;
  script: string;
  shot_type: string;
  character: string;
  scene: string;
  duration: number;
  image_prompt: string;
  motion_prompt: string;
  dialogue: string;
  image_model: string;
  video_model: string;
  image_asset_id?: string;
  image_url?: string;
  video_url?: string;
  image_state: 'idle' | 'queued' | 'running' | 'succeeded' | 'failed' | 'stale';
  video_state: 'idle' | 'queued' | 'running' | 'succeeded' | 'failed' | 'stale';
};

export type VideoStoryboard = {
  title: string;
  source_text: string;
  rewritten_text: string;
  aspect_ratio: '16:9' | '9:16' | '1:1';
  style_prompt: string;
  shots: VideoStoryboardShot[];
};

export type VideoRun = {
  id: string;
  workflow_id: string;
  shot_id?: string | null;
  created_at?: number;
  state: string;
  node_status: Record<string, { state: string }>;
  assets?: Record<string, string>;
  error?: string;
  workflow?: VideoWorkflow;
};

export type VideoAsset = { id: string; filename: string; content_type: string; size: number };
