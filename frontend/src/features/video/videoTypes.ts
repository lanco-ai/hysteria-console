export type VideoSettings = {
  provider: string;
  base_url: string;
  api_key_configured: boolean;
  api_key_masked: string;
};

export type VideoCapabilities = {
  image_models: string[];
  video_models: string[];
  first_last_frame: { supported: boolean; reason?: string | null };
  video_composition: { supported: boolean; reason?: string | null };
};

export type VideoWorkflow = {
  id: string;
  title?: string;
  nodes: Array<Record<string, unknown>>;
  edges: Array<Record<string, unknown>>;
  version?: number;
};

export type VideoRun = {
  id: string;
  workflow_id: string;
  state: string;
  node_status: Record<string, { state: string }>;
  assets?: Record<string, string>;
  error?: string;
};

export type VideoAsset = { id: string; filename: string; content_type: string; size: number };
