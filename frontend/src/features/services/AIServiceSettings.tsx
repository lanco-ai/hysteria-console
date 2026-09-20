import { useEffect, useState } from 'react';

type AIModel = { id: string; name: string; context_window?: number; input_token_limit?: number };
type AIProfile = {
  id: string;
  name: string;
  protocol: 'gemini_native' | 'openai_compatible' | 'grok_media';
  base_url: string;
  api_key_configured: boolean;
  api_key_masked: string;
  temperature: number;
  provider: string;
  models: AIModel[];
  last_verified_at: string;
  verified_capabilities: string[];
};
type Catalog = { revision: string; profiles: AIProfile[]; bindings: Record<string, string> };
type Draft = { name: string; base_url: string; api_key: string; temperature: string; clear_api_key: boolean };
type Feature = 'chat' | 'plan_assistant' | 'video_assistant' | 'image_generation' | 'video_generation';

const endpoint = '/api/ai/services';
const featureLabels: { id: Feature; label: string; protocols: AIProfile['protocol'][] }[] = [
  { id: 'chat', label: 'AI 对话', protocols: ['openai_compatible', 'gemini_native'] },
  { id: 'plan_assistant', label: '今日计划建议', protocols: ['gemini_native'] },
  { id: 'video_assistant', label: '视频创意助手', protocols: ['gemini_native'] },
  { id: 'image_generation', label: '文生图', protocols: ['grok_media'] },
  { id: 'video_generation', label: '图生视频', protocols: ['grok_media'] },
];

function errorMessage(code: unknown): string {
  if (code === 'login_required' || code === 'admin_required') return '管理员登录已失效，请重新登录。';
  if (code === 'revision_conflict') return '设置已在其他页面更改，请刷新后重试。';
  if (code === 'service_not_configured') return '请先保存服务地址和 API Key。';
  if (code === 'authentication_failed') return 'API Key 无效或已过期。';
  if (code === 'permission_denied') return '该账号没有此服务的访问权限。';
  if (code === 'rate_limited') return '上游服务限流，请稍后重试。';
  if (code === 'timeout') return '连接超时，请稍后重试。';
  if (code === 'models_endpoint_unavailable') return '模型列表接口不可用，请检查 Base URL。';
  return '操作失败，请检查设置后重试。';
}

async function requestJson<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: 'same-origin', ...options });
  let body: unknown;
  try { body = await response.json(); } catch { body = null; }
  if (!response.ok) {
    const code = body && typeof body === 'object' && 'error' in body ? body.error : undefined;
    throw new Error(errorMessage(code));
  }
  return body as T;
}

function draftFrom(profile: AIProfile): Draft {
  return {
    name: profile.name,
    base_url: profile.base_url,
    api_key: '',
    temperature: String(profile.temperature),
    clear_api_key: false,
  };
}

function saveButtonBody(revision: string, draft: Draft, profile: AIProfile) {
  const body: Record<string, unknown> = { revision, name: draft.name.trim() };
  body.base_url = draft.base_url.trim();
  if (profile.protocol === 'openai_compatible') body.temperature = Number(draft.temperature);
  if (draft.clear_api_key) body.clear_api_key = true;
  else if (draft.api_key) body.api_key = draft.api_key;
  return body;
}

