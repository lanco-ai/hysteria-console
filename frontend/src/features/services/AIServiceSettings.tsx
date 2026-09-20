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
type AssistantFeature = 'plan_assistant' | 'video_assistant';
type Catalog = { revision: string; profiles: AIProfile[]; bindings: Record<string, string>; model_bindings?: Partial<Record<AssistantFeature, string>> };
type Draft = { name: string; base_url: string; api_key: string; temperature: string; clear_api_key: boolean };
type Feature = 'chat' | 'plan_assistant' | 'video_assistant' | 'image_generation' | 'video_generation';
type AssistantTest = { level: 'B' | 'C'; service_id: string; model_id: string; revision: string; tested_at: string; structured_output?: string };
type AssistantTests = Partial<Record<'B' | 'C', AssistantTest>>;

const endpoint = '/api/ai/services';
const featureLabels: { id: Feature; label: string; protocols: AIProfile['protocol'][] }[] = [
  { id: 'chat', label: 'AI 对话', protocols: ['openai_compatible', 'gemini_native'] },
  { id: 'plan_assistant', label: '今日计划建议', protocols: ['openai_compatible', 'gemini_native'] },
  { id: 'video_assistant', label: '视频创意助手', protocols: ['openai_compatible', 'gemini_native'] },
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
  if (code === 'model_not_selected') return '请先为此助手选择模型。';
  if (code === 'model_not_available') return '所选模型已不可用，请刷新模型列表后重新选择。';
  if (code === 'invalid_model_response') return '所选模型返回了空内容、拒答、截断或无效 JSON。';
  if (code === 'structured_result_invalid') return '模型回复未通过此助手的结构和业务校验。';
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
  const [modelDrafts, setModelDrafts] = useState<Partial<Record<AssistantFeature, string>>>({});
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [profileStatus, setProfileStatus] = useState<Record<string, string>>({});
  const [bindingStatus, setBindingStatus] = useState<Partial<Record<Feature, string>>>({});
  const [assistantTests, setAssistantTests] = useState<Partial<Record<AssistantFeature, AssistantTests>>>({});
  const [profileTests, setProfileTests] = useState<Record<string, { revision: string; tested_at: string; models_count: number }>>({});

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
    setProfileTests(current => { const next = { ...current }; delete next[profile.id]; return next; });
    setProfileStatus(current => ({ ...current, [profile.id]: '' }));
    setAssistantTests(current => {
      const next = { ...current };
      for (const feature of ['plan_assistant', 'video_assistant'] as const) {
        if (Object.values(next[feature] || {}).some(result => result?.service_id === profile.id)) delete next[feature];
      }
      return next;
    });
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
    setProfileTests(current => { const next = { ...current }; delete next[profile.id]; return next; });
    setProfileStatus(current => ({ ...current, [profile.id]: '' }));
    setBusy(`test:${profile.id}`); setError('');
    try {
      const tested = await requestJson<{ ok: boolean; level: 'A'; models_count: number; revision: string; tested_at: string }>(`${endpoint}/${encodeURIComponent(profile.id)}/test`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      });
      if (!tested.ok) throw new Error('连接失败，请检查设置后重试。');
      const next = await requestJson<Catalog>(endpoint);
      setCatalog(next);
      setProfileTests(current => ({ ...current, [profile.id]: { revision: tested.revision, tested_at: tested.tested_at, models_count: tested.models_count } }));
      setProfileStatus(current => ({ ...current, [profile.id]: '' }));
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
        body: JSON.stringify({ revision: catalog.revision, feature, profile_id: profileId, ...(feature === 'plan_assistant' || feature === 'video_assistant' ? { model_id: modelDrafts[feature] ?? catalog.model_bindings?.[feature] ?? '' } : {}) }),
      });
      setCatalog(next);
      setBindingDrafts(current => ({ ...current, [feature]: next.bindings[feature] }));
      setBindingStatus(current => ({ ...current, [feature]: '已保存' }));
    } catch (value) {
      setBindingStatus(current => ({ ...current, [feature]: value instanceof Error ? value.message : '保存失败。' }));
    } finally { setBusy(''); }
  };

  const testAssistant = async (feature: AssistantFeature, level: 'B' | 'C', serviceId: string, modelId: string) => {
    if (!catalog || !modelId) {
      setBindingStatus(current => ({ ...current, [feature]: '请先选择一个模型。' }));
      return;
    }
    const operation = level === 'B' ? 'generation' : 'structured';
    setBusy(`assistant:${feature}:${level}`);
    setBindingStatus(current => ({ ...current, [feature]: '' }));
    try {
      const result = await requestJson<Omit<AssistantTest, 'level'> & { level: 'B' | 'C' }>(`${endpoint}/${encodeURIComponent(serviceId)}/test/${operation}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ feature, model_id: modelId }),
      });
      setAssistantTests(current => ({ ...current, [feature]: { ...current[feature], [level]: result } }));
    } catch (value) {
      setAssistantTests(current => {
        const next = { ...current };
        const featureTests = { ...next[feature] };
        delete featureTests[level];
        next[feature] = featureTests;
        return next;
      });
      setBindingStatus(current => ({ ...current, [feature]: value instanceof Error ? value.message : '能力测试失败。' }));
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
          const busyProfile = busy === `save:${profile.id}` || busy === `test:${profile.id}` || busy.startsWith('assistant:');
          const hasUnsavedChanges = draft.name !== profile.name
            || draft.base_url !== profile.base_url
            || draft.temperature !== String(profile.temperature)
            || Boolean(draft.api_key)
            || draft.clear_api_key;
          const verifiedTest = profileTests[profile.id];
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
            {verifiedTest && verifiedTest.revision === catalog.revision && !hasUnsavedChanges ? <p className="ai-service-status" role="status">A 模型列表读取通过 · {verifiedTest.models_count} 个 · 配置版本 {verifiedTest.revision} · {new Date(verifiedTest.tested_at).toLocaleString()}</p> : null}
            <div className="ai-service-models"><strong>可用模型</strong>{profile.models.length ? <ul>{profile.models.map(model => <li key={model.id}><code>{model.id}</code>{model.input_token_limit ? <small>输入上限 {model.input_token_limit.toLocaleString()} tokens</small> : null}</li>)}</ul> : <p>尚未读取模型列表。</p>}{profile.last_verified_at ? <small>最近验证：{new Date(profile.last_verified_at).toLocaleString()}</small> : null}</div>
          </article>;
        })}
      </div>
      <section className="ai-service-bindings" aria-labelledby="ai-service-bindings-title"><header><div><span className="services-eyebrow">功能路由</span><h2 id="ai-service-bindings-title">服务绑定</h2><p>为各项功能选择服务。助手测试分为 A 模型列表、B 所选模型文本生成、C 助手结构校验；B/C 会向上游发送真实生成请求，但不会保存计划或视频项目。</p></div></header><div className="ai-service-binding-list">
        {featureLabels.map(feature => {
          const compatible = catalog.profiles.filter(profile => feature.protocols.includes(profile.protocol));
          const profileId = bindingDrafts[feature.id] ?? catalog.bindings[feature.id] ?? '';
          const selectedProfile = compatible.find(profile => profile.id === profileId);
          const assistant = feature.id === 'plan_assistant' || feature.id === 'video_assistant';
          const assistantFeature = assistant ? feature.id as AssistantFeature : null;
          const modelId = assistantFeature ? modelDrafts[assistantFeature] ?? catalog.model_bindings?.[assistantFeature] ?? '' : '';
          const activeProfileTest = selectedProfile ? profileTests[selectedProfile.id] : undefined;
          const profileDraft = selectedProfile ? drafts[selectedProfile.id] || draftFrom(selectedProfile) : null;
          const profileDirty = Boolean(selectedProfile && profileDraft && (
            profileDraft.name !== selectedProfile.name
            || profileDraft.base_url !== selectedProfile.base_url
            || profileDraft.temperature !== String(selectedProfile.temperature)
            || Boolean(profileDraft.api_key)
            || profileDraft.clear_api_key
          ));
          const assistantTestMatches = (level: 'B' | 'C') => {
            const result = assistantFeature ? assistantTests[assistantFeature]?.[level] : undefined;
            return Boolean(result && selectedProfile
              && result.service_id === selectedProfile.id
              && result.model_id === modelId
              && result.revision === catalog.revision
              && !profileDirty);
          };
          const assistantTestB = assistantFeature ? assistantTests[assistantFeature]?.B : undefined;
          const assistantTestC = assistantFeature ? assistantTests[assistantFeature]?.C : undefined;
          return <div className="ai-service-binding" data-ai-binding={feature.id} key={feature.id}>
            <label>{feature.label} 服务<select aria-label={`${feature.label} 服务`} value={profileId} disabled={busy !== ''} onChange={event => { setBindingDrafts(current => ({ ...current, [feature.id]: event.target.value })); if (assistant) setModelDrafts(current => ({ ...current, [feature.id as AssistantFeature]: '' })); setBindingStatus(current => ({ ...current, [feature.id]: '' })); }}>{compatible.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label>
            {assistant && assistantFeature ? <>
              <label>{feature.label} 模型<select aria-label={`${feature.label} 模型`} value={modelId} disabled={busy !== '' || !selectedProfile} onChange={event => { setModelDrafts(current => ({ ...current, [assistantFeature]: event.target.value })); setBindingStatus(current => ({ ...current, [assistantFeature]: '' })); }}>
                <option value="">请选择模型</option>
                {selectedProfile?.models.map(model => <option key={model.id} value={model.id}>{model.name} · {model.id}</option>)}
                {modelId && selectedProfile && !selectedProfile.models.some(model => model.id === modelId) ? <option value={modelId} disabled>{modelId}（已不可用）</option> : null}
              </select></label>
            </> : null}
            <button type="button" className="btn service-secondary" disabled={busy !== '' || !compatible.length} onClick={() => void saveBinding(feature.id)}>保存绑定</button>
            {assistant && assistantFeature && selectedProfile ? <div className="ai-service-assistant-tests">
              <button type="button" className="btn service-secondary" disabled={busy !== '' || profileDirty || !modelId} onClick={() => void testAssistant(assistantFeature, 'B', selectedProfile.id, modelId)}>{busy === `assistant:${assistantFeature}:B` ? '生成测试中…' : 'B 测试所选模型生成'}</button>
              <button type="button" className="btn service-secondary" disabled={busy !== '' || profileDirty || !modelId} onClick={() => void testAssistant(assistantFeature, 'C', selectedProfile.id, modelId)}>{busy === `assistant:${assistantFeature}:C` ? '结构校验中…' : 'C 测试助手结构'}</button>
              <small>A：保存服务后测试连接并刷新模型列表。请选择模型后再运行 B/C。</small>
              {assistantTestMatches('B') && assistantTestB ? <span role="status">B 文本生成通过 · {assistantTestB.service_id} / {assistantTestB.model_id} · 配置版本 {assistantTestB.revision} · {new Date(assistantTestB.tested_at).toLocaleString()}</span> : null}
              {assistantTestMatches('C') && assistantTestC ? <span role="status">C 结构化校验通过 · {assistantTestC.service_id} / {assistantTestC.model_id} · 配置版本 {assistantTestC.revision} · {new Date(assistantTestC.tested_at).toLocaleString()}{assistantTestC.structured_output === 'json_text_fallback' ? ' · JSON 文本降级后通过校验' : assistantTestC.structured_output === 'json_schema' ? ' · JSON Schema 支持' : ' · Gemini 原生结构化输出'}</span> : null}
            </div> : null}
            {activeProfileTest && selectedProfile && activeProfileTest.revision === catalog.revision && !profileDirty ? <span role="status">A 模型列表读取通过 · {activeProfileTest.models_count} 个 · 配置版本 {activeProfileTest.revision} · {new Date(activeProfileTest.tested_at).toLocaleString()}</span> : null}
            {bindingStatus[feature.id] ? <span role="status">{bindingStatus[feature.id]}</span> : null}
          </div>;
        })}
      </div></section>
    </> : null}
    <p className="ai-service-security-note">API Key 不会显示在完整配置响应中，也不会保存到浏览器本地存储。</p>
  </section>;
}
