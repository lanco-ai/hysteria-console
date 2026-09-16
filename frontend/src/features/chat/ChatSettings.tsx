import { useEffect, useState } from 'react';
import type { ChatSettings } from './chatApi';

type ChatSettingsProps = {
  settings: ChatSettings | null;
  busy: boolean;
  onSave: (values: { base_url: string; model: string; temperature: number; api_key?: string }) => Promise<void>;
};

export function ChatSettings({ settings, busy, onSave }: ChatSettingsProps) {
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [temperature, setTemperature] = useState('0.7');
  const [apiKey, setApiKey] = useState('');
  const [clearKey, setClearKey] = useState(false);

  useEffect(() => {
    if (!settings) return;
    setBaseUrl(settings.base_url);
    setModel(settings.model);
    setTemperature(String(settings.temperature));
    setApiKey('');
    setClearKey(false);
  }, [settings]);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const values: { base_url: string; model: string; temperature: number; api_key?: string } = {
      base_url: baseUrl.trim(),
      model: model.trim(),
      temperature: Number(temperature),
    };
    if (clearKey) values.api_key = '';
    else if (apiKey) values.api_key = apiKey;
    await onSave(values);
    setApiKey('');
    setClearKey(false);
  };

  return <form className="chat-settings" onSubmit={submit}>
    <div className="chat-settings-grid">
      <label className="field"><span className="label">API Base URL</span><input className="input" type="url" value={baseUrl} onChange={event => setBaseUrl(event.target.value)} placeholder="https://example.com/v1" required /></label>
      <label className="field"><span className="label">Model</span><input className="input" value={model} onChange={event => setModel(event.target.value)} placeholder="gemini-xxx" required /></label>
      <label className="field"><span className="label">Temperature</span><input className="input" type="number" min="0" max="2" step="0.1" value={temperature} onChange={event => setTemperature(event.target.value)} required /></label>
      <label className="field"><span className="label">API Key</span><input className="input" type="password" value={apiKey} onChange={event => setApiKey(event.target.value)} placeholder={settings?.api_key_configured ? settings.api_key_masked : 'sk-xxx'} autoComplete="new-password" /></label>
    </div>
    <div className="chat-settings-footer">
      {settings?.api_key_configured ? <label className="chat-check"><input type="checkbox" checked={clearKey} onChange={event => setClearKey(event.target.checked)} /> 清除已保存的 Key</label> : <span className="muted">API Key 仅保存在服务器</span>}
      <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? '保存中…' : '保存设置'}</button>
    </div>
  </form>;
}