export function AIServiceSettings() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [bindingDrafts, setBindingDrafts] = useState<Partial<Record<Feature, string>>>({});
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [profileStatus, setProfileStatus] = useState<Record<string, string>>({});
  const [bindingStatus, setBindingStatus] = useState<Partial<Record<Feature, string>>>({});

  const loadCatalog = async () => {
    setError('');
    setBusy('load');
    try { setCatalog(await requestJson<Catalog>(endpoint)); }
    catch (value) { setError(value instanceof Error ? value.message : '无法读取服务配置。'); }
    finally { setBusy(''); }
  };

  useEffect(() => {
    const controller = new AbortController();
    void requestJson<Catalog>(endpoint, { signal: controller.signal }).then(setCatalog).catch(value => {
      if (!controller.signal.aborted) setError(value instanceof Error ? value.message : '无法读取服务配置。');
    });
    return () => controller.abort();
  }, []);

  const updateDraft = (profile: AIProfile, patch: Partial<Draft>) => {
    setDrafts(current => ({ ...current, [profile.id]: { ...(current[profile.id] || draftFrom(profile)), ...patch } }));
  };

  const saveProfile = async (profile: AIProfile) => {
    if (!catalog) return;
    const draft = drafts[profile.id] || draftFrom(profile);
    setBusy(`save:${profile.id}`); setError('');
    try {
      const next = await requestJson<Catalog>(`${endpoint}/${encodeURIComponent(profile.id)}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(saveButtonBody(catalog.revision, draft, profile)),
      });
      setCatalog(next);
      setDrafts(current => { const result = { ...current }; delete result[profile.id]; return result; });
      setProfileStatus(current => ({ ...current, [profile.id]: '设置已保存' }));
    } catch (value) {
      setProfileStatus(current => ({ ...current, [profile.id]: value instanceof Error ? value.message : '保存失败。' }));
    } finally { setBusy(''); }
  };

  const refreshModels = async (profile: AIProfile) => {
    if (!catalog) return;
    setBusy(`test:${profile.id}`); setError('');
    try {
      const tested = await requestJson<{ ok: boolean; models_count: number }>(`${endpoint}/${encodeURIComponent(profile.id)}/test`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      });
      if (!tested.ok) throw new Error('连接失败，请检查设置后重试。');
      const [models, next] = await Promise.all([
        requestJson<{ models: AIModel[]; last_verified_at: string }>(`${endpoint}/${encodeURIComponent(profile.id)}/models`),
        requestJson<Catalog>(endpoint),
      ]);
      setCatalog(next);
      setProfileStatus(current => ({ ...current, [profile.id]: `连接成功 · ${models.models.length} 个模型` }));
    } catch (value) {
      setProfileStatus(current => ({ ...current, [profile.id]: value instanceof Error ? value.message : '连接失败。' }));
    } finally { setBusy(''); }
  };

  const saveBinding = async (feature: Feature) => {
    if (!catalog) return;
    const profileId = bindingDrafts[feature] || catalog.bindings[feature];
    setBusy(`binding:${feature}`); setError('');
    try {
      const next = await requestJson<Catalog>('/api/ai/service-bindings', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ revision: catalog.revision, feature, profile_id: profileId }),
      });
      setCatalog(next);
      setBindingDrafts(current => ({ ...current, [feature]: next.bindings[feature] }));
      setBindingStatus(current => ({ ...current, [feature]: '已保存' }));
    } catch (value) {
      setBindingStatus(current => ({ ...current, [feature]: value instanceof Error ? value.message : '保存失败。' }));
    } finally { setBusy(''); }
  };

  return <section className="ai-service-settings" aria-label="API 服务接入">
    <header className="ai-service-heading"><div><span className="services-eyebrow">API 接入</span><h2>AI 服务</h2><p>密钥只保存在服务器；此处只返回配置状态和遮罩值。</p></div><button type="button" className="btn service-secondary" onClick={() => void loadCatalog()} disabled={busy !== ''}>刷新配置</button></header>
    {error ? <p className="ai-service-error" role="alert">{error}</p> : null}
    {!catalog && !error ? <p className="ai-service-loading" role="status">正在读取 AI 服务配置…</p> : null}
    {catalog ? <>
      <div className="ai-service-grid">
        {catalog.profiles.map(profile => {
          const draft = drafts[profile.id] || draftFrom(profile);
          const busyProfile = busy === `save:${profile.id}` || busy === `test:${profile.id}`;
          const hasUnsavedChanges = draft.name !== profile.name
            || draft.base_url !== profile.base_url
            || draft.temperature !== String(profile.temperature)
            || Boolean(draft.api_key)
            || draft.clear_api_key;
          return <article className="ai-service-card" data-ai-service={profile.id} key={profile.id}>
            <header><div><span>{profile.protocol === 'gemini_native' ? 'Gemini 原生 API' : profile.protocol === 'openai_compatible' ? 'OpenAI Compatible' : '媒体生成 API'}</span><h3>{profile.name}</h3></div><span className={`ai-service-secret-state${profile.api_key_configured ? ' is-ready' : ''}`}>{profile.api_key_configured ? '已配置' : '未配置'}</span></header>
            <label className="ai-service-field">服务名称<input value={draft.name} maxLength={80} disabled={busyProfile} onChange={event => updateDraft(profile, { name: event.target.value })} /></label>
            <label className="ai-service-field">API Base URL<input aria-label="API Base URL" type="url" value={draft.base_url} disabled={busyProfile} onChange={event => updateDraft(profile, { base_url: event.target.value })} placeholder={profile.protocol === 'gemini_native' ? 'http://127.0.0.1:8317/v1 或 https://example.com/v1' : 'https://example.com/v1'} /></label>
            <label className="ai-service-field">API Key<input aria-label="API Key" type="password" value={draft.api_key} disabled={busyProfile || draft.clear_api_key} autoComplete="new-password" onChange={event => updateDraft(profile, { api_key: event.target.value })} placeholder={profile.api_key_configured ? profile.api_key_masked : '仅保存到服务器'} /></label>
            {profile.api_key_configured ? <label className="ai-service-clear-key"><input type="checkbox" checked={draft.clear_api_key} disabled={busyProfile} onChange={event => updateDraft(profile, { clear_api_key: event.target.checked, api_key: '' })} /> 清除已保存的 Key</label> : null}
            {profile.protocol === 'openai_compatible' ? <label className="ai-service-field">Temperature<input type="number" min="0" max="2" step="0.1" value={draft.temperature} disabled={busyProfile} onChange={event => updateDraft(profile, { temperature: event.target.value })} /></label> : null}
            <div className="ai-service-actions"><button type="button" className="btn btn-primary" disabled={busy !== ''} onClick={() => void saveProfile(profile)}>{busy === `save:${profile.id}` ? '保存中…' : '保存设置'}</button><button type="button" className="btn service-secondary" disabled={busy !== '' || !profile.api_key_configured || hasUnsavedChanges} onClick={() => void refreshModels(profile)}>{busy === `test:${profile.id}` ? '连接中…' : '测试连接并刷新模型'}</button></div>
            {hasUnsavedChanges ? <p className="ai-service-status">有未保存修改；请先保存，再测试连接或刷新模型。</p> : null}
            {profileStatus[profile.id] ? <p className="ai-service-status" role="status">{profileStatus[profile.id]}</p> : null}
            <div className="ai-service-models"><strong>可用模型</strong>{profile.models.length ? <ul>{profile.models.map(model => <li key={model.id}><code>{model.id}</code>{model.input_token_limit ? <small>输入上限 {model.input_token_limit.toLocaleString()} tokens</small> : null}</li>)}</ul> : <p>尚未读取模型列表。</p>}{profile.last_verified_at ? <small>最近验证：{new Date(profile.last_verified_at).toLocaleString()}</small> : null}</div>
          </article>;
        })}
      </div>
      <section className="ai-service-bindings" aria-labelledby="ai-service-bindings-title"><header><div><span className="services-eyebrow">功能路由</span><h2 id="ai-service-bindings-title">服务绑定</h2><p>为各项功能选择服务；绑定保存不会自动触发上游生成。</p></div></header><div className="ai-service-binding-list">
        {featureLabels.map(feature => {
          const compatible = catalog.profiles.filter(profile => feature.protocols.includes(profile.protocol));
          return <div className="ai-service-binding" data-ai-binding={feature.id} key={feature.id}><label>{feature.label}<select aria-label={`${feature.label} 服务`} value={bindingDrafts[feature.id] || catalog.bindings[feature.id] || ''} disabled={busy !== ''} onChange={event => { setBindingDrafts(current => ({ ...current, [feature.id]: event.target.value })); setBindingStatus(current => ({ ...current, [feature.id]: '' })); }}>{compatible.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label><button type="button" className="btn service-secondary" disabled={busy !== '' || !compatible.length} onClick={() => void saveBinding(feature.id)}>保存绑定</button>{bindingStatus[feature.id] ? <span role="status">{bindingStatus[feature.id]}</span> : null}</div>;
        })}
      </div></section>
    </> : null}
    <p className="ai-service-security-note">API Key 不会显示在完整配置响应中，也不会保存到浏览器本地存储。</p>
  </section>;
}
