import { useEffect, useMemo, useState } from 'react';
import { AdminShell } from '../../shared/AdminShell';
import { ChatMessage } from './ChatMessage';
import { ChatSettings } from './ChatSettings';
import { completeChat, loadChatSettings, saveChatSettings, type ChatMessageData, type ChatSettings as ChatSettingsData } from './chatApi';

type ChatSession = { id: string; title: string; messages: ChatMessageData[]; updatedAt: number };
const STORAGE_KEY = 'hy2.chat.sessions.v1';

function newId() {
  return typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
function loadSessions(): ChatSession[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
    if (!Array.isArray(value)) return [];
    return value.filter(item => item && typeof item === 'object' && Array.isArray((item as ChatSession).messages)) as ChatSession[];
  } catch {
    return [];
  }
}

function assistantText(payload: Record<string, unknown>): string {
  const choices = payload.choices;
  if (!Array.isArray(choices)) return '';
  const first = choices[0];
  if (!first || typeof first !== 'object') return '';
  const message = (first as Record<string, unknown>).message;
  if (!message || typeof message !== 'object') return '';
  const content = (message as Record<string, unknown>).content;
  return typeof content === 'string' ? content : '';
}

export function ChatPage({ publicHost }: { publicHost: string }) {
  const [sessions, setSessions] = useState<ChatSession[]>(loadSessions);
  const [activeId, setActiveId] = useState(() => loadSessions()[0]?.id || '');
  const [settings, setSettings] = useState<ChatSettingsData | null>(null);
  const [settingsError, setSettingsError] = useState('');
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const active = useMemo(() => sessions.find(session => session.id === activeId) || null, [sessions, activeId]);
  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions)); } catch { /* Storage is optional. */ }
  }, [sessions]);
  useEffect(() => {
    void loadChatSettings().then(setSettings).catch(errorValue => setSettingsError(errorValue instanceof Error ? errorValue.message : '设置加载失败'));
  }, []);

  const createSession = () => {
    const session = { id: newId(), title: '新对话', messages: [], updatedAt: Date.now() };
    setSessions(current => [session, ...current]);
    setActiveId(session.id);
    setError('');
  };
  const clearSession = () => {
    if (!active) return;
    setSessions(current => current.map(session => session.id === active.id ? { ...session, title: '新对话', messages: [], updatedAt: Date.now() } : session));
    setError('');
  };
  const send = async () => {
    const content = message.trim();
    if (!content || busy) return;
    const current = active || { id: newId(), title: '新对话', messages: [], updatedAt: Date.now() };
    const userMessage: ChatMessageData = { role: 'user', content };
    const nextMessages = [...current.messages, userMessage];
    const nextSession = { ...current, title: current.messages.length ? current.title : content.slice(0, 24), messages: nextMessages, updatedAt: Date.now() };
    setSessions(existing => existing.some(session => session.id === current.id) ? existing.map(session => session.id === current.id ? nextSession : session) : [nextSession, ...existing]);
    setActiveId(current.id);
    setMessage('');
    setBusy(true);
    setError('');
    try {
      const reply = assistantText(await completeChat(nextMessages));
      if (!reply) throw new Error('第三方 API 没有返回文本。');
      const assistantMessage: ChatMessageData = { role: 'assistant', content: reply };
      setSessions(existing => existing.map(session => session.id === current.id ? { ...session, messages: [...nextMessages, assistantMessage], updatedAt: Date.now() } : session));
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : '发送失败');
    } finally {
      setBusy(false);
    }
  };
  const saveSettings = async (values: { base_url: string; model: string; temperature: number; api_key?: string }) => {
    setSettingsBusy(true);
    setSettingsError('');
    try { setSettings(await saveChatSettings(values)); } catch (errorValue) { setSettingsError(errorValue instanceof Error ? errorValue.message : '设置保存失败'); } finally { setSettingsBusy(false); }
  };

  return <AdminShell active="chat" badge={publicHost} pageTitle="AI 对话">
    <section className="chat-page">
      <div className="chat-toolbar"><div><h2 className="section-title">自用 AI 对话</h2><p className="muted">使用你配置的 OpenAI-Compatible API，聊天记录只保存在当前浏览器。</p></div><button className="btn btn-primary" type="button" onClick={createSession}>新对话</button></div>
      <div className="chat-layout">
        <aside className="chat-sidebar card"><div className="chat-sidebar-heading"><strong>会话</strong><span className="muted">{sessions.length}</span></div>{sessions.length === 0 ? <p className="muted chat-empty">还没有会话</p> : sessions.map(session => <button key={session.id} type="button" className={`chat-session ${session.id === activeId ? 'active' : ''}`} onClick={() => setActiveId(session.id)}><span>{session.title}</span><small>{session.messages.length} 条</small></button>)}</aside>
        <section className="chat-thread card"><div className="chat-thread-heading"><strong>{active?.title || '新对话'}</strong>{active ? <button className="btn btn-ghost btn-sm" type="button" onClick={clearSession}>清空</button> : null}</div><div className="chat-messages">{active?.messages.length ? active.messages.map((item, index) => <ChatMessage key={`${active.id}-${index}`} message={item} />) : <div className="chat-placeholder"><strong>开始一段对话</strong><span>输入问题，发送后会将完整上下文提交给已配置的模型。</span></div>}</div><div className="chat-composer"><textarea value={message} onChange={event => setMessage(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send(); } }} placeholder="输入消息，Enter 发送，Shift + Enter 换行" rows={3} disabled={busy} /><div className="chat-composer-footer"><span className="muted">{busy ? '正在等待回复…' : '文本对话'}</span><button className="btn btn-primary" type="button" onClick={() => void send()} disabled={busy || !message.trim()}>{busy ? '发送中…' : '发送'}</button></div></div></section>
      </div>
      {error ? <div className="err" role="alert">{error}</div> : null}
      <details className="card chat-settings-card" open><summary>API 设置</summary><div className="chat-settings-body">{settingsError ? <div className="err" role="alert">{settingsError}</div> : null}<ChatSettings settings={settings} busy={settingsBusy} onSave={saveSettings} /></div></details>
    </section>
  </AdminShell>;
}
