import { useEffect, useState, type ReactElement } from 'react';
import { loadVideoSettings, saveVideoSettings, testVideoConnection } from './videoApi';
import type { VideoSettings } from './videoTypes';

export function VideoSettingsDrawer({ open, onClose }: { open: boolean; onClose: () => void }): ReactElement | null {
  const [settings, setSettings] = useState<VideoSettings | null>(null);
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!open) return; void loadVideoSettings().then(result => { setSettings(result); setBaseUrl(result.base_url); }).catch(() => setMessage('无法读取设置')); }, [open]);
  if (!open) return null;
  const save = async () => {
    setBusy(true); setMessage('');
    try { const result = await saveVideoSettings({ baseUrl, ...(apiKey ? { apiKey } : {}) }); setSettings(result); setApiKey(''); setMessage('已保存'); }
    catch (error) { setMessage(error instanceof Error ? error.message : '保存失败'); }
    finally { setBusy(false); }
  };
  const test = async () => {
    setBusy(true); setMessage('测试中…');
    try { const result = await testVideoConnection(); setMessage(`连接成功，可用模型 ${result.models_count} 个`); }
    catch (error) { setMessage(error instanceof Error ? error.message : '连接失败'); }
    finally { setBusy(false); }
  };
  return <aside className="video-settings-drawer" aria-label="视频 API 设置"><div className="video-drawer-header"><h2>API 设置</h2><button type="button" className="button ghost" onClick={onClose}>关闭</button></div><label>API Base URL<input value={baseUrl} onChange={event => setBaseUrl(event.target.value)} placeholder="https://example.com/v1" /></label><label>API Key<input type="password" value={apiKey} onChange={event => setApiKey(event.target.value)} placeholder={settings?.api_key_configured ? settings.api_key_masked : '仅保存在服务器'} autoComplete="new-password" /></label><div className="video-drawer-actions"><button type="button" className="button secondary" onClick={test} disabled={busy || !settings?.api_key_configured}>测试连接</button><button type="button" className="button primary" onClick={save} disabled={busy || !baseUrl}>保存</button></div><p role="status">{message}</p></aside>;
}
