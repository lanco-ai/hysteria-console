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
  return <article className={`chat-message chat-message-${message.role}`}>
    <div className="chat-message-meta"><span>{message.role === 'user' ? '你' : 'AI'}</span>
      {message.role === 'assistant' ? <button type="button" className="btn btn-ghost btn-sm" onClick={copy}>{copied ? '已复制' : '复制'}</button> : null}
    </div>
    <div className="chat-message-content">{message.content}</div>
  </article>;
}
