import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CodexShell } from '../../shared/CodexShell';
import { ChatMessage } from './ChatMessage';
import { ChatSettings } from './ChatSettings';
import { ChatSidebar, type ChatSession, type ChatUsage } from './ChatSidebar';
import {
  ChatApiError,
  loadChatModels,
  loadChatSettings,
  saveChatSettings,
  streamChat,
  testChatConnection,
  type ChatMessageData,
  type ChatModel,
  type ChatSettings as ChatSettingsData,
  type ReasoningEffort,
  type ChatStreamEvent,
  type SettingsUpdate,
} from './chatApi';

type Drawer = 'settings' | 'usage' | null;

export type ChatPageProps = {
  publicHost: string;
  authenticated?: boolean;
  onUnauthenticated?: () => void;
};

const STORAGE_KEY = 'hy2.chat.sessions.v1';
const USAGE_KEY = 'hy2.chat.usage.v1';
const DEFAULT_CHAT_MODEL = 'gemini-3.8-flash-high';
const reasoningOptions: Array<{ value: ReasoningEffort; label: string }> = [
  { value: 'auto', label: '自动' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
];

function newId() {
  return typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function sessionTitle(session: ChatSession): string {
  const explicit = typeof session.title === 'string' ? session.title.trim() : '';
  if (explicit && explicit !== '新对话') return explicit;
  const first = session.messages.find(item => item.role === 'user')?.content.trim() || '';
  if (!first) return '新对话';
  return first.length > 28 ? `${first.slice(0, 28)}…` : first;
}

function normaliseSession(value: unknown): ChatSession | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Partial<ChatSession>;
  if (typeof item.id !== 'string' || !Array.isArray(item.messages)) return null;
  const messages = item.messages.filter(message => (
    message && typeof message === 'object'
    && ['system', 'user', 'assistant'].includes((message as ChatMessageData).role)
    && typeof (message as ChatMessageData).content === 'string'
  )) as ChatMessageData[];
  const session: ChatSession = {
    id: item.id,
    messages,
    updatedAt: typeof item.updatedAt === 'number' && Number.isFinite(item.updatedAt) ? item.updatedAt : Date.now(),
  };
  if (typeof item.title === 'string') session.title = item.title;
  if (typeof item.model === 'string') session.model = item.model;
  if (item.reasoningEffort === 'auto' || item.reasoningEffort === 'low' || item.reasoningEffort === 'medium' || item.reasoningEffort === 'high') session.reasoningEffort = item.reasoningEffort;
  if (typeof item.draft === 'string') session.draft = item.draft;
  return session;
}

function loadSessions(): ChatSession[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
    if (!Array.isArray(value)) return [];
    return value.map(normaliseSession).filter((item): item is ChatSession => item !== null)
      .sort((left, right) => right.updatedAt - left.updatedAt);
  } catch {
    return [];
  }
}

