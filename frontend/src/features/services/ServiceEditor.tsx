import { useEffect, useRef, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';

export type Bookmark = { id: string; name: string; url: string; description: string; category: string; api_base: string; api_notes: string; model_ids: string[]; models_checked_at: string };
type Probe = { endpoint: string; status: string; models: string[]; http_status: number | null; elapsed_ms: number };
const messages: Record<string, string> = {
  verified: '已验证', authentication_failed: '密钥无效或已过期', permission_denied: '账号权限不足',
  not_found: '此地址未找到接口', rate_limited: '请求限流，请稍后重试', upstream_error: '上游服务返回错误',
  redirect_blocked: '接口发生跳转，请填写最终 API 地址', invalid_response: '返回内容不符合接口格式',
  response_too_large: '返回内容过大，已停止读取', empty_models: '接口可访问，但没有可用模型',
  invalid_target: '请使用公网 HTTPS API 地址；内网地址暂不支持检测', timeout: '请求超时',
  network_error: '连接失败，请检查地址、证书或网络',
};

export function ServiceEditor({ draft, existing, busy, error, onChange, onSubmit, onClose }: {
  draft: Bookmark; existing: boolean; busy: boolean; error: string;
  onChange: <K extends keyof Bookmark>(field: K, value: Bookmark[K]) => void;
  onSubmit: (event: FormEvent) => void; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const active = useRef<AbortController | null>(null);
  const [key, setKey] = useState('');
  const [models, setModels] = useState<Probe | null>(null);
  const [chat, setChat] = useState<Probe | null>(null);
  const [model, setModel] = useState('');
  const [probing, setProbing] = useState('');
  const [probeError, setProbeError] = useState('');
  const [applied, setApplied] = useState(false);
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.showModal();
    nameInput.current?.focus();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      active.current?.abort(); document.body.style.overflow = overflow;
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  const resetProbe = () => { setModels(null); setChat(null); setModel(''); setProbeError(''); setApplied(false); };
  const probe = async (kind: 'models' | 'chat') => {
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    setProbing(kind); setProbeError(''); setApplied(false);
    if (kind === 'models') { setModels(null); setChat(null); setModel(''); } else setChat(null);
    try {
      const response = await fetch('/api/v1/admin/services/probe', { method: 'POST', credentials: 'same-origin', signal: controller.signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_base: draft.api_base.trim(), api_key: key.trim(), kind, model: kind === 'chat' ? model : '' }) });
      if (!response.ok) throw new Error(response.status === 401 || response.status === 403 ? '请重新登录后测试。' : response.status === 429 ? '检测任务繁忙，请稍后重试。' : '请检查 API 地址与密钥格式。');
      const result = await response.json() as Probe;
      if (controller.signal.aborted) return;
      if (kind === 'models') {
        setModels(result); setModel(result.models[0] || '');
        if (result.status === 'verified' || result.status === 'empty_models') {
          onChange('model_ids', result.models);
          onChange('models_checked_at', new Date().toISOString());
        }
      } else setChat(result);
    } catch (value) { if (!controller.signal.aborted) setProbeError(value instanceof Error ? value.message : '检测失败'); }
    finally { if (!controller.signal.aborted) setProbing(''); }
  };
  const applyResults = () => {
    const prefix = new URL(draft.api_base).pathname.replace(/\/$/, '');
    const lines = [];
    if (models?.status === 'verified') lines.push(`GET ${prefix}/models — 已验证，返回 ${models.models.length} 个模型`);
    if (chat?.status === 'verified') lines.push(`POST ${prefix}/chat/completions — 已验证，模型 ${model}`);
    lines.push(`检测时间：${new Date().toLocaleString()}；其他接口未测试。`);
    onChange('api_notes', lines.join('\n')); setApplied(true);
  };
  const resultLine = (value: Probe, label: string) => <div className={`services-probe-result ${value.status === 'verified' ? 'is-ok' : ''}`}>
    <strong>{label} · {messages[value.status] || '检测失败'}</strong>
    <span>{value.http_status ? `HTTP ${value.http_status} · ` : ''}{value.elapsed_ms}ms{value.status === 'verified' && label === '模型列表' ? ` · ${value.models.length} 个模型` : ''}</span>
  </div>;
  const locked = busy || !!probing;
  return createPortal(<dialog ref={dialog} className="services-page services-editor-dialog" aria-labelledby="service-editor-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}
    onClick={event => { if (event.target === event.currentTarget && !busy) { const rect = event.currentTarget.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose(); } }}>
    <form className="services-editor" onSubmit={onSubmit}>
      <div className="services-editor-heading"><div><h3 id="service-editor-title">{existing ? '编辑网站' : '添加网站'}</h3><span>网址、用途与 API 连接</span></div><button className="btn service-secondary" type="button" aria-label="关闭编辑" disabled={busy} onClick={onClose}>×</button></div>
      {error ? <div className="err" role="alert">{error}</div> : null}
      <fieldset disabled={locked}>
        <label>服务名称<input ref={nameInput} required maxLength={80} value={draft.name} onChange={event => onChange('name', event.target.value)} placeholder="例如：家庭 NAS"/></label>
        <label>分组<input maxLength={40} required value={draft.category} onChange={event => onChange('category', event.target.value)} list="service-groups"/><datalist id="service-groups"><option value="AI 接口"/><option value="服务器管理"/><option value="常用网站"/><option value="内网服务"/></datalist></label>
        <label className="services-wide">网站地址<input type="url" required maxLength={2048} value={draft.url} onChange={event => onChange('url', event.target.value)}/></label>
        <label className="services-wide">服务用途<input maxLength={500} value={draft.description} onChange={event => onChange('description', event.target.value)} placeholder="记下它是做什么的"/></label>
        <label className="services-wide">API Base URL（可选）<input type="url" maxLength={2048} value={draft.api_base} onChange={event => { onChange('api_base', event.target.value); onChange('model_ids', []); onChange('models_checked_at', ''); resetProbe(); }} placeholder="https://example.com/v1"/></label>
      </fieldset>
      <section className="services-probe" aria-label="API 检测">
        <div className="services-probe-heading"><strong>API 检测</strong><span>填写地址和 SK，自动读取模型</span></div>
        <label>API Key / SK<input type="password" autoComplete="off" spellCheck={false} maxLength={4096} disabled={locked} value={key} onChange={event => { setKey(event.target.value); resetProbe(); }} placeholder="仅本次使用，关闭后清空"/></label>
        <div className="services-probe-actions"><button className="btn service-secondary" type="button" disabled={locked || !draft.api_base.trim() || !key.trim()} onClick={() => void probe('models')}>{probing === 'models' ? '正在检测…' : '检测模型列表'}</button><small>密钥仅发送给填写的 API 服务，不写入收藏。</small></div>
        {probeError ? <p className="err" role="alert">{probeError}</p> : null}
        {models ? resultLine(models, '模型列表') : null}
        {models?.status === 'verified' || models?.status === 'empty_models' ? <small>模型列表已更新，点击“保存网站”后显示到卡片。</small> : null}
        {models?.status === 'verified' ? <><div className="services-probe-model"><label>测试模型<select value={model} disabled={locked} onChange={event => { setModel(event.target.value); setChat(null); setApplied(false); }}>{models.models.map(id => <option key={id} value={id}>{id}</option>)}</select></label><button className="btn service-secondary" type="button" disabled={locked || !model} onClick={() => void probe('chat')}>{probing === 'chat' ? '正在测试…' : '测试对话'}</button></div><small>发送一条极短对话，会消耗少量额度。图片、视频与流式输出暂不测试。</small></> : null}
        {chat ? resultLine(chat, '对话接口') : null}
        {models?.status === 'verified' ? <button className="btn service-secondary" type="button" disabled={locked || applied} onClick={applyResults}>{applied ? '已填入，下方保存后生效' : '将已验证结果写入说明'}</button> : null}
      </section>
      <details className="services-manual-api"><summary>接口备注（可选，不在模型卡片展示）</summary><label>提供哪些 API<textarea rows={5} maxLength={2000} disabled={locked} value={draft.api_notes} onChange={event => onChange('api_notes', event.target.value)}/></label></details>
      <div className="services-editor-actions"><small>检测结果只代表本次请求。</small><button className="btn btn-ghost" type="button" disabled={busy} onClick={onClose}>取消</button><button className="btn btn-primary" disabled={locked}>{busy ? '保存中…' : '保存网站'}</button></div>
    </form>
  </dialog>, document.body);
}
