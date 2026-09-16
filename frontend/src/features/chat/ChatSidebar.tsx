import type { ChatMessageData } from './chatApi';

export type ChatSession = {
  id: string;
  title?: string;
  messages: ChatMessageData[];
  updatedAt: number;
};

export type ChatUsage = {
  day: string;
  today: number;
  total: number;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
};

export type ChatSidebarProps = {
  sessions: ChatSession[];
  activeId: string;
  search: string;
  usage: ChatUsage;
  onSearch: (value: string) => void;
  onNew: () => void;
  onSelect: (id: string) => void;
  onRename: (session: ChatSession) => void;
  onDelete: (session: ChatSession) => void;
  onOpenSettings: () => void;
  onOpenUsage: () => void;
  disabled?: boolean;
};

function sessionTitle(session: ChatSession): string {
  const explicit = typeof session.title === 'string' ? session.title.trim() : '';
  if (explicit && explicit !== '新对话') return explicit;
  const first = session.messages.find(item => item.role === 'user')?.content.trim() || '';
  if (!first) return '新对话';
  return first.length > 28 ? `${first.slice(0, 28)}…` : first;
}

function dayStart(value: number) {
  const date = new Date(value);
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function sessionGroup(value: number): string {
  const days = Math.floor((dayStart(Date.now()) - dayStart(value)) / 86_400_000);
  if (days <= 0) return '今天';
  if (days === 1) return '昨天';
  if (days <= 7) return '过去 7 天';
  return '更早';
}

function formatTime(value: number) {
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function ChatSidebar({ sessions, activeId, search, usage, onSearch, onNew, onSelect, onRename, onDelete, onOpenSettings, onOpenUsage, disabled = false }: ChatSidebarProps) {
  const needle = search.trim().toLocaleLowerCase();
  const visibleSessions = needle ? sessions.filter(session => sessionTitle(session).toLocaleLowerCase().includes(needle)) : sessions;
  const groups = new Map<string, ChatSession[]>();
  for (const session of visibleSessions) {
    const group = sessionGroup(session.updatedAt);
    const items = groups.get(group) || [];
    items.push(session);
    groups.set(group, items);
  }
  const groupedSessions = ['今天', '昨天', '过去 7 天', '更早']
    .map(label => ({ label, items: groups.get(label) || [] }))
    .filter(group => group.items.length > 0);

  return <div className="chat-history" aria-label="对话历史">
    <button className="btn btn-primary chat-new-button" type="button" onClick={onNew} disabled={disabled}>＋ 新对话</button>
    <label className="chat-search"><span className="sr-only">搜索对话</span><span aria-hidden="true">⌕</span><input type="search" value={search} onChange={event => onSearch(event.target.value)} placeholder="搜索对话" disabled={disabled} /></label>
    <div className="chat-session-list">
      {groupedSessions.length ? groupedSessions.map(group => <section className="chat-session-group" key={group.label}><h2>{group.label}</h2>{group.items.map(session => <div className={`chat-session-row${session.id === activeId ? ' active' : ''}`} key={session.id}><button className="chat-session" type="button" onClick={() => onSelect(session.id)} disabled={disabled}><span>{sessionTitle(session)}</span><small>{formatTime(session.updatedAt)}</small></button><div className="chat-session-actions"><button type="button" aria-label={`重命名 ${sessionTitle(session)}`} onClick={() => onRename(session)} disabled={disabled}>…</button><button type="button" aria-label={`删除 ${sessionTitle(session)}`} onClick={() => onDelete(session)} disabled={disabled}>×</button></div></div>)}</section>) : <p className="chat-sidebar-empty">{search ? '没有匹配的对话' : '还没有会话'}</p>}
    </div>
    <div className="chat-sidebar-footer"><button className="chat-sidebar-link" type="button" onClick={onOpenUsage} disabled={disabled}><span>AI 用量</span><small>{usage.today} 次 · 今日</small></button><button className="chat-sidebar-link" type="button" onClick={onOpenSettings} disabled={disabled}><span>API 设置</span><small>管理连接配置</small></button></div>
  </div>;
}
