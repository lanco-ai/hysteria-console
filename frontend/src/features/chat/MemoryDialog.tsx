import { useState } from 'react';
import { WorkspaceDialog } from './WorkspaceDialog';
import { workspaceRequest as api, type LearningProject, type Conversation, type WorkspaceMessage } from './workspaceApi';

export function MemoryDialog({ project, source, onClose, onSaved }: { project: LearningProject; source?: { conversation: Conversation; message: WorkspaceMessage }; onClose: () => void; onSaved: (p: LearningProject) => void }) {
  const [text, setText] = useState(source?.message.content.slice(0, 800) || '');
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return <WorkspaceDialog title="项目长期记忆" onClose={onClose}>
    <p>保存你已确认的目标、理解或易错点。此项目的后续对话会把这些文字提供给模型，你可以随时修改或删除。</p>
    {(project.memories || []).map(m => <section className="chat-citation" key={m.id}><p>{m.text}</p><div className="workspace-actions">{m.source && <a href={`/admin/chat?conversation=${m.source.conversation_id}`}>来源对话</a>}<button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => { setEditing(m.id); setText(m.text); }}>修改</button><button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => { if (!window.confirm('删除这条项目记忆？')) return; setBusy(true); void api<LearningProject>(`/projects/${project.id}/memories/${m.id}`, 'DELETE', { revision: project.revision }).then(onSaved).catch(e => setError(String(e.message))).finally(() => setBusy(false)); }}>删除</button></div></section>)}
    <form className="workspace-form" onSubmit={e => { e.preventDefault(); setBusy(true); setError(''); void api<LearningProject>(`/projects/${project.id}/memories${editing ? `/${editing}` : ''}`, editing ? 'PATCH' : 'POST', { revision: project.revision, text: text.trim(), ...(!editing && source ? { source_conversation_id: source.conversation.id, source_message_id: source.message.id } : {}) }).then(p => { onSaved(p); setText(''); setEditing(null); }).catch(e => setError(String(e.message))).finally(() => setBusy(false)); }}>
      <label>{editing ? '修改记忆' : '确认并保存记忆'}<textarea className="input" aria-label="项目记忆内容" value={text} maxLength={800} rows={5} onChange={e => setText(e.target.value)} /></label>
      <small>每个项目最多 10 条，每条最多 800 字。</small>
      <div className="workspace-actions"><button className="btn btn-primary" disabled={busy || !text.trim()}>保存记忆</button>{editing && <button className="btn btn-secondary" type="button" onClick={() => { setEditing(null); setText(''); }}>取消修改</button>}</div>
    </form>{error && <p className="err" role="alert">{error}</p>}
  </WorkspaceDialog>;
}
