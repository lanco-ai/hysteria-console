import type { Ref, RefObject } from 'react';
import type { Conversation } from './workspaceApi';

type Group = { label: string; items: Conversation[] };

const DAY = 86_400_000;
const ORDER = ['今天', '昨天', '近 7 天', '近 30 天', '更早'];

function dayStart(value: number) {
  const date = new Date(value);
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/** Groups conversations by how recently they changed, newest group first. */
export function groupConversations(items: Conversation[], now = Date.now()): Group[] {
  const today = dayStart(now);
  const groups = new Map<string, Conversation[]>();
  for (const item of items) {
    const days = Math.round((today - dayStart(item.updatedAt)) / DAY);
    const label = days <= 0 ? '今天' : days === 1 ? '昨天' : days <= 7 ? '近 7 天' : days <= 30 ? '近 30 天' : '更早';
    groups.set(label, [...(groups.get(label) ?? []), item]);
  }
  return ORDER.filter(label => groups.has(label)).map(label => ({ label, items: groups.get(label) ?? [] }));
}

function when(value: number, label: string) {
  const date = new Date(value);
  if (label === '今天' || label === '昨天') return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日`;
}

export function ConversationSidebar({ panelRef, sessions, activeId, search, disabled, projectName, projectGoal, projectButtonRef, projectSelectorOpen, onSearch, onNew, onSelect, onRename, onDelete, onPickProject, onCreateProject, onCollapse }: {
  panelRef: Ref<HTMLElement>;
  sessions: Conversation[];
  activeId: string;
  search: string;
  disabled: boolean;
  projectName: string;
  projectGoal: string;
  projectButtonRef: RefObject<HTMLButtonElement | null>;
  projectSelectorOpen: boolean;
  onSearch: (value: string) => void;
  onNew: () => void;
  onSelect: (id: string) => void;
  onRename: (item: Conversation) => void;
  onDelete: (item: Conversation) => void;
  onPickProject: () => void;
  onCreateProject: () => void;
  onCollapse: () => void;
}) {
  const groups = groupConversations(sessions);
  return <aside ref={panelRef} className="chat-sidebar chat-history-panel" aria-label="对话列表" tabIndex={-1}>
    <div className="chat-sidebar-head">
      <button className="btn btn-primary chat-new" type="button" disabled={disabled} onClick={onNew}>
        <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4.5v11M4.5 10h11"/></svg>新对话
      </button>
      <button className="chat-icon-button" type="button" aria-label="收起对话列表" title="收起对话列表" onClick={onCollapse}>
        <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M12.5 5 7.5 10l5 5"/></svg>
      </button>
    </div>
    <div className="chat-sidebar-project">
      <button ref={projectButtonRef} type="button" className="workspace-project-trigger" aria-label={`选择学习项目：${projectName || '自由对话'}`} aria-haspopup="dialog" aria-expanded={projectSelectorOpen} disabled={disabled} onClick={onPickProject}>
        <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M2.5 6a1.5 1.5 0 0 1 1.5-1.5h4l2 2h6a1.5 1.5 0 0 1 1.5 1.5v7a1.5 1.5 0 0 1-1.5 1.5H4A1.5 1.5 0 0 1 2.5 15Z"/></svg>
        <span>{projectName || '自由对话'}</span>
        <span className="workspace-project-chevron" aria-hidden="true">⌄</span>
      </button>
      <button className="btn btn-ghost btn-sm" type="button" disabled={disabled} onClick={onCreateProject}>＋ 项目</button>
      {projectGoal ? <p className="workspace-goal" title={projectGoal}>{projectGoal}</p> : null}
    </div>
    <label className="chat-search">
      <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="9" cy="9" r="5.5"/><path d="m13.2 13.2 3.3 3.3"/></svg>
      <input type="search" aria-label="搜索对话" placeholder="搜索标题和正文" value={search} onChange={event => onSearch(event.target.value)}/>
    </label>
    <nav className="chat-session-list" aria-label="对话历史">
      {groups.length ? groups.map(group => <section className="chat-session-group" key={group.label} aria-label={group.label}>
        <h3>{group.label}</h3>
        {group.items.map(item => <div className={`chat-session-row${item.id === activeId ? ' active' : ''}`} key={item.id}>
          <button className="chat-session" type="button" disabled={disabled} aria-current={item.id === activeId ? 'page' : undefined} title={`${item.title} · ${item.message_count ?? item.messages?.length ?? 0} 条消息`} onClick={() => onSelect(item.id)}>
            <span className="chat-session-title">{item.title}</span>
            <small>{when(item.updatedAt, group.label)}</small>
          </button>
          <div className="chat-session-actions">
            <button type="button" aria-label={`重命名 ${item.title}`} title="重命名" disabled={disabled} onClick={() => onRename(item)}>
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m4 13.8-.5 2.7 2.7-.5 8.6-8.6-2.2-2.2Z"/></svg>
            </button>
            <button type="button" aria-label={`删除 ${item.title}`} title="删除" disabled={disabled} onClick={() => onDelete(item)}>
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5.5 5.5 14.5 14.5M14.5 5.5l-9 9"/></svg>
            </button>
          </div>
        </div>)}
      </section>) : <p className="chat-sidebar-empty">{search ? '没有匹配的对话' : '还没有对话，从右侧开始提问。'}</p>}
    </nav>
    <p className="workspace-muted chat-sidebar-foot">{sessions.length} 个对话 · 记录保存在服务器</p>
  </aside>;
}