function usageDay() {
  const date = new Date();
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function loadUsage(): ChatUsage {
  const empty = { day: usageDay(), today: 0, total: 0, inputTokens: 0, outputTokens: 0, totalTokens: 0 };
  try {
    const value: unknown = JSON.parse(localStorage.getItem(USAGE_KEY) || 'null');
    if (!value || typeof value !== 'object') return empty;
    const record = value as Partial<ChatUsage>;
    const total = typeof record.total === 'number' && Number.isFinite(record.total) ? Math.max(0, record.total) : 0;
    const today = record.day === empty.day && typeof record.today === 'number' && Number.isFinite(record.today)
      ? Math.max(0, record.today)
      : 0;
    const inputTokens = record.day === empty.day && typeof record.inputTokens === 'number' && Number.isFinite(record.inputTokens)
      ? Math.max(0, record.inputTokens)
      : 0;
    const outputTokens = record.day === empty.day && typeof record.outputTokens === 'number' && Number.isFinite(record.outputTokens)
      ? Math.max(0, record.outputTokens)
      : 0;
    const totalTokens = record.day === empty.day && typeof record.totalTokens === 'number' && Number.isFinite(record.totalTokens)
      ? Math.max(0, record.totalTokens)
      : inputTokens + outputTokens;
    return { day: empty.day, today, total, inputTokens, outputTokens, totalTokens };
  } catch {
    return empty;
  }
}

function responseUsage(payload: Record<string, unknown>): { inputTokens: number; outputTokens: number; totalTokens: number; contextMax: number | null } {
  const value = payload.usage;
  if (!value || typeof value !== 'object') return { inputTokens: 0, outputTokens: 0, totalTokens: 0, contextMax: null };
  const usage = value as Record<string, unknown>;
  const input = usage.prompt_tokens ?? usage.input_tokens;
  const output = usage.completion_tokens ?? usage.output_tokens;
  const total = usage.total_tokens ?? usage.totalTokens;
  const max = usage.context_window ?? usage.context_length ?? usage.max_context_tokens;
  const valid = (tokenValue: unknown) => typeof tokenValue === 'number' && Number.isFinite(tokenValue) && tokenValue >= 0 ? tokenValue : 0;
  const validMax = typeof max === 'number' && Number.isFinite(max) && max > 0 ? Math.floor(max) : null;
  return { inputTokens: valid(input), outputTokens: valid(output), totalTokens: valid(total) || valid(input) + valid(output), contextMax: validMax };
}

function formatTokens(value: number | null): string {
  return value === null ? '未知' : value.toLocaleString();
}

export function ChatPage({ publicHost, authenticated: authenticatedProp, onUnauthenticated }: ChatPageProps) {
  const [fallbackAuthenticated, setFallbackAuthenticated] = useState<boolean | null>(() => authenticatedProp === undefined ? null : authenticatedProp);
  const authenticated = authenticatedProp ?? fallbackAuthenticated === true;
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState('');
  const [settings, setSettings] = useState<ChatSettingsData | null>(null);
  const [settingsError, setSettingsError] = useState('');
  const [settingsFeedback, setSettingsFeedback] = useState('');
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [models, setModels] = useState<ChatModel[]>([]);
  const [modelsBusy, setModelsBusy] = useState(false);
  const [modelsError, setModelsError] = useState('');
  const [selectedModel, setSelectedModel] = useState('');
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>('auto');
  const [contextUsed, setContextUsed] = useState<number | null>(null);
  const [contextMax, setContextMax] = useState<number | null>(null);
  const [testBusy, setTestBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [streamStarted, setStreamStarted] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [search, setSearch] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [drawer, setDrawer] = useState<Drawer>(null);
  const [usage, setUsage] = useState<ChatUsage>(() => ({ day: usageDay(), today: 0, total: 0, inputTokens: 0, outputTokens: 0, totalTokens: 0 }));
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const messagesRef = useRef<HTMLDivElement>(null);
  const followMessagesRef = useRef(true);
  const userScrollIntentRef = useRef(false);
  const [localStateReady, setLocalStateReady] = useState(false);
  const authenticatedRef = useRef(authenticated);
  const streamAbortRef = useRef<AbortController | null>(null);

  const active = useMemo(() => sessions.find(session => session.id === activeId) || null, [sessions, activeId]);

  useEffect(() => {
    if (authenticatedProp !== undefined) {
      setFallbackAuthenticated(authenticatedProp);
      return;
    }
    let current = true;
    void fetch('/api/v1/session', {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    }).then(response => {
      if (current) setFallbackAuthenticated(response.ok);
    }).catch(() => {
      if (current) setFallbackAuthenticated(false);
    });
    return () => { current = false; };
  }, [authenticatedProp]);

  useEffect(() => {
    authenticatedRef.current = authenticated;
  }, [authenticated]);

  useEffect(() => {
    if (!authenticated || localStateReady) return;
    const initialSessions = loadSessions();
    setSessions(initialSessions);
    setActiveId(current => current || initialSessions[0]?.id || '');
    setUsage(loadUsage());
    setLocalStateReady(true);
  }, [authenticated, localStateReady]);

  useEffect(() => {
    if (!authenticated || !localStateReady) return;
    // Streaming updates are kept in React state and persisted once after the
    // request settles, rather than writing one localStorage record per delta.
    if (busy) return;
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions)); } catch { /* Storage is optional. */ }
  }, [authenticated, busy, localStateReady, sessions]);

  useEffect(() => {
    if (!authenticated || !localStateReady) return;
    try { localStorage.setItem(USAGE_KEY, JSON.stringify(usage)); } catch { /* Storage is optional. */ }
  }, [authenticated, localStateReady, usage]);

  useEffect(() => {
    if (!authenticated || !localStateReady) return;
    setMessage(active?.draft || '');
    setReasoningEffort(active?.reasoningEffort || 'auto');
  }, [activeId, authenticated, localStateReady]);

  useEffect(() => {
    if (!authenticated) return;
    let current = true;
    void loadChatSettings().then(value => {
      if (current) setSettings(value);
    }).catch(errorValue => {
      if (!current) return;
      if (errorValue instanceof ChatApiError && errorValue.status === 401) onUnauthenticated?.();
      else setSettingsError(errorValue instanceof Error ? errorValue.message : '设置加载失败');
    });
    return () => { current = false; };
  }, [authenticated, onUnauthenticated]);

  useEffect(() => {
    if (authenticated) return;
    setDrawer(null);
    setBusy(false);
    setSessions([]);
    setActiveId('');
    setLocalStateReady(false);
    setSettings(null);
    setSettingsError('');
    setSettingsFeedback('');
    setModels([]);
    setModelsBusy(false);
    setModelsError('');
    setSettingsBusy(false);
    setTestBusy(false);
    setContextUsed(null);
    setContextMax(null);
    setError('');
    setNotice('');
  }, [authenticated]);

  const refreshModels = useCallback(async () => {
    if (!authenticated) return;
    if (!settings?.api_key_configured || !settings.base_url.trim()) {
      setModels([]);
      setModelsError('先配置 API Base URL 和 API Key 后再获取模型。');
      return;
    }
    setModelsBusy(true);
    setModelsError('');
    try {
      const nextModels = await loadChatModels();
      if (authenticatedRef.current) setModels(nextModels);
    } catch (errorValue) {
      if (errorValue instanceof ChatApiError && errorValue.status === 401) onUnauthenticated?.();
      else setModelsError(errorValue instanceof Error ? errorValue.message : '模型列表加载失败');
    } finally {
      setModelsBusy(false);
    }
  }, [authenticated, onUnauthenticated, settings]);

  useEffect(() => {
    if (!authenticated) return;
    if (settings?.api_key_configured && settings.base_url.trim()) void refreshModels();
    else setModels([]);
  }, [authenticated, refreshModels, settings?.api_key_configured, settings?.base_url]);

  useEffect(() => {
    if (!models.length) {
      setSelectedModel('');
      setContextMax(null);
      return;
    }
    setSelectedModel(current => {
      const preferred = active?.model || current;
      if (models.some(item => item.id === preferred)) return preferred;
      if (models.some(item => item.id === DEFAULT_CHAT_MODEL)) return DEFAULT_CHAT_MODEL;
      return models.length === 1 ? models[0]?.id || '' : '';
    });
  }, [activeId, models]);

  useEffect(() => {
    const model = models.find(item => item.id === selectedModel);
    setContextMax(model?.context_window ?? null);
  }, [models, selectedModel]);

  useEffect(() => {
    if (!authenticated || !activeId) return;
    followMessagesRef.current = true;
    const frame = window.requestAnimationFrame(() => {
      const element = messagesRef.current;
      if (element) element.scrollTop = element.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeId, authenticated]);

  useEffect(() => {
    if (!authenticated || !activeId || !followMessagesRef.current) return;
    const frame = window.requestAnimationFrame(() => {
      const element = messagesRef.current;
      if (followMessagesRef.current && element) element.scrollTop = element.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeId, authenticated, sessions]);

  useEffect(() => {
    if (!authenticated) return;
    const element = messagesRef.current;
    if (!element || typeof MutationObserver === 'undefined') return;
    const observer = new MutationObserver(() => {
      if (!followMessagesRef.current) return;
      window.requestAnimationFrame(() => {
        if (followMessagesRef.current) element.scrollTop = element.scrollHeight;
      });
    });
    observer.observe(element, { childList: true, characterData: true, subtree: true });
    return () => observer.disconnect();
  }, [authenticated]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setDrawer(null);
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, []);

  const focusComposer = () => {
    window.requestAnimationFrame(() => composerRef.current?.focus({ preventScroll: true }));
  };
  const createSession = () => {
    if (!authenticated) return;
    const session: ChatSession = { id: newId(), title: '新对话', messages: [], updatedAt: Date.now(), ...(selectedModel ? { model: selectedModel } : {}), reasoningEffort };
    setSessions(current => [session, ...current]);
    setActiveId(session.id);
    setError('');
    setNotice('');
    focusComposer();
  };
  const clearSession = () => {
    if (!authenticated) return;
    if (!active || !window.confirm('清空当前对话？此操作不可撤销。')) return;
    setSessions(current => current.map(session => session.id === active.id ? { ...session, title: '新对话', messages: [], updatedAt: Date.now() } : session));
    setError('');
  };
  const renameSession = (session: ChatSession) => {
    if (!authenticated) return;
    const next = window.prompt('重命名对话', sessionTitle(session));
    if (!next?.trim()) return;
    setSessions(current => current.map(item => item.id === session.id ? { ...item, title: next.trim(), updatedAt: Date.now() } : item));
  };
  const deleteSession = (session: ChatSession) => {
    if (!authenticated) return;
    if (!window.confirm(`删除“${sessionTitle(session)}”？此操作不可撤销。`)) return;
    setSessions(current => current.filter(item => item.id !== session.id));
    if (session.id === activeId) {
      const next = sessions.find(item => item.id !== session.id);
      setActiveId(next?.id || '');
    }
  };
  const selectSession = (id: string) => {
    setActiveId(id);
    if (window.matchMedia('(max-width: 880px)').matches) setHistoryOpen(false);
  };

  const send = async () => {
    const content = message.trim();
    if (!authenticated || !content || busy) return;
    if (!selectedModel) {
      setError('请先在聊天顶部选择模型，或在设置中测试连接获取模型列表。');
      return;
    }
    const current: ChatSession = active || { id: newId(), title: '新对话', messages: [], updatedAt: Date.now(), ...(selectedModel ? { model: selectedModel } : {}), reasoningEffort };
    followMessagesRef.current = true;
    const userMessage: ChatMessageData = { role: 'user', content };
    const nextMessages = [...current.messages, userMessage];
    const nextSession: ChatSession = {
      ...current,
      model: selectedModel,
      reasoningEffort,
      draft: '',
      title: current.messages.length ? sessionTitle(current) : content.slice(0, 28),
      messages: nextMessages,
      updatedAt: Date.now(),
    };
    setSessions(existing => existing.some(session => session.id === current.id) ? existing.map(session => session.id === current.id ? nextSession : session) : [nextSession, ...existing]);
    setActiveId(current.id);
    setMessage('');
    setBusy(true);
    setStreamStarted(false);
    setError('');
    setNotice('');
    setUsage(currentUsage => ({
      ...currentUsage,
      day: usageDay(),
      today: (currentUsage.day === usageDay() ? currentUsage.today : 0) + 1,
      total: currentUsage.total + 1,
      inputTokens: currentUsage.day === usageDay() ? currentUsage.inputTokens : 0,
      outputTokens: currentUsage.day === usageDay() ? currentUsage.outputTokens : 0,
      totalTokens: currentUsage.day === usageDay() ? currentUsage.totalTokens : 0,
    }));
    const assistantMessage: ChatMessageData = { role: 'assistant', content: '' };
    setSessions(existing => existing.map(session => session.id === current.id ? { ...session, messages: [...nextMessages, assistantMessage], updatedAt: Date.now() } : session));
    const abortController = new AbortController();
    streamAbortRef.current = abortController;
    let reply = '';
    let streamError = '';
    let usageReceived = false;
    let flushFrame: number | null = null;
    const flushReply = () => {
      flushFrame = null;
      setSessions(existing => existing.map(session => session.id === current.id ? {
        ...session,
        messages: [...nextMessages, { role: 'assistant', content: reply }],
        updatedAt: Date.now(),
      } : session));
    };
    const appendReply = (text: string) => {
      reply += text;
      if (flushFrame === null) flushFrame = window.requestAnimationFrame(flushReply);
    };
    const applyUsage = (event: ChatStreamEvent) => {
      if (event.type !== 'usage' || usageReceived) return;
      usageReceived = true;
      const tokenUsage = responseUsage({ usage: event.usage });
      setContextUsed(tokenUsage.inputTokens || tokenUsage.totalTokens ? tokenUsage.inputTokens || tokenUsage.totalTokens : null);
      if (tokenUsage.contextMax !== null) setContextMax(tokenUsage.contextMax);
      if (tokenUsage.totalTokens || tokenUsage.inputTokens || tokenUsage.outputTokens) {
        setUsage(currentUsage => ({
          ...currentUsage,
          inputTokens: currentUsage.inputTokens + tokenUsage.inputTokens,
          outputTokens: currentUsage.outputTokens + tokenUsage.outputTokens,
          totalTokens: currentUsage.totalTokens + tokenUsage.totalTokens,
        }));
      }
    };
    const streamErrorMessage = (code: string) => {
      if (code === 'authentication_failed') return '第三方 API 认证失败，请检查 API Key。';
      if (code === 'model_not_found') return '当前模型不存在或不可用，请切换模型。';
      if (code === 'rate_limited') return '第三方 API 请求过于频繁，请稍后重试。';
      if (code === 'upstream_unavailable') return '模型服务当前没有可用容量，请稍后重试或切换模型。';
      if (code === 'timeout') return '连接第三方 API 超时，请稍后重试。';
      if (code === 'streaming_unavailable') return '当前 API 未返回可解析的流式或普通回复。';
      return '第三方 API 暂时不可用，请稍后重试。';
    };
    try {
      await streamChat(nextMessages, selectedModel, reasoningEffort, abortController.signal, event => {
        if (event.type === 'delta') {
          if (event.text) setStreamStarted(true);
          appendReply(event.text);
        }
        else if (event.type === 'usage') applyUsage(event);
        else if (event.type === 'notice' && event.notice === 'reasoning_unsupported') setNotice('当前 API 不支持思考强度，已按普通模式发送。');
        else if (event.type === 'error') {
          streamError = streamErrorMessage(event.error);
          setError(streamError);
        }
      });
      if (flushFrame !== null) {
        window.cancelAnimationFrame(flushFrame);
        flushReply();
      }
      if (!reply && !streamError && !abortController.signal.aborted) throw new Error('第三方 API 没有返回文本。');
      if (abortController.signal.aborted) setNotice('已停止生成。');
      if (!reply && (streamError || abortController.signal.aborted)) {
        setSessions(existing => existing.map(session => session.id === current.id ? { ...session, messages: nextMessages, updatedAt: Date.now() } : session));
      }
    } catch (errorValue) {
      if (abortController.signal.aborted || (errorValue instanceof DOMException && errorValue.name === 'AbortError')) {
        if (flushFrame !== null) {
          window.cancelAnimationFrame(flushFrame);
          flushReply();
        }
        setNotice('已停止生成。');
        if (!reply) setSessions(existing => existing.map(session => session.id === current.id ? { ...session, messages: nextMessages, updatedAt: Date.now() } : session));
      } else {
        if (errorValue instanceof ChatApiError && errorValue.status === 401) onUnauthenticated?.();
        else setError(errorValue instanceof Error ? errorValue.message : '发送失败');
      }
    } finally {
      if (flushFrame !== null) window.cancelAnimationFrame(flushFrame);
      if (streamAbortRef.current === abortController) streamAbortRef.current = null;
      setStreamStarted(false);
      if (authenticatedRef.current) {
        setBusy(false);
      }
    }
  };

  const saveSettings = async (values: SettingsUpdate): Promise<boolean> => {
    if (!authenticated) return false;
    setSettingsBusy(true);
    setSettingsError('');
    setSettingsFeedback('');
    try {
      const nextSettings = await saveChatSettings(values);
      if (!authenticatedRef.current) return false;
      setSettings(nextSettings);
      setSettingsFeedback('设置已保存');
      return true;
    } catch (errorValue) {
      if (errorValue instanceof ChatApiError && errorValue.status === 401) onUnauthenticated?.();
      else setSettingsError(errorValue instanceof Error ? errorValue.message : '设置保存失败');
      return false;
    } finally {
      setSettingsBusy(false);
    }
  };

  const selectModel = (model: string) => {
    setSelectedModel(model);
    setContextUsed(null);
    const selected = models.find(item => item.id === model);
    setContextMax(selected?.context_window ?? null);
    setSessions(current => current.map(session => session.id === activeId ? { ...session, model, updatedAt: Date.now() } : session));
  };
  const selectReasoning = (value: ReasoningEffort) => {
    setReasoningEffort(value);
    setSessions(current => current.map(session => session.id === activeId ? { ...session, reasoningEffort: value, updatedAt: Date.now() } : session));
  };
  const testConnection = async () => {
    if (!authenticated) return;
    setTestBusy(true);
    setSettingsFeedback('');
    try {
      const result = await testChatConnection();
      if (!authenticatedRef.current) return;
      setSettingsFeedback(`连接成功 · 发现 ${result.models_count} 个模型`);
      await refreshModels();
    } catch (errorValue) {
      if (errorValue instanceof ChatApiError && errorValue.status === 401) onUnauthenticated?.();
      else setSettingsFeedback(errorValue instanceof Error ? errorValue.message : '连接测试失败');
    } finally {
      setTestBusy(false);
    }
  };
  const clearLocalData = () => {
    if (!authenticated) return;
    if (!window.confirm('清空全部本地聊天记录和请求计数？此操作不可撤销。')) return;
    setSessions([]);
    setActiveId('');
    setUsage({ day: usageDay(), today: 0, total: 0, inputTokens: 0, outputTokens: 0, totalTokens: 0 });
    try { localStorage.removeItem(STORAGE_KEY); } catch { /* Storage is optional. */ }
    setSettingsFeedback('本地数据已清空');
  };
  const resizeComposer = (element: HTMLTextAreaElement) => {
    element.style.height = 'auto';
    element.style.height = `${Math.min(element.scrollHeight, 220)}px`;
  };
  const choosePrompt = (prompt: string) => {
    if (!authenticated) return;
    setMessage(prompt);
    focusComposer();
  };

  const toolbarModels = models;
  const toolbar = <div className="chat-topbar-controls">
    <button className={`btn btn-ghost btn-sm chat-history-toggle${historyOpen ? ' is-open' : ''}`} type="button" onClick={() => setHistoryOpen(current => !current)} aria-label={historyOpen ? '关闭历史记录' : '打开历史记录'} aria-expanded={historyOpen} aria-controls="chat-history-panel"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4.4 5.2h11.2M4.4 10h11.2M4.4 14.8h7.2" /></svg><span>历史</span><span className="chat-history-count">{sessions.length}</span></button>
    <label className="chat-toolbar-field"><span className="sr-only">模型</span>{toolbarModels.length ? <select className="chat-toolbar-select" aria-label="当前模型" value={selectedModel} onChange={event => selectModel(event.target.value)} disabled={!authenticated || !settings || modelsBusy}><option value="">选择模型</option>{toolbarModels.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select> : <input className="chat-toolbar-select chat-toolbar-model-input" aria-label="当前模型" value={selectedModel} onChange={event => selectModel(event.target.value)} placeholder="输入模型标识" disabled={!authenticated || !settings}/>}</label>
    <label className="chat-toolbar-field"><span className="sr-only">思考强度</span><select className="chat-toolbar-select chat-toolbar-select-small" aria-label="思考强度" value={reasoningEffort} onChange={event => selectReasoning(event.target.value as ReasoningEffort)} disabled={!authenticated || !settings}>{reasoningOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
    <span className="chat-context-status" aria-label="上下文状态">上下文：{formatTokens(contextUsed)} / {formatTokens(contextMax)}</span>
    <button className="btn btn-ghost btn-icon" type="button" aria-label="AI 用量" title="AI 用量" onClick={() => setDrawer('usage')} disabled={!authenticated}>⌁</button>
    <button className="btn btn-ghost btn-icon" type="button" aria-label="设置" title="设置" onClick={() => setDrawer('settings')} disabled={!authenticated}>⚙</button>
  </div>;

  return <CodexShell active="chat" badge={publicHost} pageTitle="AI 对话" topbarExtra={toolbar}>
    <section className="chat-page">
      {settingsError ? <div className="err" role="alert">{settingsError}</div> : null}
      <div className={`chat-layout${historyOpen ? '' : ' history-collapsed'}`}>
        {historyOpen ? <aside className="chat-history-panel card" id="chat-history-panel"><ChatSidebar
          sessions={sessions}
          activeId={activeId}
          search={search}
          usage={usage}
          onSearch={setSearch}
          onNew={createSession}
          onSelect={selectSession}
          onRename={renameSession}
          onDelete={deleteSession}
          onOpenSettings={() => setDrawer('settings')}
          onOpenUsage={() => setDrawer('usage')}
          onClose={() => setHistoryOpen(false)}
          disabled={!authenticated}
        /></aside> : null}
        <section className="chat-thread card">
          <header className="chat-thread-heading"><div className="chat-thread-title"><div><strong>{active ? sessionTitle(active) : '新对话'}</strong><span>{selectedModel || '尚未选择模型'}</span></div></div>{active ? <button className="btn btn-ghost btn-sm" type="button" onClick={clearSession} disabled={!authenticated}>清空</button> : null}</header>
          <div ref={messagesRef} className="chat-messages" role="log" aria-live="polite" onScroll={event => {
            if (!userScrollIntentRef.current) return;
            userScrollIntentRef.current = false;
            const element = event.currentTarget;
            followMessagesRef.current = element.scrollHeight - element.scrollTop - element.clientHeight <= 48;
          }} onWheel={() => { userScrollIntentRef.current = true; }} onTouchMove={() => { userScrollIntentRef.current = true; }} onPointerDown={() => { userScrollIntentRef.current = true; }}>
            {active?.messages.length ? active.messages.map((item, index) => <ChatMessage key={`${active.id}-${index}`} message={item} pending={busy && !streamStarted && item.role === 'assistant' && index === active.messages.length - 1 && !item.content} />) : <div className="chat-empty-state"><div className="chat-empty-mark">✦</div><h2>Lanco AI</h2><p>有什么可以帮你？</p><div className="chat-quick-prompts"><button type="button" onClick={() => choosePrompt('翻译一段文字：')} disabled={!authenticated}>翻译一段文字</button><button type="button" onClick={() => choosePrompt('请润色以下学术表达：')} disabled={!authenticated}>润色学术表达</button><button type="button" onClick={() => choosePrompt('请解释这段代码：')} disabled={!authenticated}>解释一段代码</button><button type="button" onClick={() => choosePrompt('')} disabled={!authenticated}>自由对话</button></div></div>}
          </div>
          <div className="chat-composer"><textarea ref={composerRef} value={message} onChange={event => { const next = event.target.value; setMessage(next); setSessions(current => current.map(session => session.id === activeId ? { ...session, draft: next } : session)); resizeComposer(event.currentTarget); }} onKeyDown={event => { if (event.nativeEvent.isComposing) return; if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send(); } }} placeholder="给 Lanco AI 发消息…" rows={1} disabled={!authenticated || busy} /><div className="chat-composer-footer"><span>{busy ? (streamStarted ? '正在接收流式回复…' : '模型正在思考，等待首个内容块…') : 'Enter 发送 · Shift + Enter 换行'}</span>{busy ? <button className="btn btn-secondary" type="button" onClick={() => streamAbortRef.current?.abort()}>停止</button> : <button className="btn btn-primary" type="button" onClick={() => void send()} disabled={!authenticated || !message.trim()}>发送 ↑</button>}</div></div>
        </section>
      </div>
      {notice ? <div className="chat-notice" role="status">{notice}</div> : null}
      {error ? <div className="err" role="alert">{error}</div> : null}
    </section>

    {drawer ? <div className="chat-drawer-layer"><button className="chat-drawer-backdrop" type="button" aria-label="关闭抽屉" onClick={() => setDrawer(null)} /><aside className="chat-drawer" role="dialog" aria-modal="true" aria-labelledby="chat-drawer-title"><header className="chat-drawer-header"><div><h2 id="chat-drawer-title">{drawer === 'settings' ? '设置' : 'AI 用量'}</h2><p>{drawer === 'settings' ? '低频配置集中在这里' : '只记录本地请求次数，不估算 Token'}</p></div><button className="btn btn-ghost btn-icon" type="button" aria-label="关闭设置" onClick={() => setDrawer(null)}>×</button></header>{drawer === 'settings' ? <ChatSettings settings={settings} models={models} modelsBusy={modelsBusy} modelsError={modelsError} busy={settingsBusy} testBusy={testBusy} feedback={settingsFeedback || (settingsError && !settings ? settingsError : '')} onSave={saveSettings} onRefreshModels={refreshModels} onTest={testConnection} onClearData={clearLocalData} /> : <div className="chat-usage"><div className="chat-usage-grid"><div><span>今日请求</span><strong>{usage.today}</strong></div><div><span>总请求</span><strong>{usage.total}</strong></div></div>{usage.inputTokens || usage.outputTokens ? <div className="chat-usage-grid"><div><span>今日输入 tokens</span><strong>{usage.inputTokens}</strong></div><div><span>今日输出 tokens</span><strong>{usage.outputTokens}</strong></div><div><span>今日总 tokens</span><strong>{usage.totalTokens}</strong></div></div> : <div className="chat-usage-empty"><strong>暂无 Token 数据</strong><p>第三方 API 未返回 usage 时不会估算或伪造统计。</p></div>}</div>}</aside></div> : null}
  </CodexShell>;
}
