import { useState } from 'react';
import type { ChatMessageData } from './chatApi';

export function ChatMessage({ message }: { message: ChatMessageData }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  };
  const parts = message.content.split('```');
  return <article className={`chat-message chat-message-${message.role}`}>
    <div className="chat-message-meta"><span>{message.role === 'user' ? '你' : message.role === 'system' ? '系统' : 'Lanco AI'}</span>
      {message.role === 'assistant' ? <button type="button" className="btn btn-ghost btn-sm" onClick={copy}>{copied ? '已复制' : '复制'}</button> : null}
    </div>
    <div className="chat-message-content">{parts.map((part, index) => index % 2 === 1
      ? <pre key={`code-${index}`}><code>{part.replace(/^[\w+#.-]+\n/, '')}</code></pre>
      : <span key={`text-${index}`}>{part}</span>)}</div>
  </article>;
}
