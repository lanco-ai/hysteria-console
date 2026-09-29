import type { ChatMessageData, ReasoningEffort } from './chatApi';

export type Citation = { id: string; document_id: string; title: string; page: number; quote: string; sha256: string };
export type WorkspaceMessage = ChatMessageData & { id: string; status: string; citations: Citation[]; usage?: Record<string, number>; context_truncated?: boolean };
export type Conversation = { id: string; title: string; project_id: string | null; messages: WorkspaceMessage[]; revision: number; updatedAt: number; draft: string; model: string; reasoningEffort: ReasoningEffort; active: string | null; message_count?: number };
export type LearningProject = { id: string; name: string; goal: string; instructions: string; revision: number };
export type Paper = { id: string; project_id: string; title: string; page_count: number; size: number; media_type: string };
export type WorkspaceEvent = { type: string; conversation?: Conversation; text?: string; error?: string; notice?: string; usage?: Record<string, number> };

const errors: Record<string, string> = {
  conflict: '这条记录已在其他窗口修改。草稿已保留，请刷新对话后再发送。',
  generation_in_progress: '这个对话正在生成回答，请等待或先停止。',
  request_id_conflict: '发送内容已变化，请刷新对话后重新发送。',
  project_not_empty: '项目里仍有对话或论文，请先处理这些内容。',
  document_outside_project: '所选论文已删除或不属于当前项目，请重新选择。',
  file_too_large: '文件不能超过 10 MB。',
  pdf_no_text: '未提取到文字。这可能是扫描件，请先转成可搜索的 PDF 或上传文字。',
  pdf_encrypted: '暂不支持加密 PDF，请上传可读取的文件。',
  pdf_page_limit: 'PDF 不能超过 200 页。',
  pdf_text_limit: '文件提取的文字过多，请拆分后上传。',
  invalid_pdf: 'PDF 无法解析，请检查文件或改传 TXT / Markdown。',
  pdf_extraction_timeout: 'PDF 提取超时，请拆分文件后重试。',
  pdf_extraction_busy: '正在处理另一份 PDF，请稍后重试。',
  unsupported_document: '支持 PDF、UTF-8 TXT 和 Markdown 文件。',
  utf8_required: '文本文件需要使用 UTF-8 编码。',
  login_required: '登录已失效，请重新登录。',
  settings_incomplete: '请在服务中心检查模型配置。',
  document_storage_full: '论文存储已达到 200 MB 或 200 个文件的限制。',
  conversation_full: '这个对话已达到 500 条消息，请新建对话继续。',
  generation_failed: '生成中断，已收到的内容已保存。请检查模型服务后再试。',
};

export class WorkspaceApiError extends Error {
  constructor(readonly status: number, readonly code: string) {
    super(errors[code] || (status === 413 ? '内容超过请求大小限制，请拆分后重试。' : `操作失败（${status}），请稍后重试。`));
  }
}

export async function workspaceRequest<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api/chat${path}`, { method, credentials: 'same-origin', cache: 'no-store',
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) });
  const value = await response.json().catch(() => ({})) as { error?: string };
  if (!response.ok) throw new WorkspaceApiError(response.status, value.error || 'unavailable');
  return value as T;
}

export async function uploadPaper(projectId: string, file: File): Promise<Paper> {
  if (file.size > 10 * 1024 * 1024) throw new WorkspaceApiError(413, 'file_too_large');
  const query = new URLSearchParams({ project_id: projectId, filename: file.name });
  const response = await fetch(`/api/chat/documents/upload?${query}`, { method: 'POST', credentials: 'same-origin', body: file });
  const value = await response.json().catch(() => ({})) as { error?: string };
  if (!response.ok) throw new WorkspaceApiError(response.status, value.error || 'unavailable');
  return value as Paper;
}

export async function streamTurn(id: string, body: unknown, signal: AbortSignal, onEvent: (event: WorkspaceEvent) => void) {
  const response = await fetch(`/api/chat/conversations/${id}/turns`, { method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
  if (!response.ok) {
    const value = await response.json().catch(() => ({})) as { error?: string };
    throw new WorkspaceApiError(response.status, value.error || 'unavailable');
  }
  if (!response.body) throw new Error('没有收到响应，请刷新对话检查保存状态。');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let split: number;
      while ((split = buffer.indexOf('\n\n')) >= 0) {
        const record = buffer.slice(0, split); buffer = buffer.slice(split + 2);
        const data = record.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5)).join('\n');
        if (data) onEvent(JSON.parse(data) as WorkspaceEvent);
      }
      if (done) break;
    }
  } finally { reader.releaseLock(); }
}

export function saveDownload(text: string, name: string, type = 'application/json') {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement('a'); link.href = url; link.download = name;
  document.body.append(link); link.click(); link.remove(); URL.revokeObjectURL(url);
}
