import { useEffect, useRef, type FormEvent } from 'react';
import { createPortal } from 'react-dom';

export type AIServiceDraft = { name: string; base_url: string; api_key: string; temperature: string; clear_api_key: boolean };

export function AIServiceEditor({ name, protocolLabel, draft, keyConfigured, keyMasked, withTemperature, busy, error, onChange, onSubmit, onClose }: {
  name: string; protocolLabel: string; draft: AIServiceDraft; keyConfigured: boolean; keyMasked: string;
  withTemperature: boolean; busy: boolean; error: string;
  onChange: (patch: Partial<AIServiceDraft>) => void;
  onSubmit: (event: FormEvent) => void; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.showModal();
    nameInput.current?.focus();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = overflow;
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return createPortal(<dialog ref={dialog} className="services-page services-editor-dialog ai-service-editor" aria-labelledby="ai-service-editor-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}
    onClick={event => { if (event.target === event.currentTarget && !busy) { const rect = event.currentTarget.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose(); } }}>
    <form className="services-editor" onSubmit={onSubmit}>
      <div className="services-editor-heading"><div><h3 id="ai-service-editor-title">编辑 {name}</h3><span>{protocolLabel} · 密钥只保存在服务器</span></div><button className="btn service-secondary" type="button" aria-label="关闭编辑" disabled={busy} onClick={onClose}>×</button></div>
      {error ? <div className="err" role="alert">{error}</div> : null}
      <fieldset disabled={busy}>
        <label className={withTemperature ? undefined : 'services-wide'}>接口名称<input ref={nameInput} required maxLength={80} value={draft.name} onChange={event => onChange({ name: event.target.value })}/></label>
        {withTemperature ? <label>Temperature<input type="number" min="0" max="2" step="0.1" value={draft.temperature} onChange={event => onChange({ temperature: event.target.value })}/></label> : null}
        <label className="services-wide">API Base URL<input type="url" maxLength={2048} value={draft.base_url} onChange={event => onChange({ base_url: event.target.value })} placeholder="https://example.com/v1"/></label>
        <label className="services-wide">API Key<input type="password" autoComplete="new-password" maxLength={2048} value={draft.api_key} disabled={draft.clear_api_key} onChange={event => onChange({ api_key: event.target.value })} placeholder={keyConfigured ? `${keyMasked} · 留空则保持不变` : '仅保存到服务器'}/></label>
        {keyConfigured ? <label className="services-wide ai-service-clear-key"><input type="checkbox" checked={draft.clear_api_key} onChange={event => onChange({ clear_api_key: event.target.checked, api_key: '' })}/> 清除已保存的 Key</label> : null}
      </fieldset>
      <div className="services-editor-actions"><small>更换地址或 Key 后，请重新测试连接以读取模型列表。</small><button className="btn btn-ghost" type="button" disabled={busy} onClick={onClose}>取消</button><button className="btn btn-primary" disabled={busy}>{busy ? '保存中…' : '保存设置'}</button></div>
    </form>
  </dialog>, document.body);
}
