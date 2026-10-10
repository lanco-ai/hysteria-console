import { useCallback, useEffect, useRef, useState, type ComponentType, type KeyboardEvent } from 'react';
import { CodexShell, type CodexShellProps } from '../../shared/CodexShell';
import { AISettingsDialog, SEND_KEY_STORAGE, type SendKey } from './AISettingsDialog';
import { ChatMessage } from './ChatMessage';
import { ChatJournalDialog } from './ChatJournalDialog';
import { ContextPanel } from './ContextPanel';
import { ConversationSidebar } from './ConversationSidebar';
import { ProjectDialog } from './ProjectDialog';
import { ProjectSelectorDialog } from './ProjectSelectorDialog';
import { KnowledgeDialog } from './KnowledgeDialog';
import { MemoryDialog } from './MemoryDialog';
import { ToolsDialog } from './ToolsDialog';
import { WorkspaceDialog } from './WorkspaceDialog';
import { loadChatModels, loadChatSettings, type ChatModel, type ChatSettings as Settings, type ReasoningEffort } from './chatApi';
import { workspaceRequest as api, streamTurn, uploadPaper, saveDownload, WorkspaceApiError, type Conversation, type LearningProject, type Paper, type WorkspaceMessage, type WorkspacePreferences } from './workspaceApi';

export type ChatPageProps = { publicHost: string; authenticated?: boolean; onUnauthenticated?: () => void; shell?: ComponentType<CodexShellProps> };
const LEGACY_KEY = 'hy2.chat.sessions.v1';
const DRAFT_KEY = 'hy2.chat.unsent.v2';
const PANES_KEY = 'hy2.chat.panes.v1';
// Below these widths the conversation list and the context panel float over the thread.
const DOCKED_SIDEBAR_QUERY = '(min-width: 1100px)';
const DOCKED_CONTEXT_QUERY = '(min-width: 1400px)';
const QUICK_PROMPTS = ['用提问检验我对 LSM Tree 的理解', '帮我梳理论文的研究问题与局限', '陪我练习雅思口语，并解释纠错原因', '一起检查我今天一个想法的依据'];

function replaceConversationLocation(id?: string) {
  const query = new URLSearchParams();
  if (id) query.set('conversation', id);
  const suffix = query.toString();
  window.history.replaceState(window.history.state, '', `${window.location.pathname}${suffix ? `?${suffix}` : ''}`);
}

type Panes = { sidebar?: boolean; context?: boolean };

function docked(query: string) {
  return window.matchMedia(query).matches;
}

// Open/closed choices are remembered only for docked panes; floating ones always start closed.
function readPanes(): Panes {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(PANES_KEY) || '{}');
    return value && typeof value === 'object' ? value as Panes : {};
  } catch { return {}; }
}

function savePanes(change: Panes) {
  try { localStorage.setItem(PANES_KEY, JSON.stringify({ ...readPanes(), ...change })); } catch { /* The choice still applies now. */ }
}

