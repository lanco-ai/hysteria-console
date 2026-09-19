import { useEffect, useRef, useState, type FormEvent } from 'react';
import { AdminShell } from '../../shared/AdminShell';
import { Icon } from '../../shared/icons';
import './services.css';

type Bookmark = { id: string; name: string; url: string; description: string; category: string; api_base: string; api_notes: string };
type Catalog = { items: Bookmark[]; revision: string };
const endpoint = '/api/v1/admin/services';
const groupOf = (item: Bookmark) => item.category || '常用网站';
const apiLines = (notes: string) => notes.split('\n').filter(line => /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+\//i.test(line.trim()));
const originOf = (url: string) => { try { return new URL(url).origin; } catch { return url; } };

function ServiceGlyph({ name }: { name: 'search' | 'refresh' | 'delete' | 'clock' | 'cpu' | 'memory' }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === 'search' ? <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></> : name === 'refresh' ? <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.1 8a8 8 0 0 1 13-3L20 7M4 17l1.9 2a8 8 0 0 0 13-3"/></> : name === 'delete' ? <><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></> : name === 'clock' ? <><circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/></> : name === 'cpu' ? <><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/><rect x="10" y="10" width="4" height="4"/></> : <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/></>}
  </svg>;
}

const blank = (): Bookmark => ({ id: crypto.randomUUID(), name: '', url: '', description: '', category: '常用网站', api_base: '', api_notes: '' });

async function request(options?: RequestInit): Promise<Catalog> {
  const response = await fetch(endpoint, { credentials: 'same-origin', ...options });
  if (!response.ok) throw new Error(response.status === 401 || response.status === 403 ? '登录已失效，请重新登录。' : response.status === 409 ? '收藏已在其他页面更新，请先刷新列表再保存。' : response.status === 422 ? '请检查网址与内容长度，仅支持不包含账号密码的 HTTP / HTTPS 地址。' : '暂时无法保存或读取收藏，请稍后重试。');
  return response.json() as Promise<Catalog>;
}

export function ServicesPage({ publicHost }: { publicHost: string }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [draft, setDraft] = useState<Bookmark | null>(null);
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('全部');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [deleting, setDeleting] = useState('');
  const nameRef = useRef<HTMLInputElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const apiRefs = useRef<Record<string, HTMLDetailsElement | null>>({});
  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && !event.altKey) {
        event.preventDefault(); searchRef.current?.focus();
      }
    };
    window.addEventListener('keydown', focusSearch);
    return () => window.removeEventListener('keydown', focusSearch);
  }, []);
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
  useEffect(() => { if (draft) nameRef.current?.focus(); }, [draft?.id]);

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
  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); setFeedback('API 地址已复制'); }
    catch { setFeedback('自动复制不可用，请选中地址复制'); }
  };
  const editField = (field: keyof Bookmark, value: string) => setDraft(current => current ? { ...current, [field]: value } : current);
  const categories = ['全部', ...new Set(['AI 接口', '常用网站', ...(catalog?.items || []).map(groupOf)])];
  const items = (catalog?.items || []).filter(item => (category === '全部' || groupOf(item) === category) && `${item.name} ${item.description} ${item.url} ${item.api_notes}`.toLowerCase().includes(search.toLowerCase()));

  return <AdminShell active="services" pageTitle="服务中心" badge={publicHost}>
    <div className="services-page">
      <header className="services-intro"><div><h2>一个入口，找到所有服务</h2><p>收藏管理后台与常用网址，随时打开或复制 API 地址。</p></div></header>
      {error ? <div className="err" role="alert">{error} <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => void reload()}>刷新列表</button></div> : null}
      <p className={`services-feedback${feedback ? ' is-visible' : ''}`} role="status">{feedback}</p>
      {draft ? <form className="services-editor" onSubmit={event => void save(event)} aria-label="编辑网站">
        <div className="services-editor-heading"><h3>{catalog?.items.some(item => item.id === draft.id) ? '编辑网站' : '添加网站'}</h3><span>收藏会同步到登录此面板的其他设备</span></div>
        <fieldset disabled={busy}>
          <label>服务名称<input ref={nameRef} required maxLength={80} value={draft.name} onChange={event => editField('name', event.target.value)} placeholder="例如：家庭 NAS"/></label>
          <label>分组<input maxLength={40} required value={draft.category} onChange={event => editField('category', event.target.value)} list="service-groups"/><datalist id="service-groups"><option value="AI 接口"/><option value="服务器管理"/><option value="常用网站"/><option value="内网服务"/></datalist></label>
          <label className="services-wide">网站地址<input type="url" required maxLength={2048} value={draft.url} onChange={event => editField('url', event.target.value)} placeholder="https://example.com 或 http://192.168.1.10:8080"/></label>
          <label className="services-wide">服务用途<input maxLength={500} value={draft.description} onChange={event => editField('description', event.target.value)} placeholder="记下它是做什么的"/></label>
          <label className="services-wide">API Base URL（可选）<input type="url" maxLength={2048} value={draft.api_base} onChange={event => editField('api_base', event.target.value)} placeholder="https://example.com/v1"/></label>
          <label className="services-wide">提供哪些 API（可选）<textarea rows={4} maxLength={2000} value={draft.api_notes} onChange={event => editField('api_notes', event.target.value)} placeholder="例如：POST /v1/chat/completions — 对话接口"/></label>
        </fieldset>
        <div className="services-editor-actions"><small>这里只保存网址与说明，请勿填写密码或 API Key。</small><button className="btn btn-ghost" type="button" disabled={busy} onClick={() => setDraft(null)}>取消</button><button className="btn btn-primary" disabled={busy}>{busy ? '保存中…' : '保存网站'}</button></div>
      </form> : null}
      <div className="services-toolbar">
        <div className="services-filters" aria-label="网站分组">{categories.map(value => <button key={value} type="button" aria-pressed={category === value} onClick={() => setCategory(value)}>{value}<span>{(catalog?.items || []).filter(item => value === '全部' || groupOf(item) === value).length}</span></button>)}</div>
        <div className="services-toolbar-actions"><div className="services-search"><ServiceGlyph name="search"/><input ref={searchRef} type="search" aria-label="搜索网站" placeholder="搜索名称、用途或地址…" value={search} onChange={event => setSearch(event.target.value)}/><kbd>⌘ K</kbd></div><button className="btn service-secondary" type="button" title="刷新收藏列表" aria-label="刷新收藏列表" disabled={busy} onClick={() => void reload()}><ServiceGlyph name="refresh"/></button><button className="btn btn-primary" type="button" disabled={!catalog || busy || catalog.items.length >= 100} onClick={() => { setDraft(blank()); setFeedback(''); }}>＋ 添加网站</button></div>
      </div>
      <div className="services-grid">{items.map(item => <article className="service-card" key={item.id}>
        <div className="service-card-heading"><span className="service-monogram" aria-hidden="true">{item.name.slice(0, 1).toUpperCase()}</span><div className="service-identity"><div className="service-title"><h3>{item.name}</h3><span className="service-category">{groupOf(item)}</span></div><span className="service-address" title={item.url}>{originOf(item.url)}</span></div><span className="service-status" title="尚未接入服务状态检测">未监测</span></div>
        <p className="service-description">{item.description || '暂无用途说明'}</p>
        {item.api_base ? <div className="service-api"><span>API Base URL</span><div className="service-api-row"><code>{item.api_base}</code><button className="btn service-secondary" type="button" aria-label="复制 API 地址" onClick={() => void copy(item.api_base)}><Icon name="copy"/>复制</button></div></div> : null}
        {item.api_notes ? <details ref={node => { apiRefs.current[item.id] = node; }} className="service-api-details"><summary><span>提供的 API <small>点击展开查看</small></span><span className="service-count">{apiLines(item.api_notes).length}</span></summary><p>{item.api_notes}</p></details> : null}
        <footer><a className="btn btn-primary" href={item.url} target="_blank" rel="noopener noreferrer"><Icon name="open"/>打开网站 ↗</a>{item.api_notes ? <button className="btn service-secondary" type="button" onClick={() => { const detail = apiRefs.current[item.id]; if (detail) detail.open = !detail.open; }}><Icon name="rules"/>接口</button> : null}<button className="btn service-secondary" type="button" aria-label="编辑" disabled={busy} onClick={() => { setDraft({ ...item }); setDeleting(''); }}><Icon name="config"/>设置</button><button className="btn service-secondary service-danger" type="button" disabled={busy} onClick={() => setDeleting(item.id)}><ServiceGlyph name="delete"/>删除</button></footer>
        {deleting === item.id ? <div className="service-delete" role="group" aria-label={`删除 ${item.name}`}><span>从收藏中移除？</span><button className="btn btn-sm" type="button" disabled={busy} onClick={() => void remove(item.id)}>确认删除</button><button className="btn btn-ghost btn-sm" type="button" disabled={busy} onClick={() => setDeleting('')}>取消</button></div> : null}
      </article>)}</div>
      {!catalog && !error ? <p>正在读取收藏…</p> : catalog && !items.length ? <div className="services-empty">{catalog.items.length ? '没有匹配的网站，试试其他搜索词或分组。' : '还没有收藏，添加第一个常用网站吧。'}</div> : null}
      <section className="services-monitor"><div className="services-monitor-heading"><span className="service-monogram" aria-hidden="true"><Icon name="traffic"/></span><div><h3>服务器监控 <span>尚未接入</span></h3><p>已预留监控区域，接入后可查看服务器运行状态、响应时间与资源用量。</p></div></div><div className="services-metrics">{[{ label: '在线状态', icon: 'pulse' }, { label: '响应时间', icon: 'clock' }, { label: 'CPU 使用率', icon: 'cpu' }, { label: '内存使用率', icon: 'memory' }].map(metric => <div className="services-metric" key={metric.label}>{metric.icon === 'pulse' ? <Icon name="pulse"/> : <ServiceGlyph name={metric.icon as 'clock' | 'cpu' | 'memory'}/>}<div><span>{metric.label}</span><strong>—</strong><small>尚未接入</small></div></div>)}</div></section>
      <div className="services-footnote"><p>收藏保存在服务器，仅管理员可见。内网网址需要你的设备处于对应网络；各服务仍使用自己的登录方式。</p><span>共 {catalog?.items.length ?? 0} 个服务</span></div>

    </div>
  </AdminShell>;
}
