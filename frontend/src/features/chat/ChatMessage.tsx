import { useState, type ReactNode } from 'react';
import type { ChatMessageData } from './chatApi';

function safeHref(value: string): string | null {
  try {
    const url = new URL(value, window.location.origin);
    if (url.protocol === 'http:' || url.protocol === 'https:' || url.protocol === 'mailto:') return url.href;
  } catch {
    // Invalid and unsupported links remain plain text.
  }
  return null;
}

function inlineMarkdown(value: string, prefix: string): ReactNode[] {
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^\s)]+\))/g;
  const output: ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(value))) {
    if (match.index > cursor) output.push(value.slice(cursor, match.index));
    const token = match[0];
    if (token.startsWith('**')) {
      output.push(<strong key={`${prefix}-strong-${match.index}`}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('`')) {
      output.push(<code key={`${prefix}-inline-${match.index}`}>{token.slice(1, -1)}</code>);
    } else {
      const link = token.match(/^\[([^\]]+)\]\(([^\s)]+)\)$/);
      const href = link ? safeHref(link[2] || '') : null;
      output.push(href ? <a key={`${prefix}-link-${match.index}`} href={href} target="_blank" rel="noreferrer">{link?.[1] || ''}</a> : token);
    }
    cursor = match.index + token.length;
  }
  if (cursor < value.length) output.push(value.slice(cursor));
  return output;
}

function markdownBlocks(value: string, prefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const paragraph: string[] = [];
  const list: string[] = [];
  const flushParagraph = () => {
    if (!paragraph.length) return;
    const key = `${prefix}-p-${nodes.length}`;
    nodes.push(<p key={key}>{inlineMarkdown(paragraph.join('\n'), key)}</p>);
    paragraph.length = 0;
  };
  const flushList = () => {
    if (!list.length) return;
    const key = `${prefix}-ul-${nodes.length}`;
    nodes.push(<ul key={key}>{list.map((item, index) => <li key={`${key}-${index}`}>{inlineMarkdown(item, `${key}-${index}`)}</li>)}</ul>);
    list.length = 0;
  };
  value.split('\n').forEach((line, index) => {
    const item = line.match(/^\s*[-*]\s+(.+)$/);
    const heading = line.match(/^\s*(#{1,3})\s+(.+)$/);
    if (!line.trim()) {
      flushParagraph();
      flushList();
    } else if (item) {
      flushParagraph();
      list.push(item[1] || '');
    } else if (heading) {
      flushParagraph();
      flushList();
      const headingMarks = heading[1] || '';
      const headingText = heading[2] || '';
      const Tag = headingMarks.length === 1 ? 'h3' : 'h4';
      nodes.push(<Tag key={`${prefix}-heading-${index}`}>{inlineMarkdown(headingText, `${prefix}-heading-${index}`)}</Tag>);
    } else {
      flushList();
      paragraph.push(line);
    }
  });
  flushParagraph();
  flushList();
  return nodes;
}

function renderMarkdown(value: string): ReactNode[] {
  return value.split('```').map((part, index) => index % 2 === 1
    ? <pre key={`code-${index}`}><code>{part.replace(/^[\w+#.-]+\n/, '')}</code></pre>
    : <span className="chat-markdown-block" key={`text-${index}`}>{markdownBlocks(part, `text-${index}`)}</span>);
}

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
    <div className="chat-message-meta"><span>{message.role === 'user' ? '你' : message.role === 'system' ? '系统' : 'Lanco AI'}</span>
      {message.role === 'assistant' ? <button type="button" className="btn btn-ghost btn-sm" onClick={copy}>{copied ? '已复制' : '复制'}</button> : null}</div>
    <div className="chat-message-content">{renderMarkdown(message.content)}</div>
  </article>;
}
