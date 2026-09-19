import { useEffect, useState, type FormEvent } from 'react';
import type { ChatSettings as ChatSettingsData, SettingsUpdate } from './chatApi';

type ChatSettingsProps = {
  settings: ChatSettingsData | null;
  busy: boolean;
  feedback: string;
  onSave: (values: SettingsUpdate) => Promise<boolean>;
  onClearData: () => void;
};

export function ChatSettings({ settings, busy, feedback, onSave, onClearData }: ChatSettingsProps) {
  const [temperature, setTemperature] = useState('0.7');

  useEffect(() => {
    if (settings) setTemperature(String(settings.temperature));
  }, [settings]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await onSave({ temperature: Number(temperature) });
  };

  return <form className="chat-settings" onSubmit={submit}>
    <section className="chat-settings-section">
      <div className="chat-settings-section-heading">
        <div>
          <h3>模型服务</h3>
          <p>{settings?.api_key_configured ? '聊天服务已在服务中心配置。' : '尚未配置聊天服务，先在服务中心添加并绑定模型。'}</p>
        </div>
      </div>
      <a className="btn btn-secondary" href="/admin/services?tab=ai">前往服务中心管理 API</a>
      <p className="chat-settings-note">API 地址、凭据和连接测试统一由服务中心管理；密钥不会显示在此页面。</p>
    </section>

    <section className="chat-settings-section">
      <div className="chat-settings-section-heading"><div><h3>聊天参数</h3><p>Temperature 作为聊天请求参数保存；思考强度在聊天顶部按当前对话选择。</p></div></div>
      <label className="field" htmlFor="chat-temperature"><span className="label">Temperature</span><input id="chat-temperature" className="input" type="number" min="0" max="2" step="0.1" value={temperature} onChange={event => setTemperature(event.target.value)} required /></label>
    </section>

    <section className="chat-settings-section chat-settings-data">
      <div className="chat-settings-section-heading"><div><h3>数据</h3><p>聊天记录只在当前浏览器保存。</p></div></div>
      <button className="btn btn-danger" type="button" onClick={onClearData}>清空本地聊天记录</button>
    </section>

    {feedback ? <div className="chat-settings-feedback" role="status">{feedback}</div> : null}
    <div className="chat-settings-footer">
      <button className="btn btn-primary" type="submit" disabled={busy || !settings}>{busy ? '保存中…' : '保存聊天参数'}</button>
    </div>
  </form>;
}
