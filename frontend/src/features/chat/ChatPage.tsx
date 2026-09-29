import { useCallback, useEffect, useRef, useState } from 'react';
import { CodexShell } from '../../shared/CodexShell';
import { ChatMessage } from './ChatMessage';
import { ChatSettings } from './ChatSettings';
import { ChatJournalDialog } from './ChatJournalDialog';
import { ProjectDialog } from './ProjectDialog';
import { WorkspaceDialog } from './WorkspaceDialog';
import { loadChatModels, loadChatSettings, saveChatSettings, type ChatModel, type ChatSettings as Settings, type ReasoningEffort } from './chatApi';
import { workspaceRequest as api, streamTurn, uploadPaper, saveDownload, WorkspaceApiError, type Conversation, type LearningProject, type Paper, type WorkspaceMessage } from './workspaceApi';

export type ChatPageProps = { publicHost: string; authenticated?: boolean; onUnauthenticated?: () => void };
const LEGACY_KEY = 'hy2.chat.sessions.v1';
const DRAFT_KEY = 'hy2.chat.unsent.v2';

export function ChatPage({ publicHost, authenticated: authProp, onUnauthenticated }: ChatPageProps) {
  const [fallbackAuth, setFallbackAuth] = useState(false);
  const authenticated = authProp ?? fallbackAuth;
  const [sessions, setSessions] = useState<Conversation[]>([]);
  const [active, setActive] = useState<Conversation | null>(null);
  const activeRef = useRef<Conversation | null>(null);
  const [projects, setProjects] = useState<LearningProject[]>([]);
  const [projectId, setProjectId] = useState('');
  const [papers, setPapers] = useState<Paper[]>([]);
  const [selectedPapers, setSelectedPapers] = useState<string[]>([]);
  const [models, setModels] = useState<ChatModel[]>([]);
  const [model, setModel] = useState('');
  const [reasoning, setReasoning] = useState<ReasoningEffort>('auto');
  const [settings, setSettings] = useState<Settings | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [usage, setUsage] = useState<{ requests: number; reported_requests: number; prompt_tokens: number; completion_tokens: number } | null>(null);
  const [projectEditor, setProjectEditor] = useState<LearningProject | 'new' | null>(null);
  const [journalMessage, setJournalMessage] = useState<WorkspaceMessage | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [papersOpen, setPapersOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [draft, setDraft] = useState('');
  const draftRef = useRef('');
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [uploading, setUploading] = useState(false);
  const [legacy, setLegacy] = useState<unknown[]>([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [draftStatus, setDraftStatus] = useState('');
  const [draftConflict, setDraftConflict] = useState(false);
  const draftConflictRef = useRef(false);
  const [ready, setReady] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const messagesRef = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const saveQueue = useRef<Promise<void>>(Promise.resolve());
  const requestRef = useRef<{ id: string; body: Record<string, unknown> } | null>(null);
  const authRef = useRef(authenticated); authRef.current = authenticated;
  const project = projects.find(p => p.id === projectId) || null;

  const report = useCallback((e: unknown) => { if (!authRef.current) return; setError(e instanceof Error ? e.message : '操作失败，请重试。'); if (e instanceof WorkspaceApiError && e.status === 401) onUnauthenticated?.(); }, [onUnauthenticated]);
  const accept = useCallback((item: Conversation | null) => { if (item && !authRef.current) return; activeRef.current = item; setActive(item); }, []);
  const changeDraft = useCallback((value: string) => {
    draftRef.current = value; setDraft(value);
    if (authRef.current) { try { if (value === (activeRef.current?.draft || '')) localStorage.removeItem(DRAFT_KEY); else localStorage.setItem(DRAFT_KEY, JSON.stringify({ id: activeRef.current?.id || '', text: value })); } catch { /* Server save remains available. */ } }
  }, []);
  const refreshList = useCallback(async () => {
    const params = new URLSearchParams(); if (search) params.set('q', search); if (projectId) params.set('project_id', projectId);
    const result = await api<{ items: Conversation[] }>(`/conversations?${params}`);
    if (authRef.current) setSessions(result.items);
  }, [projectId, search]);
  const refreshPapers = useCallback(async () => {
    if (!projectId) { setPapers([]); return; }
    const result = await api<{ items: Paper[] }>(`/projects/${projectId}/documents`);
    setPapers(result.items);
  }, [projectId]);

  useEffect(() => { if (authProp !== undefined) return; let live = true; void fetch('/api/session', { credentials: 'same-origin' }).then(r => r.json()).then((value: { role?: string }) => { if (live) setFallbackAuth(value.role === 'admin'); }).catch(() => {}); return () => { live = false; }; }, [authProp]);
  useEffect(() => {
    if (!authenticated) { setSessions([]); accept(null); setProjects([]); setPapers([]); setLegacy([]); setProjectEditor(null); setJournalMessage(null); setSettingsOpen(false); setUsage(null); setError(''); setNotice(''); setDraftConflict(false); draftConflictRef.current = false; setDraft(''); draftRef.current = ''; setReady(false); controller.current?.abort(); return; }
    let live = true;
    void Promise.all([api<{ items: LearningProject[] }>('/projects'), api<{ items: Conversation[] }>('/conversations')]).then(async ([p, c]) => {
      if (!live) return; setProjects(p.items); setSessions(c.items);
      let savedDraft: { id?: string; text?: string } = {};
      try { savedDraft = JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}') as typeof savedDraft; } catch { /* Optional recovery. */ }
      const linked = new URLSearchParams(window.location.search).get('conversation');
      const id = linked || savedDraft.id || c.items[0]?.id;
      if (id) {
        const item = await api<Conversation>(`/conversations/${encodeURIComponent(id)}`);
        if (!live) return; accept(item); setProjectId(item.project_id || ''); if (item.model) setModel(item.model); setReasoning(item.reasoningEffort);
        changeDraft(!linked && savedDraft.id === id && typeof savedDraft.text === 'string' ? savedDraft.text : item.draft);
      } else if (typeof savedDraft.text === 'string') changeDraft(savedDraft.text);
      setReady(true);
    }).catch(e => { if (live) { report(e); setReady(true); } });
    void loadChatSettings().then(s => { if (live) setSettings(s); }).catch(report);
    void loadChatModels().then(m => { if (live) { setModels(m); setModel(value => value || m[0]?.id || ''); } }).catch(report);
    try { const data: unknown = JSON.parse(localStorage.getItem(LEGACY_KEY) || '[]'); if (Array.isArray(data)) setLegacy(data); } catch { setNotice('旧对话数据无法解析，浏览器原始记录仍保留。'); }
    return () => { live = false; controller.current?.abort(); };
  }, [authenticated, accept, changeDraft, report]);

  useEffect(() => {
    if (!authenticated || !ready) return;
    let live = true;
    const timer = setTimeout(() => {
      const query = new URLSearchParams(); if (search) query.set('q', search); if (projectId) query.set('project_id', projectId);
      void api<{ items: Conversation[] }>(`/conversations?${query}`).then(r => { if (live) setSessions(r.items); }).catch(report);
    }, 200);
    return () => { live = false; clearTimeout(timer); };
  }, [authenticated, ready, search, projectId, report]);
  useEffect(() => {
    setSelectedPapers([]); setPapers([]);
    if (!authenticated || !projectId) return;
    let live = true; void api<{ items: Paper[] }>(`/projects/${projectId}/documents`).then(r => { if (live) setPapers(r.items); }).catch(report);
    return () => { live = false; };
  }, [projectId, authenticated, report]);

  const saveDraft = useCallback(() => {
    const task = async () => {
      const item = activeRef.current; const text = draftRef.current;
      if (draftConflictRef.current) throw new Error('草稿存在冲突，请先选择保留本机草稿或使用服务器草稿。');
      if (!item || item.active || item.draft === text) return;
      setDraftStatus('保存草稿…');
      try {
        const result = await api<Conversation>(`/conversations/${item.id}`, 'PATCH', { revision: item.revision, draft: text });
        if (activeRef.current?.id === item.id) accept(result);
        setDraftStatus('草稿已保存');
        if (draftRef.current === result.draft) { try { localStorage.removeItem(DRAFT_KEY); } catch { /* Optional recovery. */ } }
      } catch (e) { setDraftStatus('草稿保留在本机，尚未同步'); if (e instanceof WorkspaceApiError && e.status === 409) { draftConflictRef.current = true; setDraftConflict(true); } throw e; }
    };
    const next = saveQueue.current.catch(() => {}).then(task); saveQueue.current = next; return next;
  }, [accept]);
  useEffect(() => { if (!ready || busy || draftConflict || !active || active.active || draft === active.draft) return; const timer = setTimeout(() => { void saveDraft().catch(report); }, 900); return () => clearTimeout(timer); }, [draft, ready, busy, draftConflict, active, saveDraft, report]);
  useEffect(() => { if (follow.current && messagesRef.current) messagesRef.current.scrollTop = messagesRef.current.scrollHeight; }, [active?.messages]);
  useEffect(() => { const warn = (event: BeforeUnloadEvent) => { if (busyRef.current || (draftRef.current && draftRef.current !== activeRef.current?.draft)) { event.preventDefault(); event.returnValue = ''; } }; window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn); }, []);

  const resolveDraft = async (keepLocal: boolean) => {
    const item = activeRef.current; if (!item) return;
    setBusy(true);
    try {
      const current = await api<Conversation>(`/conversations/${item.id}`); accept(current);
      if (current.active) throw new Error('对话还在生成，请等待或停止后再处理草稿。');
      draftConflictRef.current = false; setDraftConflict(false); setError('');
      if (keepLocal) await saveDraft(); else { changeDraft(current.draft); setDraftStatus('已使用服务器草稿'); }
    } catch (e) { report(e); } finally { setBusy(false); }
  };
  const switchTo = async (id: string) => {
    if (busyRef.current) return;
    try {
      await saveDraft(); const item = await api<Conversation>(`/conversations/${id}`);
      accept(item); changeDraft(item.draft); setProjectId(item.project_id || ''); if (item.model) setModel(item.model); setReasoning(item.reasoningEffort); setSelectedPapers([]); setHistoryOpen(false); setError(''); setNotice(''); requestRef.current = null; follow.current = true;
      window.history.replaceState(null, '', `${window.location.pathname}?conversation=${id}`);
    } catch (e) { report(e); }
  };
  const newConversation = async (nextProject = projectId) => {
    if (busyRef.current) return;
    try { await saveDraft(); if (!activeRef.current && draftRef.current.trim() && !window.confirm('新建对话会清空当前未发送的草稿，继续吗？')) return; accept(null); changeDraft(''); setProjectId(nextProject); setSelectedPapers([]); setError(''); setNotice(''); requestRef.current = null; window.history.replaceState(null, '', window.location.pathname); setHistoryOpen(false); } catch (e) { report(e); }
  };
  const send = async () => {
    if (busyRef.current || draftConflictRef.current || !authenticated || !ready || !draftRef.current.trim() || !model.trim()) return;
    busyRef.current = true; setBusy(true); setError(''); setNotice(''); follow.current = true;
    let item = activeRef.current;
    const text = draftRef.current;
    const abort = new AbortController(); controller.current = abort;
    try {
      await saveDraft(); item = activeRef.current;
      if (!item) { item = await api<Conversation>('/conversations', 'POST', { project_id: projectId || null }); accept(item); }
      const previous = requestRef.current;
      const body = previous?.id === item.id && previous.body.content === text && previous.body.model === model
        ? previous.body : { request_id: crypto.randomUUID(), content: text, model, reasoning_effort: reasoning, document_ids: selectedPapers, revision: item.revision };
      requestRef.current = { id: item.id, body };
      await streamTurn(item.id, body, abort.signal, event => {
        if (!authRef.current) return;
        if (event.type === 'snapshot' && event.conversation) { accept(event.conversation); changeDraft(''); }
        if (event.type === 'delta' && event.text) {
          const current = activeRef.current;
          if (current) { const messages = current.messages.map((m, index) => index === current.messages.length - 1 ? { ...m, content: m.content + event.text } : m); accept({ ...current, messages }); }
        }
        if (event.type === 'error') setError(new WorkspaceApiError(502, event.error || 'generation_failed').message);
        if (event.type === 'notice') setNotice('该模型不支持当前思考参数，已按模型默认方式生成。');
      });
      requestRef.current = null;
    } catch (e) {
      if (e instanceof WorkspaceApiError) requestRef.current = null;
      if (!(e instanceof DOMException && e.name === 'AbortError')) report(e);
      else setNotice('已停止生成，已收到的内容保留在对话中。');
    } finally {
      controller.current = null;
      if (item && authRef.current) { try { accept(await api<Conversation>(`/conversations/${item.id}`)); await refreshList(); } catch (e) { report(e); } }
      busyRef.current = false; setBusy(false);
    }
  };
  const stop = async () => {
    const item = activeRef.current;
    if (item?.active) { try { accept(await api<Conversation>(`/conversations/${item.id}/stop`, 'POST', { request_id: item.active })); } catch (e) { report(e); return; } }
    controller.current?.abort();
  };
  const importLegacy = async () => {
    setUploading(true); let count = 0;
    try { for (const session of legacy) { const result = await api<{ imported: number }>('/import/legacy', 'POST', { sessions: [session] }); count += result.imported; } setLegacy([]); setNotice(`导入完成：新增 ${count} 个对话，重复记录已跳过。浏览器原始记录仍保留。`); await refreshList(); } catch (e) { report(e); } finally { setUploading(false); }
  };
  const exportCurrent = () => {
    if (!active) return;
    const text = `# ${active.title}\n\n` + active.messages.map(m => `## ${m.role === 'user' ? '你' : 'AI'}\n\n${m.content}\n\n${m.citations.map(c => `[${c.id}] ${c.title} · 第 ${c.page} 页\n> ${c.quote.replaceAll('\n', '\n> ')}`).join('\n\n')}`).join('\n\n');
    saveDownload(text, 'learning-conversation.md', 'text/markdown');
  };
  const locked = !authenticated || !ready || busy || uploading;
  const toolbar = <div className="chat-topbar-controls">
    <button className="btn btn-ghost btn-sm chat-history-toggle" aria-label={historyOpen ? '关闭历史记录' : '打开历史记录'} aria-expanded={historyOpen} onClick={() => setHistoryOpen(!historyOpen)} disabled={!authenticated}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4.4 5.2h11.2M4.4 10h11.2M4.4 14.8h7.2" /></svg><span>历史</span><span className="chat-history-count">{sessions.length}</span></button>
    <label><span className="sr-only">当前模型</span><input className="chat-toolbar-select chat-toolbar-model-input" aria-label="当前模型" list="workspace-models" value={model} onChange={e => setModel(e.target.value)} disabled={locked} placeholder="选择或输入模型" /><datalist id="workspace-models">{models.map(m => <option value={m.id} key={m.id}>{m.name}</option>)}</datalist></label>
    <select aria-label="思考强度" className="chat-toolbar-select chat-toolbar-select-small" value={reasoning} onChange={e => setReasoning(e.target.value as ReasoningEffort)} disabled={locked}><option value="auto">自动</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select>
    <button className="btn btn-ghost btn-sm" aria-label="AI 用量" disabled={!authenticated} onClick={() => { void api<{ requests: number; reported_requests: number; prompt_tokens: number; completion_tokens: number }>('/workspace/usage').then(setUsage).catch(report); }}>用量</button>
    <button className="btn btn-ghost btn-sm" aria-label="设置" onClick={() => setSettingsOpen(true)} disabled={!authenticated}>设置</button>
  </div>;

  return <CodexShell active="chat" badge={publicHost} pageTitle="AI 对话" topbarExtra={toolbar} agentEnabled={authenticated}>
    <section className="chat-page personal-workspace">
      <div className="workspace-project-bar"><label>学习项目<select className="input" aria-label="学习项目" value={projectId} disabled={locked} onChange={e => { void newConversation(e.target.value); }}><option value="">全部 / 自由对话</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <div className="workspace-actions"><button className="btn btn-ghost btn-sm" disabled={locked} onClick={() => setProjectEditor('new')}>＋ 项目</button>{project && <button className="btn btn-ghost btn-sm" disabled={locked} onClick={() => setProjectEditor(project)}>项目目标</button>}<button className="btn btn-secondary btn-sm" disabled={locked || !projectId} onClick={() => setPapersOpen(!papersOpen)}>论文资料 · {papers.length}</button><button className="btn btn-primary btn-sm" disabled={locked} onClick={() => { void newConversation(); }}>新对话</button></div>
      </div>
      {project?.goal && <p className="workspace-goal">{project.goal}</p>}
      {legacy.length > 0 && <div className="workspace-banner"><span>发现此浏览器的旧对话，可导入服务器后跨设备使用。</span><button className="btn btn-secondary btn-sm" disabled={locked} onClick={() => { void importLegacy(); }}>导入旧对话（{legacy.length}）</button></div>}
      {papersOpen && projectId && <section className="workspace-papers card" aria-label="论文资料"><div className="workspace-actions"><label className="btn btn-secondary">{uploading ? '正在提取文字…' : '上传论文'}<input className="sr-only" type="file" accept=".pdf,.txt,.md" disabled={locked} aria-label="上传论文" onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (!file) return; setUploading(true); setError(''); void uploadPaper(projectId, file).then(async () => { await refreshPapers(); setNotice('论文已保存，请勾选后提问。'); }).catch(report).finally(() => setUploading(false)); }} /></label><span className="workspace-muted">PDF / TXT / MD · 每份 ≤ 10 MB · PDF ≤ 200 页</span></div>
        <p className="workspace-muted">勾选的论文片段会发送给当前模型服务。按本地关键词选取最多 8 个片段；不包含扫描件识别。</p>
        {papers.map(p => <div className="workspace-paper" key={p.id}><label><input type="checkbox" checked={selectedPapers.includes(p.id)} disabled={locked || (!selectedPapers.includes(p.id) && selectedPapers.length >= 8)} onChange={e => setSelectedPapers(current => e.target.checked ? [...current, p.id] : current.filter(id => id !== p.id))} />{p.title} <small>{p.page_count} 页</small></label><a href={`/api/chat/documents/${p.id}/file`} target="_blank" rel="noreferrer">原文</a><button type="button" className="btn btn-ghost btn-sm" disabled={locked} onClick={() => { if (!window.confirm('删除原文件？历史回答中的引用片段会保留。')) return; void api(`/documents/${p.id}`, 'DELETE').then(async () => { setSelectedPapers(ids => ids.filter(id => id !== p.id)); await refreshPapers(); }).catch(report); }}>删除</button></div>)}
        {!papers.length && <p>上传后可在当前项目中反复引用。</p>}
      </section>}
      <div className={`chat-layout${historyOpen ? '' : ' history-collapsed'}`}>
        {historyOpen && authenticated && <aside className="chat-history-panel card"><div className="chat-history"><header className="chat-sidebar-header"><strong>对话历史</strong><button className="btn btn-ghost btn-sm" onClick={() => setHistoryOpen(false)}>隐藏</button></header><label className="chat-search"><input type="search" aria-label="搜索对话" placeholder="搜索标题和正文" value={search} onChange={e => setSearch(e.target.value)} /></label><div className="chat-session-list">{sessions.map(s => <div className={`chat-session-row${s.id === active?.id ? ' active' : ''}`} key={s.id}><button className="chat-session" disabled={locked} onClick={() => { void switchTo(s.id); }}><span>{s.title}</span><small>{s.message_count} 条 · {new Date(s.updatedAt).toLocaleDateString()}</small></button><div className="chat-session-actions"><button aria-label={`重命名 ${s.title}`} disabled={locked} onClick={() => { const title = window.prompt('对话标题', s.title)?.trim(); if (!title) return; void api<Conversation>(`/conversations/${s.id}`, 'PATCH', { title, revision: s.revision }).then(async item => { if (active?.id === item.id) accept(item); await refreshList(); }).catch(report); }}>…</button><button aria-label={`删除 ${s.title}`} disabled={locked} onClick={() => { if (!window.confirm('删除这个对话？建议先导出需要的内容。')) return; void api(`/conversations/${s.id}`, 'DELETE', { revision: s.revision }).then(async () => { if (active?.id === s.id) { accept(null); changeDraft(''); } await refreshList(); }).catch(report); }}>×</button></div></div>)}{!sessions.length && <p className="chat-sidebar-empty">没有匹配的对话</p>}</div><p className="workspace-muted">记录保存在服务器</p></div></aside>}
        <section className="chat-thread card"><header className="chat-thread-heading"><div className="chat-thread-title"><div><strong>{active?.title || '开始一段学习对话'}</strong><span>{busy ? '正在生成并保存' : active ? '服务器已保存' : '围绕一个问题，慢慢想清楚'}</span></div></div><div className="workspace-actions">{active && <><button className="btn btn-ghost btn-sm" onClick={exportCurrent}>导出</button><button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => { void api<Conversation>(`/conversations/${active.id}`).then(accept).catch(report); }}>刷新</button></>}</div></header>
          <div ref={messagesRef} className="chat-messages" role="log" aria-live="polite" onScroll={e => { const node = e.currentTarget; follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 60; }}>
            {active?.messages.length ? active.messages.map(m => <ChatMessage key={m.id} message={m} pending={m.status === 'streaming' && !m.content} {...(m.role === 'assistant' ? { onJournal: () => setJournalMessage(m) } : {})} />) : <div className="chat-empty-state"><div className="chat-empty-mark">✦</div><h2>把问题想明白</h2><p>讨论原理、阅读论文，也记录你的理解如何变化。</p><div className="chat-quick-prompts">{['用提问检验我对 LSM Tree 的理解', '帮我梳理论文的研究问题与局限', '陪我练习雅思口语，并解释纠错原因', '一起检查我今天一个想法的依据'].map(prompt => <button type="button" key={prompt} disabled={locked} onClick={() => changeDraft(prompt)}>{prompt}</button>)}</div></div>}
          </div>
          <div className="chat-composer">{selectedPapers.length > 0 && <small>本次使用 {selectedPapers.length} 份资料</small>}<textarea aria-label="聊天消息" value={draft} maxLength={12000} onChange={e => changeDraft(e.target.value)} onKeyDown={e => { if (!e.nativeEvent.isComposing && e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(); } }} placeholder="写下你的问题、解释或想法…" rows={3} disabled={!authenticated || !ready || busy || !!active?.active} /><div className="chat-composer-footer"><span>{draftStatus || 'Enter 发送 · Shift + Enter 换行'}</span>{busy || active?.active ? <button className="btn btn-secondary" onClick={() => { void stop(); }}>停止</button> : <button className="btn btn-primary" disabled={locked || draftConflict || !draft.trim() || !model.trim()} onClick={() => { void send(); }}>发送 ↑</button>}</div></div>
        </section>
      </div>
      {draftConflict && <div className="workspace-banner" role="alert"><span>另一处修改了草稿。本机文字已保留，请选择：</span><button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => { void resolveDraft(true); }}>保留本机草稿并保存</button><button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => { void resolveDraft(false); }}>使用服务器草稿</button></div>}
      {notice && <div className="chat-notice" role="status">{notice}</div>}{error && <div className="err" role="alert">{error}</div>}
    </section>
    {projectEditor && <ProjectDialog project={projectEditor === 'new' ? null : projectEditor} onClose={() => setProjectEditor(null)} onSaved={p => { setProjectEditor(null); void api<{ items: LearningProject[] }>('/projects').then(r => setProjects(r.items)).catch(report); if (p && projectEditor === 'new') void newConversation(p.id); if (!p) void newConversation(''); }} />}
    {journalMessage && active && <ChatJournalDialog conversation={active} message={journalMessage} onClose={() => setJournalMessage(null)} />}
    {usage && <WorkspaceDialog title="AI 用量" onClose={() => setUsage(null)}><p>已保存对话中的请求：{usage.requests} 次</p><p>输入 tokens：{usage.prompt_tokens.toLocaleString()} · 输出 tokens：{usage.completion_tokens.toLocaleString()}</p><p className="workspace-muted">其中 {usage.reported_requests} 次由服务返回了用量。未返回的用量不估算；不包含导入的旧记录与已删除的对话。</p></WorkspaceDialog>}
    {settingsOpen && <WorkspaceDialog title="设置" onClose={() => setSettingsOpen(false)}><ChatSettings settings={settings} busy={busy} feedback="" onSave={async values => { try { setSettings(await saveChatSettings(values)); setNotice('设置已保存'); return true; } catch (e) { report(e); return false; } }} onExport={() => { void api<unknown>('/workspace/export').then(value => saveDownload(JSON.stringify(value, null, 2), 'learning-workspace.json')).catch(report); }} /></WorkspaceDialog>}
  </CodexShell>;
}
