import { useEffect, useState, type FormEvent } from 'react';
import type { ChatModel, ChatSettings as ChatSettingsData, ReasoningEffort, SettingsUpdate } from './chatApi';

type ChatSettingsProps = {
  settings: ChatSettingsData | null;
  models: ChatModel[];
  busy: boolean;
  modelsBusy: boolean;
  modelsError: string;
  testBusy: boolean;
  feedback: string;
  onSave: (values: SettingsUpdate) => Promise<boolean>;
  onRefreshModels: () => Promise<void>;
  onTest: () => Promise<void>;
  onClearData: () => void;
};

const reasoningOptions: Array<{ value: ReasoningEffort; label: string }> = [
  { value: 'auto', label: '自动' },
  { value: 'low', label: '低' },
  { value: 'medium', label: '中' },
  { value: 'high', label: '高' },
];

export function ChatSettings({
  settings,
  models,
  busy,
  modelsBusy,
  modelsError,
  testBusy,
  feedback,
  onSave,
  onRefreshModels,
  onTest,
  onClearData,
}: ChatSettingsProps) {
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [temperature, setTemperature] = useState('0.7');
  const [apiKey, setApiKey] = useState('');
  const [clearKey, setClearKey] = useState(false);
  const [reasoningEnabled, setReasoningEnabled] = useState(false);
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>('auto');

  useEffect(() => {
    if (!settings) return;
    setBaseUrl(settings.base_url);
    setModel(settings.model);
    setTemperature(String(settings.temperature));
    setApiKey('');
    setClearKey(false);
    setReasoningEnabled(settings.reasoning_enabled);
    setReasoningEffort(settings.reasoning_effort);
  }, [settings]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const values: SettingsUpdate = {
      base_url: baseUrl.trim(),
      model: model.trim(),
      temperature: Number(temperature),
      reasoning_enabled: reasoningEnabled,
      reasoning_effort: reasoningEffort,
    };
    if (clearKey) values.api_key = '';
    else if (apiKey) values.api_key = apiKey;
    if (await onSave(values)) {
      setApiKey('');
      setClearKey(false);
    }
  };

  const modelOptions = models.some(item => item.id === model) || !model
    ? models
    : [{ id: model, name: model }, ...models];

  return <form className="chat-settings" onSubmit={submit}>
    <section className="chat-settings-section">
      <div className="chat-settings-section-heading">
        <div><h3>模型与接口</h3><p>API Key 仅保存在服务器，浏览器不会收到完整密钥。</p></div>
        <button className="btn btn-ghost btn-sm" type="button" onClick={() => void onRefreshModels()} disabled={modelsBusy || !settings?.api_key_configured || !baseUrl.trim()}>{modelsBusy ? '刷新中…' : '刷新模型'}</button>
      </div>
      <label className="field" htmlFor="chat-base-url"><span className="label">API Base URL</span><input id="chat-base-url" className="input" type="url" value={baseUrl} onChange={event => setBaseUrl(event.target.value)} placeholder="https://example.com/v1" required /></label>
      <label className="field" htmlFor="chat-api-key"><span className="label">API Key</span><input id="chat-api-key" className="input" type="password" value={apiKey} onChange={event => setApiKey(event.target.value)} placeholder={settings?.api_key_configured ? settings.api_key_masked : 'sk-xxx'} autoComplete="new-password" /></label>
      {settings?.api_key_configured ? <label className="chat-check"><input type="checkbox" checked={clearKey} onChange={event => setClearKey(event.target.checked)} /> 清除已保存的 Key</label> : <p className="chat-settings-note">尚未配置 API Key</p>}
      <div className="chat-settings-model-row">
        <label className="field" htmlFor="chat-model"><span className="label">默认模型</span><select id="chat-model" className="select" value={model} onChange={event => setModel(event.target.value)} disabled={modelsBusy && models.length === 0}><option value="">手动输入 Model ID</option>{modelOptions.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className="field" htmlFor="chat-model-id"><span className="label">Model ID</span><input id="chat-model-id" className="input" value={model} onChange={event => setModel(event.target.value)} placeholder="gemini-xxx" required /></label>
      </div>
      <p className="chat-settings-note">模型列表失败时仍可直接填写 Model ID。</p>
      {modelsError ? <p className="chat-settings-error" role="alert">{modelsError}</p> : null}
    </section>

    <section className="chat-settings-section">
      <div className="chat-settings-section-heading"><div><h3>聊天参数</h3><p>思考强度默认关闭，兼容不支持该字段的 API。</p></div></div>
      <div className="chat-settings-model-row">
        <label className="field" htmlFor="chat-temperature"><span className="label">Temperature</span><input id="chat-temperature" className="input" type="number" min="0" max="2" step="0.1" value={temperature} onChange={event => setTemperature(event.target.value)} required /></label>
        <label className="field" htmlFor="chat-reasoning-effort"><span className="label">默认思考强度</span><select id="chat-reasoning-effort" className="select" value={reasoningEffort} onChange={event => setReasoningEffort(event.target.value as ReasoningEffort)} disabled={!reasoningEnabled}>{reasoningOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
      </div>
      <label className="chat-toggle"><input type="checkbox" checked={reasoningEnabled} onChange={event => setReasoningEnabled(event.target.checked)} /><span><strong>启用思考强度参数</strong><small>关闭时不会向第三方 API 发送 reasoning_effort。</small></span></label>
    </section>

    <section className="chat-settings-section chat-settings-data">
      <div className="chat-settings-section-heading"><div><h3>数据</h3><p>聊天记录只在当前浏览器保存。</p></div></div>
      <button className="btn btn-danger" type="button" onClick={onClearData}>清空本地聊天记录</button>
    </section>

    {feedback ? <div className="chat-settings-feedback" role="status">{feedback}</div> : null}
    <div className="chat-settings-footer">
      <button className="btn btn-secondary" type="button" onClick={() => void onTest()} disabled={testBusy || busy || !settings?.api_key_configured}>{testBusy ? '测试中…' : '测试连接'}</button>
      <button className="btn btn-primary" type="submit" disabled={busy || !settings}>{busy ? '保存中…' : '保存设置'}</button>
    </div>
  </form>;
}
