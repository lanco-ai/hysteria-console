import { useEffect, useRef, useState, type FormEvent } from 'react';
import { AdminShell } from '../../shared/AdminShell';
import './services.css';

type Bookmark = { id: string; name: string; url: string; description: string; category: string; api_base: string; api_notes: string };
type Catalog = { items: Bookmark[]; revision: string };
const endpoint = '/api/v1/admin/services';
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
  const reload = async () => {
    setError(''); setBusy(true);
    try { setCatalog(await request()); } catch (value) { setError(value instanceof Error ? value.message : '读取失败'); }
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
  const categories = ['全部', ...new Set((catalog?.items || []).map(item => item.category || '常用网站'))];
  const items = (catalog?.items || []).filter(item => (category === '全部' || item.category === category) && `${item.name} ${item.description} ${item.url} ${item.api_notes}`.toLowerCase().includes(search.toLowerCase()));

  return <AdminShell active="services" pageTitle="服务中心" badge={publicHost}>
    <div className="services-page">
      <header className="services-intro"><div><span className="services-eyebrow">你的服务与常用网站</span><h2>一个入口，找到所有服务</h2><p>收藏管理后台与常用网址，随时打开或复制 API 地址。</p></div><button className="btn btn-primary" type="button" disabled={!catalog || busy || catalog.items.length >= 100} onClick={() => { setDraft(blank()); setFeedback(''); }}>＋ 添加网站</button></header>
      {error ? <div className="err" role="alert">{error} <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => void reload()}>刷新列表</button></div> : null}
      <p className="services-feedback" role="status">{feedback}</p>
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
      <div className="services-toolbar"><div className="services-filters" aria-label="网站分组">{categories.map(value => <button key={value} type="button" aria-pressed={category === value} onClick={() => setCategory(value)}>{value}</button>)}</div><input type="search" aria-label="搜索网站" placeholder="搜索名称、用途或地址" value={search} onChange={event => setSearch(event.target.value)}/></div>
      <div className="services-grid">{items.map(item => <article className="service-card" key={item.id}>
        <div className="service-card-heading"><span className="service-monogram" aria-hidden="true">{item.name.slice(0, 1).toUpperCase()}</span><div><h3>{item.name}</h3><span className="service-category">{item.category || '常用网站'}</span></div></div>
        <p className="service-description">{item.description || '暂无用途说明'}</p>
        <span className="service-address">{item.url}</span>
        {item.api_base ? <div className="service-api"><span>API Base URL</span><code>{item.api_base}</code><button className="btn btn-ghost btn-sm" type="button" onClick={() => void copy(item.api_base)}>复制 API 地址</button></div> : null}
        {item.api_notes ? <details className="service-api-details"><summary>提供的 API</summary><p>{item.api_notes}</p></details> : null}
        <footer><a className="btn btn-primary" href={item.url} target="_blank" rel="noopener noreferrer">打开网站 ↗</a><button className="btn btn-ghost btn-sm" type="button" disabled={busy} onClick={() => { setDraft({ ...item }); setDeleting(''); }}>编辑</button><button className="btn btn-ghost btn-sm" type="button" disabled={busy} onClick={() => setDeleting(item.id)}>删除</button></footer>
        {deleting === item.id ? <div className="service-delete" role="group" aria-label={`删除 ${item.name}`}><span>从收藏中移除？</span><button className="btn btn-sm" type="button" disabled={busy} onClick={() => void remove(item.id)}>确认删除</button><button className="btn btn-ghost btn-sm" type="button" disabled={busy} onClick={() => setDeleting('')}>取消</button></div> : null}
      </article>)}</div>
      {!catalog && !error ? <p>正在读取收藏…</p> : catalog && !items.length ? <div className="services-empty">{catalog.items.length ? '没有匹配的网站，试试其他搜索词或分组。' : '还没有收藏，添加第一个常用网站吧。'}</div> : null}
      <section className="services-monitor"><span className="services-monitor-icon" aria-hidden="true">◉</span><div><h3>服务器监控 <span>尚未接入</span></h3><p>已预留监控区域。以后可接入其他服务器，查看在线状态、响应时间与资源用量。</p></div></section>
      <p className="services-footnote">收藏保存在服务器，仅管理员可见。内网网址需要你的设备处于对应网络；各服务仍使用自己的登录方式。</p>
    </div>
  </AdminShell>;
}
