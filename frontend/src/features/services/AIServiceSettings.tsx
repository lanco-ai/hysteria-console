import { useEffect, useRef, useState } from 'react';

type AIModel = { id: string; name: string; context_window?: number; input_token_limit?: number };
type AIProfile = {
  id: string;
  name: string;
  protocol: 'openai_compatible' | 'grok_media';
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
type Feature = 'chat' | AssistantFeature | 'image_generation' | 'video_generation';
type Catalog = { revision: string; profiles: AIProfile[]; bindings: Partial<Record<Feature, string>>; model_bindings?: Partial<Record<AssistantFeature, string>> };
type Draft = { name: string; base_url: string; api_key: string; temperature: string; clear_api_key: boolean };
type AssistantTest = { level: 'B' | 'C'; service_id: string; model_id: string; revision: string; tested_at: string; structured_output?: string };
type AssistantTests = Partial<Record<'B' | 'C', AssistantTest>>;

const endpoint = '/api/ai/services';
const featureLabels: Record<Feature, string> = {
  chat: 'AI 对话',
  plan_assistant: '今日计划建议',
  video_assistant: '视频创意助手',
  image_generation: '文生图',
  video_generation: '图生视频',
};
const features = Object.keys(featureLabels) as Feature[];
const assistantFeatures: AssistantFeature[] = ['plan_assistant', 'video_assistant'];
const protocolLabels: Record<AIProfile['protocol'], string> = { openai_compatible: 'OpenAI 兼容接口', grok_media: '媒体生成接口' };

function errorMessage(code: unknown): string {
  if (code === 'login_required' || code === 'admin_required') return '管理员登录已失效，请重新登录。';
  if (code === 'revision_conflict') return '设置已在其他页面更改，请刷新后重试。';
  if (code === 'unknown_service') return '该服务已移除，请刷新配置。';
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

const isDirty = (draft: Draft, profile: AIProfile) => draft.name !== profile.name
  || draft.base_url !== profile.base_url
  || draft.temperature !== String(profile.temperature)
  || Boolean(draft.api_key)
  || draft.clear_api_key;

const modelLabel = (model: AIModel) => model.name && model.name !== model.id ? `${model.name} · ${model.id}` : model.id;
const timeLabel = (value: string) => new Date(value).toLocaleString();

function saveButtonBody(revision: string, draft: Draft, profile: AIProfile) {
  const body: Record<string, unknown> = { revision, name: draft.name.trim() };
  body.base_url = draft.base_url.trim();
  if (profile.protocol === 'openai_compatible') body.temperature = Number(draft.temperature);
  if (draft.clear_api_key) body.clear_api_key = true;
  else if (draft.api_key) body.api_key = draft.api_key;
  return body;
}

export function AIServiceSettings({ onSettled }: { onSettled?: () => void }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [modelDrafts, setModelDrafts] = useState<Partial<Record<AssistantFeature, string>>>({});
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [profileStatus, setProfileStatus] = useState<Record<string, string>>({});
  const [bindingStatus, setBindingStatus] = useState<Partial<Record<AssistantFeature, string>>>({});
  const [assistantTests, setAssistantTests] = useState<Partial<Record<AssistantFeature, AssistantTests>>>({});
  const [profileTests, setProfileTests] = useState<Record<string, { revision: string; tested_at: string; models_count: number }>>({});
  const [modelProfileId, setModelProfileId] = useState('');
  const [modelSearch, setModelSearch] = useState('');
  const [catalogOpen, setCatalogOpen] = useState(false);
  const catalogRef = useRef<HTMLDetailsElement>(null);
  const settled = useRef(onSettled);

  const modelProfile = catalog?.profiles.find(profile => profile.id === modelProfileId) ?? catalog?.profiles[0];
  const visibleModels = modelProfile?.models.filter(model => `${model.name} ${model.id}`.toLocaleLowerCase().includes(modelSearch.trim().toLocaleLowerCase())) ?? [];

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
    }).finally(() => {
      if (!controller.signal.aborted) settled.current?.();
    });
    return () => controller.abort();
  }, []);

  const updateDraft = (profile: AIProfile, patch: Partial<Draft>) => {
    setDrafts(current => ({ ...current, [profile.id]: { ...(current[profile.id] || draftFrom(profile)), ...patch } }));
    setProfileTests(current => { const next = { ...current }; delete next[profile.id]; return next; });
    setProfileStatus(current => ({ ...current, [profile.id]: '' }));
    setAssistantTests(current => {
      const next = { ...current };
      for (const feature of assistantFeatures) {
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

  const showModels = (profile: AIProfile) => {
    setModelProfileId(profile.id);
    setModelSearch('');
    setCatalogOpen(true);
    window.requestAnimationFrame(() => catalogRef.current?.scrollIntoView({ block: 'nearest' }));
  };

  const saveModel = async (feature: AssistantFeature) => {
    const profileId = catalog?.bindings[feature];
    if (!catalog || !profileId) return;
    setBusy(`binding:${feature}`); setError('');
    try {
      const next = await requestJson<Catalog>('/api/ai/service-bindings', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ revision: catalog.revision, feature, profile_id: profileId, model_id: modelDrafts[feature] ?? catalog.model_bindings?.[feature] ?? '' }),
      });
      setCatalog(next);
      setModelDrafts(current => { const result = { ...current }; delete result[feature]; return result; });
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

  const selectedModels = catalog ? assistantFeatures.filter(feature => catalog.model_bindings?.[feature]).length : 0;

  return <section className="services-section ai-service-settings" id="ai-services" aria-labelledby="ai-services-title">
    <header className="services-section-heading">
      <div><h2 id="ai-services-title">API 接入</h2><p>AI 对话、助手和图像 / 视频生成共用这些接口。密钥只保存在服务器，页面只显示配置状态和遮罩值。</p></div>
      <button type="button" className="btn service-secondary" onClick={() => void loadCatalog()} disabled={busy !== ''}>刷新配置</button>
    </header>
    {error ? <p className="ai-service-error" role="alert">{error}</p> : null}
    {!catalog && !error ? <p className="ai-service-loading" role="status">正在读取 AI 服务配置…</p> : null}
    {catalog ? <>
      <div className="ai-service-grid">
        {catalog.profiles.map(profile => {
          const draft = drafts[profile.id] || draftFrom(profile);
          const busyProfile = busy === `save:${profile.id}` || busy === `test:${profile.id}` || busy.startsWith('assistant:');
          const hasUnsavedChanges = isDirty(draft, profile);
          const verifiedTest = profileTests[profile.id];
          const uses = features.filter(feature => catalog.bindings[feature] === profile.id);
          return <article className="ai-service-card" data-ai-service={profile.id} key={profile.id}>
            <header><div><span>{protocolLabels[profile.protocol]}</span><h3>{profile.name}</h3></div><span className={`ai-service-secret-state${profile.api_key_configured ? ' is-ready' : ''}`}>{profile.api_key_configured ? '已配置' : '未配置'}</span></header>
            <div className="ai-service-card-summary"><strong>{profile.models.length} 个模型</strong><span>{profile.last_verified_at ? `最近验证 ${timeLabel(profile.last_verified_at)}` : '尚未验证连接'}</span></div>
            {uses.length ? <p className="ai-service-uses"><span>用于</span>{uses.map(feature => <span className="ai-service-use" key={feature}>{featureLabels[feature]}</span>)}</p> : null}
            <div className="ai-service-card-actions">
              <button type="button" className="btn service-secondary" disabled={busy !== '' || !profile.api_key_configured || hasUnsavedChanges} onClick={() => void refreshModels(profile)}>{busy === `test:${profile.id}` ? '连接中…' : '测试连接'}</button>
              <button type="button" className="btn btn-ghost ai-service-show-models" disabled={!profile.models.length} onClick={() => showModels(profile)}>查看模型</button>
            </div>
            <details className="ai-service-config"><summary>编辑配置{hasUnsavedChanges ? <span className="ai-service-unsaved">未保存</span> : null}</summary><div className="ai-service-config-fields">
              <label className="ai-service-field">接口名称<input value={draft.name} maxLength={80} disabled={busyProfile} onChange={event => updateDraft(profile, { name: event.target.value })} /></label>
              <label className="ai-service-field">API Base URL<input aria-label="API Base URL" type="url" value={draft.base_url} disabled={busyProfile} onChange={event => updateDraft(profile, { base_url: event.target.value })} placeholder="https://example.com/v1" /></label>
              <label className="ai-service-field">API Key<input aria-label="API Key" type="password" value={draft.api_key} disabled={busyProfile || draft.clear_api_key} autoComplete="new-password" onChange={event => updateDraft(profile, { api_key: event.target.value })} placeholder={profile.api_key_configured ? profile.api_key_masked : '仅保存到服务器'} /></label>
              {profile.api_key_configured ? <label className="ai-service-clear-key"><input type="checkbox" checked={draft.clear_api_key} disabled={busyProfile} onChange={event => updateDraft(profile, { clear_api_key: event.target.checked, api_key: '' })} /> 清除已保存的 Key</label> : null}
              {profile.protocol === 'openai_compatible' ? <label className="ai-service-field">Temperature<input type="number" min="0" max="2" step="0.1" value={draft.temperature} disabled={busyProfile} onChange={event => updateDraft(profile, { temperature: event.target.value })} /></label> : null}
              <div className="ai-service-actions"><button type="button" className="btn btn-primary" disabled={busy !== ''} onClick={() => void saveProfile(profile)}>{busy === `save:${profile.id}` ? '保存中…' : '保存设置'}</button></div>
            </div></details>
            {hasUnsavedChanges ? <p className="ai-service-status">有未保存修改；请先保存，再测试连接或刷新模型。</p> : null}
            {profileStatus[profile.id] ? <p className="ai-service-status" role="status">{profileStatus[profile.id]}</p> : null}
            {verifiedTest && verifiedTest.revision === catalog.revision && !hasUnsavedChanges ? <p className="ai-service-status" role="status">A 模型列表读取通过 · {verifiedTest.models_count} 个 · 配置版本 {verifiedTest.revision} · {timeLabel(verifiedTest.tested_at)}</p> : null}
          </article>;
        })}
        <section className="ai-service-card ai-service-assistants" id="assistant-models" aria-labelledby="assistant-models-title">
          <header><div><span>功能绑定</span><h3 id="assistant-models-title">助手模型</h3></div><span className={`ai-service-secret-state${selectedModels === assistantFeatures.length ? ' is-ready' : ''}`}>已选 {selectedModels}/{assistantFeatures.length}</span></header>
          <p className="ai-service-card-note">两个助手都通过绑定的接口调用所选模型；模型列表来自该接口最近一次测试连接。</p>
          {assistantFeatures.map(feature => {
            const profile = catalog.profiles.find(item => item.id === catalog.bindings[feature]);
            const savedModel = catalog.model_bindings?.[feature] ?? '';
            const modelId = modelDrafts[feature] ?? savedModel;
            const modelChanged = modelId !== savedModel;
            const profileDirty = Boolean(profile && isDirty(drafts[profile.id] || draftFrom(profile), profile));
            const activeProfileTest = profile ? profileTests[profile.id] : undefined;
            const assistantTestMatches = (level: 'B' | 'C') => {
              const result = assistantTests[feature]?.[level];
              return Boolean(result && profile
                && result.service_id === profile.id
                && result.model_id === modelId
                && result.revision === catalog.revision
                && !profileDirty);
            };
            const assistantTestB = assistantTests[feature]?.B;
            const assistantTestC = assistantTests[feature]?.C;
            return <div className="ai-service-binding" data-ai-binding={feature} key={feature}>
              <div className="ai-service-binding-heading"><strong>{featureLabels[feature]}</strong><span className="ai-service-binding-via">{profile ? `经 ${profile.name}` : '未绑定接口'}</span>{modelChanged ? <span className="ai-service-binding-hint">未保存</span> : !savedModel ? <span className="ai-service-binding-hint">未选择模型</span> : null}{bindingStatus[feature] ? <span role="status">{bindingStatus[feature]}</span> : null}</div>
              <div className="ai-service-binding-controls">
                <label>模型<select aria-label={`${featureLabels[feature]} 模型`} value={modelId} disabled={busy !== '' || !profile} onChange={event => { setModelDrafts(current => ({ ...current, [feature]: event.target.value })); setBindingStatus(current => ({ ...current, [feature]: '' })); }}>
                  <option value="">{profile?.models.length ? '请选择模型' : '请先测试接口连接'}</option>
                  {profile?.models.map(model => <option key={model.id} value={model.id}>{modelLabel(model)}</option>)}
                  {modelId && profile && !profile.models.some(model => model.id === modelId) ? <option value={modelId} disabled>{modelId}（已不可用）</option> : null}
                </select></label>
                <button type="button" className="btn service-secondary" disabled={busy !== '' || !profile || !modelChanged} onClick={() => void saveModel(feature)}>{busy === `binding:${feature}` ? '保存中…' : '保存模型'}</button>
              </div>
              {profile ? <details className="ai-service-assistant-tests"><summary>能力测试</summary><div className="ai-service-assistant-test-content"><p>B/C 会向上游发送真实生成请求，但不会保存计划或视频项目。</p>
                <button type="button" className="btn service-secondary" disabled={busy !== '' || profileDirty || !modelId} onClick={() => void testAssistant(feature, 'B', profile.id, modelId)}>{busy === `assistant:${feature}:B` ? '生成测试中…' : 'B 测试所选模型生成'}</button>
                <button type="button" className="btn service-secondary" disabled={busy !== '' || profileDirty || !modelId} onClick={() => void testAssistant(feature, 'C', profile.id, modelId)}>{busy === `assistant:${feature}:C` ? '结构校验中…' : 'C 测试助手结构'}</button>
                <small>A：保存接口后测试连接并刷新模型列表。请选择模型后再运行 B/C。</small>
                {activeProfileTest && activeProfileTest.revision === catalog.revision && !profileDirty ? <span role="status">A 模型列表读取通过 · {activeProfileTest.models_count} 个 · 配置版本 {activeProfileTest.revision} · {timeLabel(activeProfileTest.tested_at)}</span> : null}
                {assistantTestMatches('B') && assistantTestB ? <span role="status">B 文本生成通过 · {assistantTestB.service_id} / {assistantTestB.model_id} · 配置版本 {assistantTestB.revision} · {timeLabel(assistantTestB.tested_at)}</span> : null}
                {assistantTestMatches('C') && assistantTestC ? <span role="status">C 结构化校验通过 · {assistantTestC.service_id} / {assistantTestC.model_id} · 配置版本 {assistantTestC.revision} · {timeLabel(assistantTestC.tested_at)}{assistantTestC.structured_output === 'json_text_fallback' ? ' · JSON 文本降级后通过校验' : assistantTestC.structured_output === 'json_schema' ? ' · JSON Schema 支持' : ''}</span> : null}
              </div></details> : null}
            </div>;
          })}
        </section>
      </div>
      <details className="ai-service-catalog" ref={catalogRef} open={catalogOpen} onToggle={event => setCatalogOpen(event.currentTarget.open)}>
        <summary><span className="ai-service-catalog-title"><strong>模型目录</strong><small>搜索各接口已验证的模型</small></span><span className="ai-service-catalog-counts">{catalog.profiles.map(profile => <span key={profile.id}>{profile.name} · {profile.models.length}</span>)}</span></summary>
        <div className="ai-service-catalog-body">
          <div className="ai-service-catalog-controls"><label>接口<select aria-label="模型目录服务" value={modelProfile?.id ?? ''} onChange={event => { setModelProfileId(event.target.value); setModelSearch(''); }}>{catalog.profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label><label>搜索模型<input type="search" aria-label="搜索模型" value={modelSearch} onChange={event => setModelSearch(event.target.value)} placeholder="输入模型名称或 ID" /></label></div>
          {modelProfile?.last_verified_at ? <p className="ai-service-catalog-verified">{modelProfile.name} · 最近验证 {timeLabel(modelProfile.last_verified_at)}</p> : null}
          {visibleModels.length ? <ul className="ai-service-catalog-list">{visibleModels.map(model => <li key={model.id}><div><strong>{model.name || model.id}</strong>{model.name && model.name !== model.id ? <code>{model.id}</code> : null}</div><div className="ai-service-catalog-meta">{model.context_window != null ? <span>上下文上限 {model.context_window.toLocaleString()} tokens</span> : null}{model.input_token_limit != null ? <span>输入上限 {model.input_token_limit.toLocaleString()} tokens</span> : null}</div></li>)}</ul> : <p className="ai-service-catalog-empty">{modelProfile?.models.length ? '没有匹配的模型。' : '尚未读取模型列表。请先测试对应接口的连接。'}</p>}
        </div>
      </details>
    </> : null}
  </section>;
}
