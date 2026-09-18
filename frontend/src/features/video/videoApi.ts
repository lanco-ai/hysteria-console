import type { VideoAsset, VideoCapabilities, VideoRun, VideoSettings, VideoWorkflow } from './videoTypes';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: 'same-origin', ...init });
  let payload: unknown = null;
  try { payload = await response.json(); } catch { /* the boundary may return an empty error */ }
  if (!response.ok) throw new Error(typeof payload === 'object' && payload && 'error' in payload ? String((payload as { error: unknown }).error) : `request_failed_${response.status}`);
  return payload as T;
}

export const loadVideoSettings = () => request<VideoSettings>('/api/video/settings');
export const saveVideoSettings = (values: { baseUrl: string; apiKey?: string; provider?: string }) => request<VideoSettings>('/api/video/settings', {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ base_url: values.baseUrl, ...(values.apiKey ? { api_key: values.apiKey } : {}), ...(values.provider ? { provider: values.provider } : {}) }),
});
export const testVideoConnection = () => request<{ ok: boolean; models_count: number }>('/api/video/connection/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
export const loadVideoCapabilities = () => request<VideoCapabilities>('/api/video/capabilities');
export const loadWorkflows = async () => (await request<{ workflows: VideoWorkflow[] }>('/api/video/workflows')).workflows;
export const saveWorkflow = (workflow: Omit<VideoWorkflow, 'id'> & Partial<Pick<VideoWorkflow, 'id'>>) => request<VideoWorkflow>('/api/video/workflows', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(workflow) });
export const createRun = (workflowId: string) => request<VideoRun>('/api/video/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workflow_id: workflowId }) });
export const loadRun = (runId: string) => request<VideoRun>(`/api/video/runs/${encodeURIComponent(runId)}`);
export const cancelRun = (runId: string) => request<VideoRun>(`/api/video/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
export const uploadAsset = (file: Blob, filename: string) => request<VideoAsset>('/api/video/assets', { method: 'POST', headers: { 'Content-Type': file.type, 'X-File-Name': filename }, body: file });
