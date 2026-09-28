import { useEffect, useRef, useState, type FormEvent } from 'react';
import { AdminShell } from '../../shared/AdminShell';
import { Icon } from '../../shared/icons';
import { ServiceEditor, type Bookmark } from './ServiceEditor';
import { ServiceModels } from './ServiceModels';
import { AIServiceSettings } from './AIServiceSettings';
import './services.css';

type Catalog = { items: Bookmark[]; revision: string };
const endpoint = '/api/v1/admin/services';
const groupOf = (item: Bookmark) => item.category || '常用网站';
const originOf = (url: string) => { try { return new URL(url).origin; } catch { return url; } };

function ServiceGlyph({ name }: { name: 'search' | 'refresh' | 'delete' }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === 'search' ? <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></> : name === 'refresh' ? <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.1 8a8 8 0 0 1 13-3L20 7M4 17l1.9 2a8 8 0 0 0 13-3"/></> : <><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></>}
  </svg>;
}

const blank = (): Bookmark => ({ id: crypto.randomUUID(), name: '', url: '', description: '', category: '常用网站', api_base: '', api_notes: '', model_ids: [], models_checked_at: '' });

async function request(options?: RequestInit): Promise<Catalog> {
  const response = await fetch(endpoint, { credentials: 'same-origin', ...options });
  if (!response.ok) throw new Error(response.status === 401 || response.status === 403 ? '登录已失效，请重新登录。' : response.status === 409 ? '收藏已在其他页面更新，请先刷新列表再保存。' : response.status === 422 ? '请检查网址与内容长度，仅支持不包含账号密码的 HTTP / HTTPS 地址。' : '暂时无法保存或读取收藏，请稍后重试。');
  return response.json() as Promise<Catalog>;
}

