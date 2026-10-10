import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import type { ChatMessageData } from './chatApi';
import type { WorkspaceMessage } from './workspaceApi';
import { ToolOutput } from './ToolsDialog';

export function ChatMessage({ message, pending = false, onJournal, onMemory, onBranch, disabled = false }: { message: ChatMessageData | WorkspaceMessage; pending?: boolean; onJournal?: () => void; onMemory?: () => void; onBranch?: (mode: 'edit' | 'regenerate' | 'continue') => void; disabled?: boolean }) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const citations = 'citations' in message ? message.citations : [];
  const status = 'status' in message ? message.status : 'completed';
  const author = message.role === 'user' ? '你' : message.role === 'system' ? '系统' : 'Lanco AI';
  const actions = <>
    {message.content && <button type="button" className="btn btn-ghost btn-sm" onClick={() => { void navigator.clipboard.writeText(message.content).then(() => { setCopied(true); setCopyError(false); }).catch(() => setCopyError(true)); }}>{copyError ? '复制失败，请手动选择' : copied ? '已复制' : '复制'}</button>}
    {onJournal && message.content && status !== 'streaming' && <button type="button" className="btn btn-ghost btn-sm" onClick={onJournal}>保存到记录</button>}
    {onMemory && message.content && status === 'completed' && <button className="btn btn-ghost btn-sm" disabled={disabled} onClick={onMemory}>记入项目</button>}
    {onBranch && status !== 'streaming' && <>
      {message.role === 'user' && <button className="btn btn-ghost btn-sm" disabled={disabled} onClick={() => onBranch('edit')}>编辑问题</button>}
      {message.role === 'assistant' && <button className="btn btn-ghost btn-sm" disabled={disabled} onClick={() => onBranch('regenerate')}>重新生成</button>}
      {status === 'completed' && <button className="btn btn-ghost btn-sm" disabled={disabled} onClick={() => onBranch('continue')}>从这里分支</button>}
    </>}
  </>;
  return <article className={`chat-message chat-message-${message.role}`} aria-label={author}>
    {message.role === 'user' ? null : <div className="chat-message-heading"><span className="chat-message-avatar" aria-hidden="true">✦</span><strong>{author}</strong></div>}
    {'attachments' in message && !!message.attachments?.length && <div className="workspace-message-attachments">{message.attachments.map(a => <a key={a.id} href={`/api/chat/documents/${a.id}/file`} target="_blank" rel="noreferrer">{a.has_image && <img src={`/api/chat/documents/${a.id}/file`} alt={a.title} loading="lazy" />}<span>{a.title}</span></a>)}</div>}
    <div className={`chat-message-content${pending ? ' chat-message-pending' : ''}`}>{pending ? <span>模型正在思考…</span> : <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[[rehypeKatex, { strict: 'ignore', trust: false, maxExpand: 1000 }]]} skipHtml components={{
      a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
      img: ({ alt }) => <span>[图片：{alt || '未加载外部图片'}]</span>,
    }}>{message.content}</ReactMarkdown>}</div>
    {citations.length > 0 && <details className="chat-citations"><summary>参考原文 · {citations.length} 个片段</summary>{citations.map(c => <div key={c.id} className="chat-citation">
      <a href={`/api/chat/documents/${encodeURIComponent(c.document_id)}/file#page=${c.page}`} target="_blank" rel="noreferrer">[{c.id}] {c.title} · {c.location_kind === 'section' ? `第 ${c.page} 部分` : `第 ${c.page} 页`} ↗</a><blockquote>{c.quote}</blockquote>
    </div>)}<small>页码是 PDF 文件页序。片段已随回答保存；原文件删除后无法打开。引用由模型生成，请核对原文。</small></details>}
    {'tool_results' in message && !!message.tool_results?.length && <details className="chat-citations"><summary>本次工具结果 · {message.tool_results.length}</summary>{message.tool_results.map(r => <section key={r.id}><strong>{r.name}</strong><ToolOutput result={r.result} artifacts={r.artifacts} /></section>)}</details>}
    {'context_truncated' in message && message.context_truncated && <p className="workspace-muted">{message.context_summary_used ? '本次使用了早期对话摘要和近期消息。' : '此次回答只使用了最近的一部分对话。'}{message.context_summary_incomplete && '部分早期内容尚未汇总。'}完整历史仍已保存，可在“长期上下文”中查看摘要。</p>}
    {status !== 'completed' && status !== 'streaming' && <p className="workspace-muted">{status === 'stopped' ? '已停止生成' : '回答未完成'} · 已保留收到的内容</p>}
    <div className="workspace-actions chat-message-actions">{actions}</div>
  </article>;
}
