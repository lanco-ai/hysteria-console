import type { VideoAsset, VideoAssistantDraft, VideoCapabilities, VideoRun, VideoWorkflow } from './videoTypes';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: 'same-origin', ...init });
  let payload: unknown = null;
  try { payload = await response.json(); } catch { /* the boundary may return an empty error */ }
  if (!response.ok) throw new Error(typeof payload === 'object' && payload && 'error' in payload ? String((payload as { error: unknown }).error) : `request_failed_${response.status}`);
  return payload as T;
}

export const loadVideoCapabilities = () => request<VideoCapabilities>('/api/video/capabilities');
export const draftVideoStoryboard = (values: { idea: string; style_prompt: string; aspect_ratio: '9:16' | '16:9' | '1:1'; shot_count: number; shot_duration: number }) => request<VideoAssistantDraft>('/api/video/assistant/draft', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values),
});
export const loadWorkflows = async () => (await request<{ workflows: VideoWorkflow[] }>('/api/video/workflows')).workflows;
export const loadVideoRuns = async () => {
  const payload = await request<{ runs?: VideoRun[] }>('/api/video/runs');
  return Array.isArray(payload.runs) ? payload.runs : [];
};
export const saveWorkflow = (workflow: Omit<VideoWorkflow, 'id'> & Partial<Pick<VideoWorkflow, 'id'>>) => request<VideoWorkflow>('/api/video/workflows', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(workflow) });
export const createRun = (workflowId: string, shotId?: string) => request<VideoRun>('/api/video/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workflow_id: workflowId, ...(shotId ? { shot_id: shotId } : {}) }) });
export const loadRun = (runId: string) => request<VideoRun>(`/api/video/runs/${encodeURIComponent(runId)}`);
export const cancelRun = (runId: string) => request<VideoRun>(`/api/video/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
export const uploadAsset = (file: Blob, filename: string) => request<VideoAsset>('/api/video/assets', { method: 'POST', headers: { 'Content-Type': file.type, 'X-File-Name': filename }, body: file });
