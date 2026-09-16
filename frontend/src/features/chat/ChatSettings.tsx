import { useEffect, useState, type FormEvent } from 'react';
import type { ChatModel, ChatSettings as ChatSettingsData, SettingsUpdate } from './chatApi';

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
  const [temperature, setTemperature] = useState('0.7');
  const [apiKey, setApiKey] = useState('');
  const [clearKey, setClearKey] = useState(false);

  useEffect(() => {
    if (!settings) return;
    setBaseUrl(settings.base_url);
    setTemperature(String(settings.temperature));
    setApiKey('');
    setClearKey(false);
  }, [settings]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const values: SettingsUpdate = {
      base_url: baseUrl.trim(),
      temperature: Number(temperature),
    };
    if (clearKey) values.api_key = '';
    else if (apiKey) values.api_key = apiKey;
    if (await onSave(values)) {
      setApiKey('');
      setClearKey(false);
    }
  };

  return <form className="chat-settings" onSubmit={submit}>
    <section className="chat-settings-section">
      <div className="chat-settings-section-heading">
        <div><h3>模型与接口</h3><p>API Key 仅保存在服务器，浏览器不会收到完整密钥。</p></div>
        <button className="btn btn-ghost btn-sm" type="button" onClick={() => void onRefreshModels()} disabled={modelsBusy || !settings?.api_key_configured || !baseUrl.trim()}>{modelsBusy ? '刷新中…' : '刷新模型'}</button>
      </div>
      <label className="field" htmlFor="chat-base-url"><span className="label">API Base URL</span><input id="chat-base-url" className="input" type="url" value={baseUrl} onChange={event => setBaseUrl(event.target.value)} placeholder="https://example.com/v1" required /></label>
      <label className="field" htmlFor="chat-api-key"><span className="label">API Key</span><input id="chat-api-key" className="input" type="password" value={apiKey} onChange={event => setApiKey(event.target.value)} placeholder={settings?.api_key_configured ? settings.api_key_masked : 'sk-xxx'} autoComplete="new-password" /></label>
      {settings?.api_key_configured ? <label className="chat-check"><input type="checkbox" checked={clearKey} onChange={event => setClearKey(event.target.checked)} /> 清除已保存的 Key</label> : <p className="chat-settings-note">尚未配置 API Key</p>}
      <p className="chat-settings-note">模型连接成功后会保存在当前会话的可用列表中；若接口不提供列表，可在聊天顶部手动输入模型标识。</p>
      {modelsError ? <p className="chat-settings-error" role="alert">{modelsError}</p> : null}
    </section>

    <section className="chat-settings-section">
      <div className="chat-settings-section-heading"><div><h3>聊天参数</h3><p>Temperature 仅作为请求参数；思考强度在聊天顶部按当前对话选择。</p></div></div>
      <label className="field" htmlFor="chat-temperature"><span className="label">Temperature</span><input id="chat-temperature" className="input" type="number" min="0" max="2" step="0.1" value={temperature} onChange={event => setTemperature(event.target.value)} required /></label>
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
