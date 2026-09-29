import { useState } from 'react';
import type { JournalKind } from '../plans/journalApi';
import { WorkspaceDialog } from './WorkspaceDialog';
import { workspaceRequest, type Conversation, type WorkspaceMessage } from './workspaceApi';

export function ChatJournalDialog({ conversation, message, onClose }: { conversation: Conversation; message: WorkspaceMessage; onClose: () => void }) {
  const [title, setTitle] = useState(conversation.title.slice(0, 160));
  const [body, setBody] = useState(message.content.slice(0, 12000));
  const [kind, setKind] = useState<JournalKind>(message.citations.length ? 'paper' : 'ai_storage');
  const [next, setNext] = useState('');
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [saved, setSaved] = useState(false);
  return <WorkspaceDialog title="保存到学习日记" onClose={onClose}><form className="workspace-form" onSubmit={event => {
    event.preventDefault(); setBusy(true); setFeedback('');
    void workspaceRequest<{ already_saved?: boolean }>(`/conversations/${conversation.id}/journal`, 'POST', { message_id: message.id,
      draft: { kind, title, body, next_check: next, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai', occurred_at: new Date().toISOString(),
        source: message.citations.map(c => `${c.title} · 第 ${c.page} 页 [${c.id}]`).join('\n').slice(0, 2000) } })
      .then(result => { setSaved(true); setFeedback(result.already_saved ? '这条回答之前已保存，请到日记中继续编辑。' : '已保存，可在日记中继续补充自己的理解。'); })
      .catch(e => setFeedback(String(e.message))).finally(() => setBusy(false));
  }}><p className="workspace-muted">把你认可的内容留下，也可以改写成自己的理解。保存后会保留原对话链接。</p>
    {message.content.length > 12000 && <p role="status">回答较长，已取前 12000 字；请调整为你想记录的部分。</p>}
    <label>类型<select className="input" value={kind} onChange={e => setKind(e.target.value as JournalKind)}><option value="ai_storage">AI 与存储</option><option value="paper">论文阅读</option><option value="ielts">雅思练习</option><option value="thought">日常思考</option></select></label>
    <label>标题<input className="input" value={title} maxLength={160} onChange={e => setTitle(e.target.value)} /></label>
    <label>我的学习记录<textarea className="input" value={body} maxLength={12000} rows={10} required onChange={e => setBody(e.target.value)} /></label>
    <label>下一次如何验证或复习<textarea className="input" value={next} maxLength={2000} rows={2} onChange={e => setNext(e.target.value)} /></label>
    {feedback && <p role="status">{feedback}</p>}<div className="workspace-actions"><button className="btn btn-primary" disabled={busy || saved || !body.trim()}>确认保存到日记</button><a href="/admin/plans?tab=journal" className="btn btn-secondary">查看日记</a></div>
  </form></WorkspaceDialog>;
}
