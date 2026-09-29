import { useEffect, useState } from 'react';
import { WorkspaceDialog } from './WorkspaceDialog';
import { workspaceRequest as api, WorkspaceApiError, type Conversation, type Paper } from './workspaceApi';

export type ToolArtifact = { id: string; name: string; media_type: string; size: number };
export type ToolRun = { id: string; tool: string; name: string; status: string; arguments: Record<string, unknown>; result?: Record<string, unknown>; error?: string; artifacts?: ToolArtifact[] };
type Tool = { id: string; name: string; description?: string; server_name?: string; inputSchema?: Record<string, unknown> };
type Server = { id: string; name: string; url: string; allow_private: boolean; token_configured: boolean; revision: number; tools: unknown[] };

export function ToolOutput({ result, artifacts = [] }: { result?: Record<string, unknown> | undefined; artifacts?: ToolArtifact[] | undefined }) {
  const search = Array.isArray(result?.results) ? result.results as { title: string; url: string; snippet: string }[] : null;
  return <div className="workspace-tool-output">
    {search ? search.map((r, i) => <div key={i}><a href={/^https?:\/\//.test(r.url) ? r.url : undefined} target="_blank" rel="noreferrer">{r.title}</a><p>{r.snippet}</p></div>) : result && <pre>{typeof result.stdout === 'string' ? result.stdout + (result.error ? `\n${String(result.error)}` : '') : JSON.stringify(result, null, 2)}</pre>}
    {artifacts.map(a => <div key={a.id}>{a.media_type === 'image/png' && <img loading="lazy" src={`/api/chat/tools/artifacts/${a.id}`} alt={a.name} />}<a href={`/api/chat/tools/artifacts/${a.id}`} download={a.name}>{a.name} · {Math.ceil(a.size / 1024)} KB</a></div>)}
  </div>;
}

export function ToolsDialog({ conversation, question, model, papers, attached, onClose, onAttach }: { conversation: Conversation; question: string; model: string; papers: Paper[]; attached: string[]; onClose: () => void; onAttach: (ids: string[]) => void }) {
  const [catalog, setCatalog] = useState<Tool[]>([]);
  const [runs, setRuns] = useState<ToolRun[]>([]);
  const [servers, setServers] = useState<Server[]>([]);
  const [connections, setConnections] = useState(false);
  const [tool, setTool] = useState('web_search');
  const [text, setText] = useState(question);
  const [code, setCode] = useState('import numpy as np\nprint(np.mean([1, 2, 3]))');
  const [argumentsText, setArgumentsText] = useState('{}');
  const [files, setFiles] = useState<string[]>([]);
  const [enabled, setEnabled] = useState(['web_search', 'web_read', 'python']);
  const [selected, setSelected] = useState(attached);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [serverEdit, setServerEdit] = useState<Server | null>(null);
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [token, setToken] = useState('');
  const [clearToken, setClearToken] = useState(false);
  const [privateNetwork, setPrivateNetwork] = useState(false);
  const report = (e: unknown) => setError(e instanceof Error ? e.message : '操作失败，请稍后重试。');
  const refresh = async () => {
    const [c, r, s] = await Promise.all([api<{ items: Tool[] }>('/tools/catalog'), api<{ items: ToolRun[] }>(`/conversations/${conversation.id}/tools`), api<{ items: Server[] }>('/tools/servers')]);
    setCatalog(c.items); setRuns(r.items); setServers(s.items);
  };
  useEffect(() => { let live = true; void Promise.all([api<{ items: Tool[] }>('/tools/catalog'), api<{ items: ToolRun[] }>(`/conversations/${conversation.id}/tools`), api<{ items: Server[] }>('/tools/servers')]).then(([c, r, s]) => { if (live) { setCatalog(c.items); setRuns(r.items); setServers(s.items); } }).catch(e => { if (live) report(e); }); return () => { live = false; }; }, [conversation.id]);
  const act = async (action: () => Promise<void>) => { setBusy(true); setError(''); setNotice(''); try { await action(); } catch (e) { report(e); } finally { setBusy(false); } };
  const current = catalog.find(t => t.id === tool);
  return <WorkspaceDialog title="搜索与工具" onClose={() => { if (!busy) onClose(); }}>
    <div className="workspace-actions"><button className={`btn ${connections ? 'btn-ghost' : 'btn-secondary'}`} disabled={busy} onClick={() => setConnections(false)}>本次工具</button><button className={`btn ${connections ? 'btn-secondary' : 'btn-ghost'}`} disabled={busy} onClick={() => setConnections(true)}>MCP 连接</button></div>
    {connections ? <>
      <p>连接使用 Streamable HTTP 的 MCP 服务。凭据只保存在服务器，不会发给聊天模型。调用前会显示工具名称和参数，需你确认执行。</p>
      {servers.map(s => <section className="chat-citation" key={s.id}><strong>{s.name}</strong><p>{s.tools.length} 个已发现工具 · {s.token_configured ? '已配置凭据' : '无凭据'}</p><div className="workspace-actions"><button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => { void act(async () => { await api(`/tools/servers/${s.id}/discover`, 'POST', {}); await refresh(); setNotice('工具列表已更新。'); }); }}>连接并发现工具</button><button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => { setServerEdit(s); setName(s.name); setUrl(s.url); setPrivateNetwork(s.allow_private); setToken(''); setClearToken(false); }}>修改连接</button><button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => { if (!window.confirm('删除这个 MCP 连接？')) return; void act(async () => { await api(`/tools/servers/${s.id}`, 'DELETE', { revision: s.revision }); await refresh(); }); }}>删除</button></div></section>)}
      <form className="workspace-form" onSubmit={e => { e.preventDefault(); void act(async () => { await api(`/tools/servers${serverEdit ? `/${serverEdit.id}` : ''}`, serverEdit ? 'PUT' : 'POST', { name, url, allow_private: privateNetwork, ...(token || clearToken ? { token: clearToken ? '' : token } : {}), ...(serverEdit ? { revision: serverEdit.revision } : {}) }); setName(''); setUrl(''); setToken(''); setServerEdit(null); setClearToken(false); await refresh(); setNotice('连接已保存，请点击“连接并发现工具”。'); }); }}>
        <h3>{serverEdit ? '修改 MCP 连接' : '新增 MCP 连接'}</h3>
        <label>名称<input className="input" required maxLength={80} value={name} onChange={e => setName(e.target.value)} /></label>
        <label>MCP 地址<input className="input" required type="url" value={url} maxLength={2048} onChange={e => setUrl(e.target.value)} placeholder="https://example.com/mcp" autoComplete="off" /></label>
        <label>Bearer 凭据{serverEdit && '（留空保留）'}<input className="input" type="password" value={token} maxLength={4096} onChange={e => setToken(e.target.value)} autoComplete="new-password" /></label>
        {serverEdit?.token_configured && <label><input type="checkbox" checked={clearToken} onChange={e => setClearToken(e.target.checked)} />清除已保存凭据</label>}
        <label><input type="checkbox" checked={privateNetwork} onChange={e => setPrivateNetwork(e.target.checked)} />允许连接这台服务器可访问的本机 / 内网地址</label>
        <button className="btn btn-primary" disabled={busy || !name.trim() || !url.trim()}>保存连接</button>{serverEdit && <button type="button" className="btn btn-ghost" onClick={() => { setServerEdit(null); setName(''); setUrl(''); setToken(''); setClearToken(false); }}>取消修改</button>}
      </form>
    </> : <>
      <p>先查看工具要执行的内容，再确认执行。选择已完成的结果，可让 AI 结合结果回答当前问题。</p>
      <details><summary>让模型为当前问题选择工具</summary><p className="workspace-muted">{question || '请先在聊天输入框写下问题。'}</p><div className="workspace-tool-options">{catalog.map(t => <label key={t.id}><input type="checkbox" checked={enabled.includes(t.id)} disabled={busy || (!enabled.includes(t.id) && enabled.length >= 12)} onChange={e => setEnabled(current => e.target.checked ? [...current, t.id] : current.filter(id => id !== t.id))} />{t.server_name ? `${t.server_name} / ` : ''}{t.name}</label>)}</div><button className="btn btn-secondary" disabled={busy || !question.trim() || !model || !enabled.length} onClick={() => { void act(async () => { const plan = await api<{ status: string; error?: string; run_ids: string[] }>(`/conversations/${conversation.id}/tool-plans`, 'POST', { question, model, enabled, request_id: crypto.randomUUID() }); if (plan.status === 'error') throw new WorkspaceApiError(502, plan.error || 'tool_planning_failed'); await refresh(); setNotice(plan.run_ids.length ? '模型已提出调用，请逐项检查参数后确认执行。' : '模型认为这次问题无需调用工具。'); }); }}>生成工具建议</button></details>
      <form className="workspace-form" onSubmit={e => { e.preventDefault(); void act(async () => { const args = tool === 'python' ? { code, document_ids: files } : tool === 'web_search' ? { query: text } : tool === 'web_read' ? { url: text } : JSON.parse(argumentsText) as unknown; await api(`/conversations/${conversation.id}/tools`, 'POST', { tool, arguments: args, request_id: crypto.randomUUID() }); await refresh(); setNotice('调用已准备好，请在下面确认执行。'); }); }}>
        <label>手动选择工具<select className="input" aria-label="工具类型" value={tool} disabled={busy} onChange={e => { setTool(e.target.value); setText(e.target.value === 'web_search' ? question : ''); setArgumentsText('{}'); }}>{catalog.map(t => <option key={t.id} value={t.id}>{t.server_name ? `${t.server_name} / ` : ''}{t.name}</option>)}</select></label>
        <p className="workspace-muted">{current?.description}</p>
        {tool === 'python' ? <><label>Python 代码<textarea className="input workspace-code-editor" aria-label="Python 代码" rows={8} value={code} maxLength={20000} onChange={e => setCode(e.target.value)} /></label><p className="workspace-muted">无网络，最多 40 秒、192 MB 内存。输出文件写入 /workspace/output，可下载 PNG、CSV、TXT、JSON、PDF。</p>{papers.length > 0 && <details><summary>提供项目文件（最多 4 份，共 16 MB）</summary>{papers.map(p => <label className="workspace-tool-file" key={p.id}><input type="checkbox" checked={files.includes(p.id)} disabled={!files.includes(p.id) && files.length >= 4} onChange={e => setFiles(current => e.target.checked ? [...current, p.id] : current.filter(id => id !== p.id))} />{p.title}<small>/workspace/input/{p.id}.{p.title.split('.').pop()}</small></label>)}</details>}</> : tool.startsWith('mcp:') ? <><label>调用参数（JSON）<textarea className="input workspace-code-editor" aria-label="MCP 调用参数" rows={6} value={argumentsText} maxLength={32000} onChange={e => setArgumentsText(e.target.value)} /></label><details><summary>查看参数要求</summary><pre>{JSON.stringify(current?.inputSchema, null, 2)}</pre></details></> : <label>{tool === 'web_search' ? '搜索内容' : '网页地址'}<input className="input" aria-label="工具输入" value={text} maxLength={tool === 'web_search' ? 1000 : 2048} onChange={e => setText(e.target.value)} /></label>}
        <button className="btn btn-secondary" disabled={busy}>准备调用</button>
      </form>
      <h3>本次对话的工具记录</h3>
      {!runs.length && <p className="workspace-muted">还没有工具调用。</p>}
      {runs.map(r => <section className="workspace-tool-run" key={r.id}><div className="workspace-actions"><strong>{r.name}</strong><span>{({ proposed: '等待确认', running: '正在执行', completed: '已执行', error: '执行失败', uncertain: '执行结果未确认' } as Record<string, string>)[r.status]}</span></div><details open={r.status === 'proposed'}><summary>查看执行参数</summary><pre>{JSON.stringify(r.arguments, null, 2)}</pre></details>
        {r.status === 'proposed' && <><p className="workspace-muted">{r.tool.startsWith('mcp:') ? '此调用可能读取或修改外部服务的数据，请核对目标和参数。' : '确认后执行上面显示的内容。'}</p><button className="btn btn-primary" disabled={busy} onClick={() => { void act(async () => { const done = await api<ToolRun>(`/tools/runs/${r.id}/execute`, 'POST', { approved: true }); setRuns(current => current.map(x => x.id === done.id ? done : x)); if (done.status === 'completed') setSelected(current => current.includes(done.id) || current.length >= 3 ? current : [...current, done.id]); }); }}>确认执行</button></>}
        {r.status === 'uncertain' && <p>连接中断，外部操作可能已经执行。请先在该服务中核实，再决定是否发起新调用。</p>}
        {r.error && <p className="err">{new WorkspaceApiError(502, r.error).message}</p>}
        <ToolOutput result={r.result} artifacts={r.artifacts} />
        <div className="workspace-actions">{r.status === 'completed' && <label><input type="checkbox" checked={selected.includes(r.id)} disabled={!selected.includes(r.id) && selected.length >= 3} onChange={e => setSelected(current => e.target.checked ? [...current, r.id] : current.filter(id => id !== r.id))} />用于当前提问</label>}<button className="btn btn-ghost btn-sm" disabled={busy || r.status === 'running'} onClick={() => { if (!window.confirm('删除这条记录和生成文件？外部服务中的操作不会撤销。')) return; void act(async () => { await api(`/tools/runs/${r.id}`, 'DELETE'); setSelected(current => current.filter(id => id !== r.id)); await refresh(); }); }}>删除记录</button></div>
      </section>)}
      <button className="btn btn-primary" disabled={busy} onClick={() => onAttach(selected)}>附上 {selected.length} 份结果并返回对话</button>
    </>}
    {busy && <p role="status">处理中，请稍候…</p>}{notice && <p role="status">{notice}</p>}{error && <p className="err" role="alert">{error}</p>}
  </WorkspaceDialog>;
}
