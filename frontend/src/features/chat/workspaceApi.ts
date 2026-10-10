import type { ToolArtifact } from './ToolsDialog';
import type { ChatMessageData, ReasoningEffort } from './chatApi';

export type Citation = { id: string; document_id: string; title: string; page: number; quote: string; sha256: string; location_kind?: 'page' | 'section' };
export type WorkspaceMessage = ChatMessageData & { id: string; status: string; citations: Citation[]; usage?: Record<string, number>; context_truncated?: boolean; document_ids?: string[]; attachments?: { id: string; title: string; media_type: string; has_image?: boolean }[]; context_summary_used?: boolean; context_summary_incomplete?: boolean; tool_results?: { id: string; name: string; tool: string; result: Record<string, unknown>; artifacts: ToolArtifact[] }[] };
export type Conversation = { id: string; title: string; project_id: string | null; messages: WorkspaceMessage[]; revision: number; updatedAt: number; draft: string; model: string; reasoningEffort: ReasoningEffort; active: string | null; message_count?: number; parent_conversation_id?: string; branch_from_message_id?: string; branch_mode?: string; draft_document_ids?: string[]; draft_tool_run_ids?: string[]; summary?: { text: string; through_message_id: string; edited?: boolean; updatedAt: number } };
export type LearningProject = { id: string; name: string; goal: string; instructions: string; revision: number; memories?: { id: string; text: string; source?: { conversation_id: string; message_id: string } | null }[] };
export type Paper = { id: string; project_id: string; title: string; page_count: number; size: number; media_type: string; status?: 'queued' | 'processing' | 'ready' | 'error'; error?: string; has_image?: boolean; index_status?: 'queued' | 'processing' | 'ready' | 'error'; chunk_count?: number };
export type WorkspacePreferences = { instructions: string; default_model: string; default_reasoning: ReasoningEffort; revision: number };
export type WorkspaceUsage = { requests: number; reported_requests: number; prompt_tokens: number; completion_tokens: number };
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
  unsupported_document: '支持 PDF、DOCX、UTF-8 TXT / Markdown、PNG / JPEG / WebP。',
  document_not_ready: '所选资料仍在解析或解析失败，请查看资料状态。',
  image_count_limit: '每次最多向视觉模型发送 4 张图片。',
  image_size_limit: '单张图片不能超过 4 MB。',
  image_dimensions_limit: '图片不能超过 800 万像素，请缩小后上传。',
  ocr_page_limit: '每份 PDF 最多识别 30 页扫描内容，请拆分上传。',
  ocr_unavailable: '文字识别组件暂不可用，请检查服务部署。',
  document_processing_timeout: '资料处理超时，请拆分或缩小后重试。',
  document_expansion_limit: '文档解压后的内容过大，请拆分文件。',
  invalid_document: '资料无法解析，请检查文件格式。',
  document_no_text: '文档中未找到可读取的文字。',
  embedding_unavailable: '本地语义检索模型未就绪，请检查模型安装。',
  embedding_failed: '语义索引处理未完成，请稍后重试。',
  knowledge_busy: '正在处理另一份资料，请稍后再搜索。',
  knowledge_changed: '检索期间资料已被修改或删除，请重新发送。',
  knowledge_project_required: '请先选择学习项目，或改为检索全部个人资料。',
  tool_not_found: '这个工具未就绪，请先检查 MCP 连接和工具列表。',
  workspace_storage_low: '服务器剩余磁盘空间不足，已暂停新增文件，请先扩容或整理文件。',
  invalid_tool_arguments: '工具参数不符合要求，请核对必填项和参数类型。',
  tool_definition_changed: '工具配置已变化，请重新准备调用并核对参数。',
  tool_planning_failed: '模型没有返回可用的工具建议，请重试或手动选择工具。',
  invalid_tool_plan: '模型返回的调用格式或工具名称无效，请重新生成。',
  mcp_unavailable: '无法连接 MCP 服务，请检查地址、凭据和服务状态。',
  mcp_request_failed: 'MCP 请求未成功，请检查服务端状态。',
  mcp_invalid_response: 'MCP 服务返回的响应不符合协议。',
  mcp_protocol_unsupported: '这个 MCP 协议版本暂不支持。',
  mcp_query_credentials_unsupported: '请使用不含查询参数的 MCP 地址，并在 Bearer 凭据栏填写凭据。',
  private_network_blocked: '该地址指向内网；网页读取只允许公网地址。',
  https_required: '公开服务需要使用 HTTPS 地址。',
  web_search_unavailable: '搜索服务暂时不可用，请稍后重试。',
  web_read_failed: '未能读取网页正文，请检查地址或稍后重试。',
  python_unavailable: 'Python 沙盒未能运行，请检查沙盒镜像和服务状态。',
  python_timeout: 'Python 执行超时，容器已停止，请缩小任务。',
  python_busy: '已有一个 Python 任务正在运行，请稍后重试。',
  python_output_limit: 'Python 输出超过限制，请减少打印内容或缩小生成文件。',
  tool_artifact_limit: '生成文件的存储空间不足，请整理工具记录。',
  invalid_tool_result: '工具结果不属于当前对话或尚未执行完成，请重新选择。',
  tool_interrupted: '执行中断，外部操作是否完成需要到原服务核实。',
  tool_failed: '工具执行失败，请核对参数后重试。',
  tools_busy: '已有工具正在执行，请稍后重试。',
  memory_limit: '每个项目最多保存 10 条记忆，请先整理已有记忆。',
  utf8_required: '文本文件需要使用 UTF-8 编码。',
  login_required: '登录已失效，请重新登录。',
  settings_incomplete: '请在服务中心检查模型配置。',
  document_storage_full: '论文存储已达到 200 MB 或 200 个文件的限制。',
  conversation_full: '这个对话已达到 500 条消息，请新建对话继续。',
  generation_failed: '生成中断，已收到的内容已保存。请检查模型服务后再试。',
  revision_conflict: 'AI 设置已在其他窗口修改，请关闭后重新打开再保存。',
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