export function ChatPage({ publicHost, authenticated: authProp, onUnauthenticated, shell: Shell = CodexShell }: ChatPageProps) {
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
  const [preferences, setPreferences] = useState<WorkspacePreferences | null>(null);
  const [sendKey, setSendKey] = useState<SendKey>('enter');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const [knowledgeScope, setKnowledgeScope] = useState('none');
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [memorySource, setMemorySource] = useState<WorkspaceMessage | null>(null);
  const [summaryEditor, setSummaryEditor] = useState<string | null>(null);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [toolRuns, setToolRuns] = useState<string[]>([]);
  const [projectEditor, setProjectEditor] = useState<LearningProject | 'new' | null>(null);
  const [projectSelectorOpen, setProjectSelectorOpen] = useState(false);
  const [projectSwitchPending, setProjectSwitchPending] = useState(false);
  const projectSwitchRef = useRef(false);
  const projectButtonRef = useRef<HTMLButtonElement>(null);
  const projectEditorFocus = useRef<HTMLElement | null>(null);
  const settingsButtonRef = useRef<HTMLButtonElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const sidebarToggleRef = useRef<HTMLButtonElement>(null);
  const contextRef = useRef<HTMLElement>(null);
  const contextToggleRef = useRef<HTMLButtonElement>(null);
  // Set by explicit open/close actions so focus follows the pane instead of falling back to <body>.
  const paneFocus = useRef<'sidebar' | 'sidebar-toggle' | 'context' | 'context-toggle' | null>(null);
  const [journalMessage, setJournalMessage] = useState<WorkspaceMessage | null>(null);
  const [branch, setBranch] = useState<{ message: WorkspaceMessage; mode: 'edit' | 'regenerate' | 'continue'; text: string; requestId: string } | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
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
  const toolsButtonRef = useRef<HTMLButtonElement>(null);
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
  useEffect(() => {
    if (!authenticated || !papers.some(p => p.status === 'queued' || p.status === 'processing')) return;
    const timer = setInterval(() => { void refreshPapers().catch(report); }, 2500);
    return () => clearInterval(timer);
  }, [authenticated, papers, refreshPapers, report]);

  useEffect(() => { if (authProp !== undefined) return; let live = true; void fetch('/api/session', { credentials: 'same-origin' }).then(r => r.json()).then((value: { role?: string }) => { if (live) setFallbackAuth(value.role === 'admin'); }).catch(() => {}); return () => { live = false; }; }, [authProp]);
  useEffect(() => {
    if (!authenticated) { setSessions([]); accept(null); setProjects([]); setPapers([]); setLegacy([]); setProjectEditor(null); setProjectSelectorOpen(false); setJournalMessage(null); setBranch(null); setKnowledgeOpen(false); setToolsOpen(false); setToolRuns([]); setMemoryOpen(false); setMemorySource(null); setSummaryEditor(null); setSettingsOpen(false); setPreferences(null); setHistoryOpen(false); setContextOpen(false); setError(''); setNotice(''); setDraftConflict(false); draftConflictRef.current = false; setDraft(''); draftRef.current = ''; setReady(false); controller.current?.abort(); return; }
    let live = true;
    void Promise.all([api<{ items: LearningProject[] }>('/projects'), api<{ items: Conversation[] }>('/conversations')]).then(async ([p, c]) => {
      if (!live) return; setProjects(p.items); setSessions(c.items);
      let savedDraft: { id?: string; text?: string } = {};
      try { savedDraft = JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}') as typeof savedDraft; } catch { /* Optional recovery. */ }
      const linked = new URLSearchParams(window.location.search).get('conversation');
      const id = linked || savedDraft.id || c.items[0]?.id;
      if (id) {
        const item = await api<Conversation>(`/conversations/${encodeURIComponent(id)}`);
        if (!live) return; accept(item); setProjectId(item.project_id || ''); setSelectedPapers(item.draft_document_ids || []); setToolRuns(item.draft_tool_run_ids || []); if (item.model) setModel(item.model); setReasoning(item.reasoningEffort);
        changeDraft(!linked && savedDraft.id === id && typeof savedDraft.text === 'string' ? savedDraft.text : item.draft);
      } else if (typeof savedDraft.text === 'string') changeDraft(savedDraft.text);
      setReady(true);
    }).catch(e => { if (live) { report(e); setReady(true); } });
    void loadChatSettings().then(s => { if (live) setSettings(s); }).catch(report);
    void loadChatModels().then(m => { if (live) { setModels(m); setModel(value => value || m[0]?.id || ''); } }).catch(report);
    void api<WorkspacePreferences>('/workspace/preferences').then(value => { if (live) setPreferences(value); }).catch(report);
    try { const data: unknown = JSON.parse(localStorage.getItem(LEGACY_KEY) || '[]'); if (Array.isArray(data)) setLegacy(data); } catch { setNotice('旧对话数据无法解析，浏览器原始记录仍保留。'); }
    // Layout preferences are read only once signed in, like the conversation state.
    try { setSendKey(localStorage.getItem(SEND_KEY_STORAGE) === 'mod-enter' ? 'mod-enter' : 'enter'); } catch { /* Defaults apply. */ }
    const panes = readPanes();
    setHistoryOpen(docked(DOCKED_SIDEBAR_QUERY) && panes.sidebar !== false);
    setContextOpen(docked(DOCKED_CONTEXT_QUERY) && panes.context === true);
    return () => { live = false; controller.current?.abort(); };
  }, [authenticated, accept, changeDraft, report]);
  // Crossing a breakpoint re-applies the remembered docked layout instead of leaving a drawer over the thread.
  useEffect(() => {
    if (!authenticated) return;
    const sidebarQuery = window.matchMedia(DOCKED_SIDEBAR_QUERY);
    const contextQuery = window.matchMedia(DOCKED_CONTEXT_QUERY);
    const sync = () => { const panes = readPanes(); setHistoryOpen(sidebarQuery.matches && panes.sidebar !== false); setContextOpen(contextQuery.matches && panes.context === true); };
    sidebarQuery.addEventListener('change', sync); contextQuery.addEventListener('change', sync);
    return () => { sidebarQuery.removeEventListener('change', sync); contextQuery.removeEventListener('change', sync); };
  }, [authenticated]);
  // New conversations start from the AI 设置 defaults; existing ones keep their own choice.
  // Keyed on the default values so saving other settings never resets a pick made for this draft.
  const defaultModel = preferences?.default_model;
  const defaultReasoning = preferences?.default_reasoning;
  useEffect(() => {
    if (!ready || defaultReasoning === undefined || activeRef.current) return;
    if (defaultModel) setModel(defaultModel);
    setReasoning(defaultReasoning);
  }, [ready, defaultModel, defaultReasoning]);

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

  useEffect(() => {
    const target = paneFocus.current; paneFocus.current = null;
    const element = target === 'sidebar' ? sidebarRef.current : target === 'sidebar-toggle' ? sidebarToggleRef.current : target === 'context' ? contextRef.current : target === 'context-toggle' ? contextToggleRef.current : null;
    element?.focus({ preventScroll: true });
  }, [historyOpen, contextOpen]);
  const showSidebar = (open: boolean) => {
    paneFocus.current = open ? 'sidebar' : 'sidebar-toggle';
    setHistoryOpen(open);
    if (docked(DOCKED_SIDEBAR_QUERY)) savePanes({ sidebar: open });
    else if (open) setContextOpen(false);
  };
  // Floating panes give the thread back after a choice; docked ones stay open.
  const settleFloatingSidebar = () => { if (!docked(DOCKED_SIDEBAR_QUERY)) setHistoryOpen(open => { if (open) paneFocus.current = 'sidebar-toggle'; return false; }); };
  const showContext = (open: boolean) => {
    paneFocus.current = open ? 'context' : 'context-toggle';
    setContextOpen(open);
    if (docked(DOCKED_CONTEXT_QUERY)) savePanes({ context: open });
    else if (open) settleFloatingSidebar();
  };
  const closeFloatingPane = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    if (contextOpen && !docked(DOCKED_CONTEXT_QUERY)) { event.preventDefault(); showContext(false); }
    else if (historyOpen && !docked(DOCKED_SIDEBAR_QUERY)) { event.preventDefault(); showSidebar(false); }
  };
  const chooseSendKey = (value: SendKey) => { setSendKey(value); try { localStorage.setItem(SEND_KEY_STORAGE, value); } catch { /* Applies to this page. */ } };

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
      accept(item); changeDraft(item.draft); setProjectId(item.project_id || ''); if (item.model) setModel(item.model); setReasoning(item.reasoningEffort); setSelectedPapers(item.draft_document_ids || []); setToolRuns(item.draft_tool_run_ids || []); settleFloatingSidebar(); setError(''); setNotice(''); requestRef.current = null; follow.current = true;
      replaceConversationLocation(id);
    } catch (e) { report(e); }
  };
  const newConversation = async (nextProject = projectId): Promise<boolean> => {
    if (busyRef.current || projectSwitchRef.current || !authenticated || !ready || uploading) return false;
    projectSwitchRef.current = true; setProjectSwitchPending(true);
    try {
      await saveDraft();
      if (!authRef.current) return false;
      if (!activeRef.current && draftRef.current.trim() && !window.confirm('新建对话会清空当前未发送的草稿，继续吗？')) return false;
      accept(null); changeDraft(''); setProjectId(nextProject); if (!nextProject && knowledgeScope === 'project') setKnowledgeScope('none'); setSelectedPapers([]); setToolRuns([]); setError(''); setNotice(''); requestRef.current = null; replaceConversationLocation(); settleFloatingSidebar();
      if (preferences?.default_model) setModel(preferences.default_model);
      if (preferences) setReasoning(preferences.default_reasoning);
      return true;
    } catch (e) { report(e); return false; }
    finally { projectSwitchRef.current = false; setProjectSwitchPending(false); }
  };
  const openTools = async () => {
    if (busyRef.current || !authenticated || !ready) return;
    busyRef.current = true; setBusy(true);
    try {
      await saveDraft();
      if (!activeRef.current) { accept(await api<Conversation>('/conversations', 'POST', { project_id: projectId || null })); await saveDraft(); }
      setToolsOpen(true);
    } catch (e) { report(e); } finally { busyRef.current = false; setBusy(false); }
  };
  const send = async (documents = selectedPapers, results = toolRuns) => {
    if (busyRef.current || draftConflictRef.current || !authenticated || !ready || !draftRef.current.trim() || !model.trim()) return;
    busyRef.current = true; setBusy(true); setError(''); setNotice(''); follow.current = true;
    let item = activeRef.current;
    const text = draftRef.current;
    const abort = new AbortController(); controller.current = abort;
    try {
      await saveDraft(); item = activeRef.current;
      if (!item) { item = await api<Conversation>('/conversations', 'POST', { project_id: projectId || null }); accept(item); }
      const previous = requestRef.current;
      const body = previous?.id === item.id && previous.body.content === text && previous.body.model === model && previous.body.reasoning_effort === reasoning && previous.body.knowledge_scope === knowledgeScope && JSON.stringify(previous.body.document_ids) === JSON.stringify(documents) && JSON.stringify(previous.body.tool_run_ids) === JSON.stringify(results)
        ? previous.body : { request_id: crypto.randomUUID(), content: text, model, reasoning_effort: reasoning, document_ids: documents, knowledge_scope: knowledgeScope, tool_run_ids: results, revision: item.revision };
      requestRef.current = { id: item.id, body };
      await streamTurn(item.id, body, abort.signal, event => {
        if (!authRef.current) return;
        if (event.type === 'snapshot' && event.conversation) { accept(event.conversation); changeDraft(''); setToolRuns([]); }
        if (event.type === 'delta' && event.text) {
          const current = activeRef.current;
          if (current) { const messages = current.messages.map((m, index) => index === current.messages.length - 1 ? { ...m, content: m.content + event.text } : m); accept({ ...current, messages }); }
        }
        if (event.type === 'error') setError(new WorkspaceApiError(502, event.error || 'generation_failed').message);
        if (event.type === 'notice') setNotice(event.notice === 'context_summary_incomplete' ? '部分早期对话暂未成功汇总，本次仍会使用已保存的摘要和近期消息。' : '该模型不支持当前思考参数，已按模型默认方式生成。');
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
  const createBranch = async () => {
    if (!branch || busyRef.current || !activeRef.current) return;
    busyRef.current = true; setBusy(true); setError('');
    let next: Conversation | null = null;
    try {
      await saveDraft(); const source = activeRef.current!;
      next = await api<Conversation>(`/conversations/${source.id}/branches`, 'POST', { revision: source.revision, request_id: branch.requestId, message_id: branch.message.id, mode: branch.mode, content: branch.text });
      accept(next); changeDraft(next.draft); setSelectedPapers(next.draft_document_ids || []); setToolRuns(next.draft_tool_run_ids || []); requestRef.current = null;
      replaceConversationLocation(next.id);
      setBranch(null); await refreshList();
    } catch (e) { report(e); } finally { busyRef.current = false; setBusy(false); }
    if (next && branch.mode !== 'continue') await send(next.draft_document_ids || [], next.draft_tool_run_ids || []);
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
  const renameConversation = (item: Conversation) => {
    const title = window.prompt('对话标题', item.title)?.trim(); if (!title) return;
    void api<Conversation>(`/conversations/${item.id}`, 'PATCH', { title, revision: item.revision }).then(async next => { if (active?.id === next.id) accept(next); await refreshList(); }).catch(report);
  };
  const deleteConversation = (item: Conversation) => {
    if (!window.confirm('删除这个对话？建议先导出需要的内容。')) return;
    void api(`/conversations/${item.id}`, 'DELETE', { revision: item.revision }).then(async () => { if (active?.id === item.id) { accept(null); changeDraft(''); } await refreshList(); }).catch(report);
  };
  const uploadDocument = (file: File) => {
    setUploading(true); setError('');
    void uploadPaper(projectId, file).then(async () => { await refreshPapers(); setNotice('资料已保存，处理完成后可勾选提问。'); }).catch(report).finally(() => setUploading(false));
  };
  const locked = !authenticated || !ready || busy || uploading || projectSwitchPending;
  const sendHint = sendKey === 'enter' ? 'Enter 发送 · Shift + Enter 换行' : 'Ctrl / ⌘ + Enter 发送 · Enter 换行';
  const paperTitle = (id: string) => papers.find(p => p.id === id)?.title || '资料';

  const toolbar = <div className="chat-topbar-controls">
    <label><span className="sr-only">当前模型</span><input className="chat-toolbar-select chat-toolbar-model-input" aria-label="当前模型" list="workspace-models" value={model} onChange={e => setModel(e.target.value)} disabled={locked} placeholder="选择或输入模型" /><datalist id="workspace-models">{models.map(m => <option value={m.id} key={m.id}>{m.name}</option>)}</datalist></label>
    <select aria-label="思考强度" className="chat-toolbar-select chat-toolbar-select-small" value={reasoning} onChange={e => setReasoning(e.target.value as ReasoningEffort)} disabled={locked}><option value="auto">自动</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select>
    <button ref={settingsButtonRef} className="btn btn-ghost btn-sm chat-settings-trigger" type="button" aria-label="AI 设置" title="AI 设置" onClick={() => { setSettingsOpen(true); void api<WorkspacePreferences>('/workspace/preferences').then(value => { if (authRef.current) setPreferences(value); }).catch(report); }} disabled={!authenticated}><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="2.6"/><path d="M10 2.8v2M10 15.2v2M2.8 10h2M15.2 10h2M4.9 4.9l1.4 1.4M13.7 13.7l1.4 1.4M4.9 15.1l1.4-1.4M13.7 6.3l1.4-1.4"/></svg><span>AI 设置</span></button>
  </div>;

  return <Shell active="chat" badge={publicHost} pageTitle="AI 对话" topbarExtra={toolbar} agentEnabled={authenticated}>
    <section className={`chat-page personal-workspace chat-workbench${historyOpen && authenticated ? ' has-sidebar' : ''}${contextOpen && authenticated ? ' has-context' : ''}`} onKeyDown={closeFloatingPane}>
      {historyOpen && authenticated && <ConversationSidebar panelRef={sidebarRef} sessions={sessions} activeId={active?.id || ''} search={search} disabled={locked} projectName={project?.name || ''} projectGoal={project?.goal || ''} projectButtonRef={projectButtonRef} projectSelectorOpen={projectSelectorOpen}
        onSearch={setSearch} onNew={() => { void newConversation(); }} onSelect={id => { void switchTo(id); }} onRename={renameConversation} onDelete={deleteConversation}
        onPickProject={() => setProjectSelectorOpen(true)} onCreateProject={() => setProjectEditor('new')} onCollapse={() => showSidebar(false)} />}
      {historyOpen && authenticated && <button type="button" className="chat-scrim chat-scrim-sidebar" aria-label="关闭对话列表" tabIndex={-1} onClick={() => showSidebar(false)}/>}

      <section className="chat-thread card">
        <header className="chat-thread-heading">
          {!historyOpen && <div className="chat-thread-nav">
            <button ref={sidebarToggleRef} type="button" className="chat-icon-button" aria-label="打开对话列表" title="打开对话列表" disabled={!authenticated} onClick={() => showSidebar(true)}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 5.5h12M4 10h12M4 14.5h8"/></svg></button>
            <button type="button" className="chat-icon-button" aria-label="新对话" title="新对话" disabled={locked} onClick={() => { void newConversation(); }}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4.5v11M4.5 10h11"/></svg></button>
          </div>}
          <div className="chat-thread-title">
            <strong>{active?.title || '新对话'}</strong>
            <span>{project?.name || '自由对话'} · {busy ? '正在生成并保存' : active ? '服务器已保存' : '围绕一个问题，慢慢想清楚'}</span>
          </div>
          <div className="workspace-actions chat-thread-actions">
            {active && <><button className="btn btn-ghost btn-sm" type="button" disabled={locked || !!active.active} onClick={() => setSummaryEditor(active.summary?.text || '')}>长期上下文</button><button className="btn btn-ghost btn-sm" type="button" onClick={exportCurrent}>导出</button><button className="btn btn-ghost btn-sm" type="button" disabled={busy} onClick={() => { void api<Conversation>(`/conversations/${active.id}`).then(accept).catch(report); }}>刷新</button></>}
            <button ref={contextToggleRef} className={`btn btn-sm chat-context-toggle${contextOpen ? ' is-active' : ''}`} type="button" aria-expanded={contextOpen} disabled={!authenticated} onClick={() => showContext(!contextOpen)}>
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 3.5h5.5L15 7v9.5H6Z"/><path d="M11.5 3.5V7H15M8.5 10.5h4M8.5 13.5h4"/></svg>
              <span>论文资料 · {papers.length}{selectedPapers.length ? ` · 已选 ${selectedPapers.length}` : ''}</span>
            </button>
          </div>
        </header>
        {legacy.length > 0 && <div className="workspace-banner"><span>发现此浏览器的旧对话，可导入服务器后跨设备使用。</span><button className="btn btn-secondary btn-sm" disabled={locked} onClick={() => { void importLegacy(); }}>导入旧对话（{legacy.length}）</button></div>}
        <div ref={messagesRef} className="chat-messages" role="log" aria-live="polite" onScroll={e => { const node = e.currentTarget; follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 60; }}>
          {active?.parent_conversation_id && <p className="workspace-muted chat-branch-note">当前为独立分支 · <button className="btn btn-ghost btn-sm" disabled={locked} onClick={() => { void switchTo(active.parent_conversation_id!); }}>返回原对话</button></p>}
          {active?.messages.length ? active.messages.map(m => <ChatMessage key={m.id} message={m} disabled={locked || !!active.active} pending={m.status === 'streaming' && !m.content} {...(project ? { onMemory: () => { setMemorySource(m); setMemoryOpen(true); } } : {})} onBranch={mode => setBranch({ message: m, mode, text: mode === 'edit' ? m.content : '', requestId: crypto.randomUUID() })} {...(m.role === 'assistant' ? { onJournal: () => setJournalMessage(m) } : {})} />) : <div className="chat-empty-state">
            <div className="chat-empty-mark" aria-hidden="true">✦</div>
            <h2>把问题想明白</h2>
            <p>讨论原理、阅读论文，也记录你的理解如何变化。</p>
            <div className="chat-quick-prompts">{QUICK_PROMPTS.map(prompt => <button type="button" key={prompt} disabled={locked} onClick={() => changeDraft(prompt)}>{prompt}</button>)}</div>
            <p className="workspace-muted chat-empty-meta">{model ? `当前模型 ${model}` : '尚未选择模型'}{project ? ` · 项目 ${project.name}` : ''}</p>
          </div>}
        </div>
        {draftConflict && <div className="workspace-banner" role="alert"><span>另一处修改了草稿。本机文字已保留，请选择：</span><button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => { void resolveDraft(true); }}>保留本机草稿并保存</button><button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => { void resolveDraft(false); }}>使用服务器草稿</button></div>}
        {notice && <div className="chat-notice" role="status">{notice}</div>}{error && <div className="err" role="alert">{error}</div>}
        <div className="chat-composer">
          {(selectedPapers.length > 0 || toolRuns.length > 0 || knowledgeScope !== 'none') && <div className="chat-attachments" aria-label="本次附加">
            {selectedPapers.map(id => <span className="chat-chip" key={id}><span>{paperTitle(id)}</span><button type="button" aria-label={`不再使用 ${paperTitle(id)}`} disabled={locked} onClick={() => setSelectedPapers(current => current.filter(item => item !== id))}>×</button></span>)}
            {toolRuns.length > 0 && <button className="chat-chip" type="button" disabled={locked} onClick={() => setToolRuns([])}>已附 {toolRuns.length} 份工具结果 · 移除</button>}
            {knowledgeScope !== 'none' && <span className="chat-chip is-info">自动检索的原文片段会发送给当前模型</span>}
          </div>}
          <textarea aria-label="聊天消息" value={draft} maxLength={12000} onChange={e => changeDraft(e.target.value)} onKeyDown={e => {
            if (e.nativeEvent.isComposing || e.key !== 'Enter') return;
            const modifier = e.ctrlKey || e.metaKey;
            if (sendKey === 'enter' ? !e.shiftKey && !modifier : modifier) { e.preventDefault(); void send(); }
          }} placeholder="写下你的问题、解释或想法…" rows={3} disabled={!authenticated || !ready || busy || !!active?.active} />
          <div className="chat-composer-footer">
            <div className="chat-composer-tools">
              <button ref={toolsButtonRef} className="btn btn-ghost btn-sm" type="button" disabled={locked || !!active?.active} onClick={() => { void openTools(); }}>搜索与工具</button>
              <label className="workspace-context-scope"><span>知识库</span><select aria-label="回答使用的知识库范围" value={knowledgeScope} disabled={locked} onChange={e => setKnowledgeScope(e.target.value)}><option value="none">仅勾选资料</option>{projectId && <option value="project">自动检索当前项目</option>}<option value="all">自动检索全部个人资料</option></select></label>
            </div>
            <span className="chat-composer-hint">{draftStatus || sendHint}</span>
            {busy || active?.active ? <button className="btn btn-secondary" type="button" onClick={() => { void stop(); }}>停止</button> : <button className="btn btn-primary" type="button" disabled={locked || draftConflict || !draft.trim() || !model.trim()} onClick={() => { void send(); }}>发送 ↑</button>}
          </div>
        </div>
      </section>

      {contextOpen && authenticated && <ContextPanel panelRef={contextRef} project={project} papers={papers} selected={selectedPapers} locked={locked} uploading={uploading} onUpload={uploadDocument}
        onTogglePaper={(id, on) => setSelectedPapers(current => on ? [...current, id] : current.filter(item => item !== id))}
        onRetryPaper={id => { void api(`/documents/${id}/retry`, 'POST').then(refreshPapers).catch(report); }}
        onDeletePaper={id => { if (!window.confirm('删除原文件？历史回答中的引用片段会保留。')) return; void api(`/documents/${id}`, 'DELETE').then(async () => { setSelectedPapers(ids => ids.filter(item => item !== id)); await refreshPapers(); }).catch(report); }}
        onOpenKnowledge={() => setKnowledgeOpen(true)} onOpenMemory={() => { setMemorySource(null); setMemoryOpen(true); }} onEditProject={() => { if (project) setProjectEditor(project); }} onClose={() => showContext(false)} />}
      {contextOpen && authenticated && <button type="button" className="chat-scrim chat-scrim-context" aria-label="关闭资料面板" tabIndex={-1} onClick={() => showContext(false)}/>}
    </section>
    {projectSelectorOpen && authenticated && <ProjectSelectorDialog projects={projects} currentId={projectId} disabled={locked} returnFocusTo={projectButtonRef.current} onClose={() => setProjectSelectorOpen(false)} onSelect={newConversation} onCreate={() => { setProjectSelectorOpen(false); projectEditorFocus.current = projectButtonRef.current; setProjectEditor('new'); }} />}
    {projectEditor && <ProjectDialog project={projectEditor === 'new' ? null : projectEditor} {...(projectEditorFocus.current ? { returnFocusTo: projectEditorFocus.current } : {})} onClose={() => { setProjectEditor(null); projectEditorFocus.current = null; }} onSaved={p => { setProjectEditor(null); projectEditorFocus.current = null; void api<{ items: LearningProject[] }>('/projects').then(r => setProjects(r.items)).catch(report); if (p && projectEditor === 'new') void newConversation(p.id); if (!p) void newConversation(''); }} />}
    {journalMessage && active && <ChatJournalDialog conversation={active} message={journalMessage} onClose={() => setJournalMessage(null)} />}
    {knowledgeOpen && authenticated && <KnowledgeDialog projectId={projectId} onClose={() => setKnowledgeOpen(false)} />}
    {toolsOpen && active && authenticated && <ToolsDialog returnFocusTo={toolsButtonRef.current} conversation={active} question={draft} model={model} papers={papers} attached={toolRuns} onClose={() => setToolsOpen(false)} onAttach={ids => { setToolRuns(ids); setToolsOpen(false); }} />}
    {memoryOpen && project && authenticated && <MemoryDialog project={project} {...(memorySource && active ? { source: { conversation: active, message: memorySource } } : {})} onClose={() => { setMemoryOpen(false); setMemorySource(null); }} onSaved={p => setProjects(current => current.map(x => x.id === p.id ? p : x))} />}
    {summaryEditor !== null && active && authenticated && <WorkspaceDialog title="长期上下文" onClose={() => setSummaryEditor(null)}><p>对话过长时，会使用当前模型自动整理早期内容。原消息完整保存，摘要可以检查和修改。</p>{active.summary ? <><textarea className="input" aria-label="早期对话摘要" rows={10} value={summaryEditor} maxLength={4000} onChange={e => setSummaryEditor(e.target.value)} /><div className="workspace-actions"><button className="btn btn-primary" disabled={busy} onClick={() => { setBusy(true); void api<Conversation>(`/conversations/${active.id}/context`, 'PATCH', { revision: active.revision, text: summaryEditor }).then(item => { accept(item); setSummaryEditor(null); }).catch(report).finally(() => setBusy(false)); }}>保存摘要</button><small>清空后保存可删除摘要；下次长对话会重新整理。</small></div></> : <p>当前对话暂不需要摘要，后续超过上下文长度时会自动生成。</p>}</WorkspaceDialog>}
    {branch && authenticated && <WorkspaceDialog title={branch.mode === 'edit' ? '编辑问题' : branch.mode === 'regenerate' ? '重新生成回答' : '从这里继续'} onClose={() => { if (!busy) setBranch(null); }}><p>原对话会保留，新分支只带入此处之前的对话内容。</p>{branch.mode === 'edit' && <textarea className="input" aria-label="修改问题" rows={6} maxLength={12000} value={branch.text} disabled={busy} onChange={e => setBranch({ ...branch, text: e.target.value, requestId: crypto.randomUUID() })} />}<button className="btn btn-primary" disabled={busy || (branch.mode === 'edit' && !branch.text.trim())} onClick={() => { void createBranch(); }}>{busy ? '正在创建…' : branch.mode === 'continue' ? '创建分支' : '创建分支并生成'}</button></WorkspaceDialog>}
    {settingsOpen && authenticated && <AISettingsDialog settings={settings} models={models} preferences={preferences} sendKey={sendKey} legacyCount={legacy.length} locked={locked} returnFocusTo={settingsButtonRef.current}
      onSendKey={chooseSendKey} onPreferences={setPreferences} onSettings={setSettings}
      onImportLegacy={() => { void importLegacy(); }} onError={report} onClose={() => setSettingsOpen(false)} />}
  </Shell>;
}
