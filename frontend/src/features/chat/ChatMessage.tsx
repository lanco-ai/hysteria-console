import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import type { ChatMessageData } from './chatApi';
import type { WorkspaceMessage } from './workspaceApi';

export function ChatMessage({ message, pending = false, onJournal }: { message: ChatMessageData | WorkspaceMessage; pending?: boolean; onJournal?: () => void }) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const citations = 'citations' in message ? message.citations : [];
  const status = 'status' in message ? message.status : 'completed';
  return <article className={`chat-message chat-message-${message.role}`}>
    <div className="chat-message-heading"><strong>{message.role === 'user' ? '你' : message.role === 'system' ? '系统' : 'Lanco AI'}</strong><div className="workspace-actions">
      {message.content && <button type="button" className="btn btn-ghost btn-sm" onClick={() => { void navigator.clipboard.writeText(message.content).then(() => { setCopied(true); setCopyError(false); }).catch(() => setCopyError(true)); }}>{copyError ? '复制失败，请手动选择' : copied ? '已复制' : '复制'}</button>}
      {onJournal && message.content && status !== 'streaming' && <button type="button" className="btn btn-ghost btn-sm" onClick={onJournal}>存入学习日记</button>}
    </div></div>
    <div className={`chat-message-content${pending ? ' chat-message-pending' : ''}`}>{pending ? <span>模型正在思考…</span> : <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[[rehypeKatex, { strict: 'ignore', trust: false, maxExpand: 1000 }]]} skipHtml components={{
      a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
      img: ({ alt }) => <span>[图片：{alt || '未加载外部图片'}]</span>,
    }}>{message.content}</ReactMarkdown>}</div>
    {citations.length > 0 && <details className="chat-citations"><summary>参考原文 · {citations.length} 个片段</summary>{citations.map(c => <div key={c.id} className="chat-citation">
      <a href={`/api/chat/documents/${encodeURIComponent(c.document_id)}/file#page=${c.page}`} target="_blank" rel="noreferrer">[{c.id}] {c.title} · 第 {c.page} 页 ↗</a><blockquote>{c.quote}</blockquote>
    </div>)}<small>页码是 PDF 文件页序。片段已随回答保存；原文件删除后无法打开。引用由模型生成，请核对原文。</small></details>}
    {'context_truncated' in message && message.context_truncated && <p className="workspace-muted">此次回答只使用了最近的一部分对话，完整历史仍已保存。</p>}
    {status !== 'completed' && status !== 'streaming' && <p className="workspace-muted">{status === 'stopped' ? '已停止生成' : '回答未完成'} · 已保留收到的内容</p>}
  </article>;
}
