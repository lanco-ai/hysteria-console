import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { WorkspaceDialog } from './WorkspaceDialog';
import { saveChatSettings, type ChatModel, type ChatSettings, type ReasoningEffort } from './chatApi';
import { saveDownload, workspaceRequest as api, WorkspaceApiError, type WorkspacePreferences, type WorkspaceUsage } from './workspaceApi';

export type SendKey = 'enter' | 'mod-enter';
export const SEND_KEY_STORAGE = 'hy2.chat.send-key';
const INSTRUCTIONS_LIMIT = 2000;

type Section = 'defaults' | 'instructions' | 'input' | 'usage' | 'data';
const SECTIONS: { key: Section; label: string }[] = [
  { key: 'defaults', label: '模型与默认值' },
  { key: 'instructions', label: '自定义指令' },
  { key: 'input', label: '输入习惯' },
  { key: 'usage', label: '用量' },
  { key: 'data', label: '数据' },
];
const REASONING: { value: ReasoningEffort; label: string }[] = [
  { value: 'auto', label: '自动' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
];

export function AISettingsDialog({ settings, models, preferences, sendKey, legacyCount, locked, returnFocusTo, onSendKey, onPreferences, onSettings, onImportLegacy, onError, onClose }: {
  settings: ChatSettings | null;
  models: ChatModel[];
  preferences: WorkspacePreferences | null;
  sendKey: SendKey;
  legacyCount: number;
  locked: boolean;
  returnFocusTo: HTMLElement | null;
  onSendKey: (value: SendKey) => void;
  onPreferences: (preferences: WorkspacePreferences) => void;
  onSettings: (settings: ChatSettings) => void;
  onImportLegacy: () => void;
  onError: (error: unknown) => void;
  onClose: () => void;
}) {
  const [section, setSection] = useState<Section>('defaults');
  const [model, setModel] = useState(preferences?.default_model ?? '');
  const [reasoning, setReasoning] = useState<ReasoningEffort>(preferences?.default_reasoning ?? 'auto');
  const [temperature, setTemperature] = useState(String(settings?.temperature ?? 0.7));
  const [instructions, setInstructions] = useState(preferences?.instructions ?? '');
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState('');
  const [failure, setFailure] = useState('');
  // After a conflict the next save deliberately overwrites the newer server copy with these edits.
  const [revision, setRevision] = useState(preferences?.revision ?? 0);
  const [usage, setUsage] = useState<WorkspaceUsage | null>(null);

  // Reset the form only when the server copy really changed, so a refetch cannot clobber typing.
  const serverRevision = preferences?.revision;
  useEffect(() => {
    if (preferences) { setModel(preferences.default_model); setReasoning(preferences.default_reasoning); setInstructions(preferences.instructions); setRevision(preferences.revision); }
  }, [serverRevision]);
  useEffect(() => { if (settings) setTemperature(String(settings.temperature)); }, [settings]);
  // Errors stay inside the dialog; only an expired session is handed to the page.
  const fail = useCallback((error: unknown) => {
    setSaved('');
    setFailure(error instanceof Error ? error.message : '操作失败，请重试。');
    if ((error as { status?: number } | null)?.status === 401) onError(error);
  }, [onError]);
  useEffect(() => {
    if (section !== 'usage') return;
    let live = true;
    void api<WorkspaceUsage>('/workspace/usage').then(value => { if (live) setUsage(value); }).catch(fail);
    return () => { live = false; };
  }, [section, fail]);

  const dirty = !!preferences && (model !== preferences.default_model || reasoning !== preferences.default_reasoning || instructions.trim() !== preferences.instructions
    || (!!settings && Number(temperature) !== settings.temperature));
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!preferences || busy) return;
    setBusy(true); setSaved(''); setFailure('');
    try {
      const next = await api<WorkspacePreferences>('/workspace/preferences', 'PUT', { revision, instructions, default_model: model, default_reasoning: reasoning });
      setRevision(next.revision); onPreferences(next);
      if (settings && Number(temperature) !== settings.temperature) onSettings(await saveChatSettings({ temperature: Number(temperature) }));
      setSaved('已保存，下一条消息开始生效');
    } catch (error) {
      if (error instanceof WorkspaceApiError && error.code === 'revision_conflict') {
        try {
          setRevision((await api<WorkspacePreferences>('/workspace/preferences')).revision);
          setFailure('AI 设置刚在其他窗口修改过。你的修改仍保留在这里，再次保存会覆盖那次修改。');
        } catch (reload) { fail(reload); }
      } else fail(error);
    } finally { setBusy(false); }
  };
  const saveBar = <div className="ai-settings-save">
    {failure ? <span className="ai-settings-failure" role="alert">{failure}</span> : <span className="workspace-muted" role="status">{saved || (dirty ? '有未保存的修改' : '')}</span>}
    <button className="btn btn-primary" type="submit" disabled={busy || locked || !preferences || !dirty}>{busy ? '保存中…' : '保存设置'}</button>
  </div>;

  const close = () => { if (!dirty || busy || window.confirm('放弃尚未保存的 AI 设置修改？')) onClose(); };

  return <WorkspaceDialog title="AI 设置" className="ai-settings-dialog" returnFocusTo={returnFocusTo} onClose={close}>
    <div className="ai-settings">
      <nav className="ai-settings-nav" aria-label="AI 设置分类">
        {SECTIONS.map(item => <button key={item.key} type="button" aria-current={section === item.key ? 'page' : undefined} onClick={() => setSection(item.key)}>{item.label}</button>)}
      </nav>
      <form className="ai-settings-body" onSubmit={save}>
        {section === 'defaults' ? <>
          <section className="ai-settings-card">
            <h3>模型服务</h3>
            <p className={`ai-service-status${settings?.api_key_configured ? ' is-ready' : ''}`}>
              <span aria-hidden="true"/>{settings?.api_key_configured ? `已连接${settings.service_name ? ` · ${settings.service_name}` : ''} · ${models.length} 个可用模型` : '尚未配置聊天服务'}
            </p>
            <p className="workspace-muted">API 地址、密钥和连接测试统一在服务中心管理，密钥不会显示在这里。</p>
            <a className="btn btn-sm" href="/admin/services#ai-services">前往服务中心</a>
          </section>
          <section className="ai-settings-card">
            <h3>新对话默认值</h3>
            <p className="workspace-muted">只影响新开的对话；已有对话保留各自选择的模型和思考强度。</p>
            <label className="field"><span className="label">默认模型</span>
              <select className="select" value={model} onChange={event => setModel(event.target.value)}>
                <option value="">第一个可用模型</option>
                {model && !models.some(item => item.id === model) ? <option value={model}>{model}（当前不可用）</option> : null}
                {models.map(item => <option key={item.id} value={item.id}>{item.name || item.id}</option>)}
              </select>
            </label>
            <label className="field"><span className="label">默认思考强度</span>
              <select className="select" value={reasoning} onChange={event => setReasoning(event.target.value as ReasoningEffort)}>
                {REASONING.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
            <label className="field" htmlFor="chat-temperature"><span className="label">Temperature · {Number(temperature).toFixed(1)}</span>
              <span className="ai-settings-range">
                <input type="range" min="0" max="2" step="0.1" value={temperature} aria-label="Temperature 滑块" onChange={event => setTemperature(event.target.value)}/>
                <input id="chat-temperature" className="input" type="number" min="0" max="2" step="0.1" value={temperature} onChange={event => setTemperature(event.target.value)} required/>
              </span>
              <small className="workspace-muted">越低越稳定，越高越发散；保存在服务中心的聊天服务上，所有使用它的功能共用。</small>
            </label>
          </section>
          {saveBar}
        </> : null}

        {section === 'instructions' ? <>
          <section className="ai-settings-card">
            <h3>自定义指令</h3>
            <p className="workspace-muted">附加到每个对话的系统提示里，例如你的背景、回答语言和格式偏好。项目指令和本次对话中的明确要求优先。</p>
            <label className="field"><span className="sr-only">自定义指令</span>
              <textarea className="input ai-instructions" aria-label="自定义指令" rows={10} maxLength={INSTRUCTIONS_LIMIT} value={instructions} placeholder={'例如：\n我是存储方向的研究生，回答尽量用中文。\n先给结论，再给推理过程和可验证的依据。'} onChange={event => setInstructions(event.target.value)}/>
            </label>
            <span className={`ai-instructions-count${instructions.length > INSTRUCTIONS_LIMIT * 0.9 ? ' is-near' : ''}`}>{instructions.length} / {INSTRUCTIONS_LIMIT}</span>
          </section>
          {saveBar}
        </> : null}

        {section === 'input' ? <section className="ai-settings-card">
          <h3>发送方式</h3>
          <p className="workspace-muted">只保存在当前浏览器。</p>
          <div className="ai-settings-options" role="radiogroup" aria-label="发送方式">
            <label className={`ai-settings-option${sendKey === 'enter' ? ' is-selected' : ''}`}><input type="radio" name="send-key" checked={sendKey === 'enter'} onChange={() => onSendKey('enter')}/><span><strong>Enter 发送</strong><small>Shift + Enter 换行</small></span></label>
            <label className={`ai-settings-option${sendKey === 'mod-enter' ? ' is-selected' : ''}`}><input type="radio" name="send-key" checked={sendKey === 'mod-enter'} onChange={() => onSendKey('mod-enter')}/><span><strong>Ctrl / ⌘ + Enter 发送</strong><small>Enter 换行，适合写长段落</small></span></label>
          </div>
        </section> : null}

        {section === 'usage' ? <section className="ai-settings-card">
          <h3>AI 用量</h3>
          {usage ? <>
            <dl className="ai-usage-grid">
              <div><dt>请求</dt><dd>{usage.requests.toLocaleString()}</dd></div>
              <div><dt>输入 tokens</dt><dd>{usage.prompt_tokens.toLocaleString()}</dd></div>
              <div><dt>输出 tokens</dt><dd>{usage.completion_tokens.toLocaleString()}</dd></div>
            </dl>
            <p className="workspace-muted">其中 {usage.reported_requests} 次由服务返回了用量。包含自动摘要和工具建议请求，分支共用的历史只计一次；未返回的用量不估算，不包含导入的旧记录与已删除的对话。</p>
          </> : <p className="workspace-muted">正在读取用量…</p>}
        </section> : null}

        {section === 'data' ? <section className="ai-settings-card">
          <h3>数据</h3>
          <p className="workspace-muted">对话、项目和 AI 设置保存在服务器，可在其他设备登录后继续使用。导出包含对话、项目与引用片段；论文原文件请从资料列表下载。</p>
          <div className="workspace-actions">
            <button className="btn btn-sm" type="button" disabled={locked} onClick={() => { setFailure(''); void api<unknown>('/workspace/export').then(value => saveDownload(JSON.stringify(value, null, 2), 'learning-workspace.json')).catch(fail); }}>导出全部对话与项目</button>
            {legacyCount > 0 ? <button className="btn btn-sm" type="button" disabled={locked} onClick={onImportLegacy}>导入此浏览器的旧对话（{legacyCount}）</button> : null}
          </div>
        </section> : null}
      </form>
    </div>
  </WorkspaceDialog>;
}