export function ServicesPage({ publicHost }: { publicHost: string }) {
  const tabFromLocation = () => new URLSearchParams(window.location.search).get('tab') === 'websites' ? 'websites' : 'ai';
  const [activeTab, setActiveTab] = useState<'ai' | 'websites'>(tabFromLocation);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [draft, setDraft] = useState<Bookmark | null>(null);
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('全部');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [copiedId, setCopiedId] = useState('');
  useEffect(() => {
    if (!copiedId) return;
    const timer = window.setTimeout(() => setCopiedId(''), 2000);
    return () => window.clearTimeout(timer);
  }, [copiedId]);
  const [deleting, setDeleting] = useState('');
  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if (!document.querySelector('dialog[open]') && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && !event.altKey) {
        event.preventDefault(); searchRef.current?.focus();
      }
    };
    window.addEventListener('keydown', focusSearch);
    return () => window.removeEventListener('keydown', focusSearch);
  }, []);
  useEffect(() => {
    const onPopState = () => setActiveTab(tabFromLocation());
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);
  const selectTab = (tab: 'ai' | 'websites') => {
    setActiveTab(tab);
    const url = new URL(window.location.href);
    if (tab === 'ai') url.searchParams.set('tab', 'ai');
    else url.searchParams.set('tab', 'websites');
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
  };
  const reload = async () => {
    setError(''); setBusy(true);
    try { setCatalog(await request()); setFeedback('收藏列表已刷新'); } catch (value) { setError(value instanceof Error ? value.message : '读取失败'); }
    finally { setBusy(false); }
  };
  useEffect(() => {
    const controller = new AbortController();
    void request({ signal: controller.signal }).then(setCatalog).catch(value => {
      if (!controller.signal.aborted) setError(value instanceof Error ? value.message : '读取失败');
    });
    return () => controller.abort();
  }, []);

  const persist = async (items: Bookmark[]) => {
    if (!catalog || busy) return false;
    setBusy(true); setError(''); setFeedback('');
    try {
      setCatalog(await request({ method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ items, revision: catalog.revision }) }));
      setFeedback('收藏已保存'); return true;
    } catch (value) { setError(value instanceof Error ? value.message : '保存失败'); return false; }
    finally { setBusy(false); }
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!draft || !catalog) return;
    const exists = catalog.items.some(item => item.id === draft.id);
    if (await persist(exists ? catalog.items.map(item => item.id === draft.id ? draft : item) : [...catalog.items, draft])) setDraft(null);
  };
  const remove = async (id: string) => {
    if (catalog && await persist(catalog.items.filter(item => item.id !== id))) { setDeleting(''); if (draft?.id === id) setDraft(null); }
  };
  const copy = async (text: string, id: string) => {
    try { await navigator.clipboard.writeText(text); setFeedback('API 地址已复制'); setCopiedId(id); }
    catch { setError('自动复制不可用，请选中地址复制'); }
  };
  const editField = <K extends keyof Bookmark>(field: K, value: Bookmark[K]) => setDraft(current => current ? { ...current, [field]: value } : current);
  const categories = ['全部', ...new Set(['AI 接口', '常用网站', ...(catalog?.items || []).map(groupOf)])];
  const items = (catalog?.items || []).filter(item => (category === '全部' || groupOf(item) === category) && `${item.name} ${item.description} ${item.url} ${(item.model_ids || []).join(' ')}`.toLowerCase().includes(search.toLowerCase()));

  return <AdminShell active="services" pageTitle="服务中心" badge={publicHost}>
    <div className="services-page">
      <div className="services-page-tabs" role="tablist" aria-label="服务中心分类">
        <button type="button" role="tab" aria-selected={activeTab === 'ai'} onClick={() => selectTab('ai')}>API 接入</button>
        <button type="button" role="tab" aria-selected={activeTab === 'websites'} onClick={() => selectTab('websites')}>网站收藏</button>
      </div>
      {activeTab === 'ai' ? <AIServiceSettings /> : null}
      {activeTab === 'websites' ? <>
      {error && !draft ? <div className="err" role="alert">{error} <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => void reload()}>刷新列表</button></div> : null}
      <p className="services-feedback" role="status">{feedback}</p>
      {draft ? <ServiceEditor key={draft.id} draft={draft} existing={!!catalog?.items.some(item => item.id === draft.id)} busy={busy} error={error} onChange={editField} onSubmit={event => void save(event)} onClose={() => { setDraft(null); setError(''); }}/>: null}
      <div className="services-toolbar">
        <div className="services-filters" aria-label="网站分组">{categories.map(value => <button key={value} type="button" aria-pressed={category === value} onClick={() => setCategory(value)}>{value}<span>{(catalog?.items || []).filter(item => value === '全部' || groupOf(item) === value).length}</span></button>)}</div>
        <div className="services-toolbar-actions"><div className="services-search"><ServiceGlyph name="search"/><input ref={searchRef} type="search" aria-label="搜索网站" placeholder="搜索名称、用途或地址…" value={search} onChange={event => setSearch(event.target.value)}/><kbd>⌘ K</kbd></div><button className="btn service-secondary" type="button" title="刷新收藏列表" aria-label="刷新收藏列表" disabled={busy} onClick={() => void reload()}><ServiceGlyph name="refresh"/></button><button className="btn btn-primary" type="button" disabled={!catalog || busy || catalog.items.length >= 100} onClick={() => { setDraft(blank()); setFeedback(''); }}>＋ 添加网站</button></div>
      </div>
      <div className="services-grid">{items.map(item => <article className="service-card" key={item.id}>
        <div className="service-card-heading"><span className="service-monogram" aria-hidden="true">{item.name.slice(0, 1).toUpperCase()}</span><div className="service-identity"><div className="service-title"><h3>{item.name}</h3><span className="service-category">{groupOf(item)}</span></div><span className="service-address" title={item.url}>{originOf(item.url)}</span></div><div className="service-card-actions" role="group" aria-label="服务操作"><a className="btn service-secondary service-open" title="打开网站" aria-label="打开网站" href={item.url} target="_blank" rel="noopener noreferrer"><Icon name="open"/></a><button className="btn service-secondary" type="button" aria-label="编辑" title="设置" disabled={busy} onClick={() => { setDraft({ ...item }); setDeleting(''); }}><Icon name="config"/></button><button className="btn service-secondary service-danger" type="button" aria-label="删除" title="删除" disabled={busy} onClick={() => setDeleting(item.id)}><ServiceGlyph name="delete"/></button></div></div>
        <p className="service-description">{item.description || '暂无用途说明'}</p>
        {item.api_base ? <div className="service-api"><span>API Base URL</span><div className="service-api-row"><code>{item.api_base}</code><button className="btn service-secondary" type="button" aria-label="复制 API 地址" onClick={() => void copy(item.api_base, item.id)}><Icon name="copy"/>{copiedId === item.id ? '已复制' : '复制'}</button></div></div> : null}
        {item.api_base ? <ServiceModels item={item} onDetect={() => { setDraft({ ...item }); setDeleting(''); }}/> : null}

        {deleting === item.id ? <div className="service-delete" role="group" aria-label={`删除 ${item.name}`}><span>从收藏中移除？</span><button className="btn btn-sm" type="button" disabled={busy} onClick={() => void remove(item.id)}>确认删除</button><button className="btn btn-ghost btn-sm" type="button" disabled={busy} onClick={() => setDeleting('')}>取消</button></div> : null}
      </article>)}</div>
      {!catalog && !error ? <p>正在读取收藏…</p> : catalog && !items.length ? <div className="services-empty">{catalog.items.length ? '没有匹配的网站，试试其他搜索词或分组。' : '还没有收藏，添加第一个常用网站吧。'}</div> : null}
      <div className="services-footnote"><p>收藏保存在服务器，仅管理员可见。内网网址需要你的设备处于对应网络；各服务仍使用自己的登录方式。</p><span>共 {catalog?.items.length ?? 0} 个服务</span></div>
      </> : null}
    </div>
  </AdminShell>;
}
