import { useEffect, useState } from 'react';
import { WorkspaceDialog } from './WorkspaceDialog';
import { workspaceRequest as api, type Citation, type Paper } from './workspaceApi';

type Status = { available: boolean; indexed: number; documents: number; pending: number; failed: number };
export function KnowledgeDialog({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const [query, setQuery] = useState('');
  const [scope, setScope] = useState(projectId ? 'project' : 'all');
  const [items, setItems] = useState<Citation[]>([]);
  const [documents, setDocuments] = useState<Paper[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    const refresh = () => { void Promise.all([api<Status>('/knowledge/status'), api<{ items: Paper[] }>('/knowledge/documents')]).then(([s, d]) => { if (live) { setStatus(s); setDocuments(d.items); } }).catch(e => { if (live) setError(String(e.message)); }); };
    refresh(); const timer = setInterval(refresh, 5000);
    return () => { live = false; clearInterval(timer); };
  }, []);
  return <WorkspaceDialog title="个人知识库" onClose={onClose}>
    <p>按意思检索已有资料，支持中英文跨语言查找。索引在服务器本地生成；这里的搜索不会调用聊天模型。</p>
    {status && <p className="workspace-muted">已索引 {status.indexed} / {status.documents} 份 · 待处理 {status.pending} · 失败 {status.failed}{!status.available && ' · 本地语义模型未就绪'}</p>}
    <form className="workspace-form" onSubmit={e => { e.preventDefault(); setBusy(true); setError(''); void api<{ items: Citation[] }>('/knowledge/search', 'POST', { query, project_id: scope === 'project' ? projectId : null }).then(r => { setItems(r.items); setSearched(true); }).catch(e => setError(String(e.message))).finally(() => setBusy(false)); }}>
      <select className="input" aria-label="知识库搜索范围" value={scope} onChange={e => setScope(e.target.value)}><option value="all">全部个人资料</option>{projectId && <option value="project">当前项目</option>}</select>
      <input className="input" aria-label="搜索知识库" value={query} maxLength={2000} onChange={e => setQuery(e.target.value)} placeholder="例如：怎样减少磁盘写入开销？" />
      <button className="btn btn-primary" disabled={busy || !query.trim() || !status?.available}>{busy ? '正在检索…' : '语义搜索'}</button>
    </form>
    {error && <p className="err" role="alert">{error}</p>}
    {items.map(c => <section className="chat-citation" key={c.id}><a href={`/api/chat/documents/${c.document_id}/file#page=${c.page}`} target="_blank" rel="noreferrer">{c.title} · {c.location_kind === 'page' ? `第 ${c.page} 页` : `第 ${c.page} 部分`}</a><blockquote>{c.quote}</blockquote></section>)}
    {searched && !items.length && <p>这个范围内还没有可检索的文字索引。请检查资料处理状态。</p>}
    <details><summary>资料索引状态</summary>{documents.map(d => <div className="workspace-paper" key={d.id}><span>{d.title} · {d.index_status === 'ready' ? `已索引 ${d.chunk_count || 0} 个片段` : d.index_status === 'error' ? '索引失败' : d.status === 'error' ? '解析失败' : '等待处理'}</span>{(!d.status || d.status === 'ready') && <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => { void api(`/documents/${d.id}/reindex`, 'POST').then(() => setDocuments(current => current.map(x => x.id === d.id ? { ...x, index_status: 'queued' } : x))).catch(e => setError(String(e.message))); }}>重建索引</button>}</div>)}</details>
  </WorkspaceDialog>;
}
