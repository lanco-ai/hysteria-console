import { useEffect, useState } from 'react';
import { AdminShell } from '../../shared/AdminShell';
import { money, priceCents, shopRequest } from './catalog';
import { isNoticeProduct } from './ShopNotice';

type Setting = { price_cents: number | null; published: boolean };
type SourceItem = { key: string; product_id: string; title: string; label: string; cost_cents: number; available: boolean };
type ProductCopy = Record<string, { description: string; after_sales: string }>;
type SourceStatus = { last_success: number | null; error: string | null; is_stale: boolean; cooldown_seconds: number; retry_after_seconds: number };
type Merchant = { sources?: Record<string, SourceStatus>; products?: ProductCopy; revision: number; telegram: string; skus: Record<string, Setting>; items: SourceItem[]; last_success: number | null; error: string | null; is_stale: boolean; cooldown_seconds: number; retry_after_seconds: number };
type Draft = Record<string, { price: string; published: boolean }>;
function draftFor(value: Merchant): Draft {
  const draft: Draft = {};
  for (const [key, setting] of Object.entries(value.skus)) draft[key] = { price: setting.price_cents === null ? '' : money(setting.price_cents).slice(1), published: setting.published };
  for (const item of value.items) draft[item.key] ??= { price: '', published: false };
  return draft;
}

export function ShopAdminPage() {
  const [state, setState] = useState<Merchant | null>(null);
  const [telegram, setTelegram] = useState('');
  const [draft, setDraft] = useState<Draft>({});
  const [copy, setCopy] = useState<ProductCopy>({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  function accept(value: Merchant) { setState(value); setTelegram(value.telegram); setDraft(draftFor(value)); setCopy(value.products ?? {}); setDirty(false); }
  useEffect(() => {
    let active = true;
    void shopRequest<Merchant>('admin').then(value => { if (active) accept(value); }).catch(reason => { if (active) setError(String(reason.message)); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!dirty) return;
    function unload(event: BeforeUnloadEvent) { event.preventDefault(); }
    window.addEventListener('beforeunload', unload);
    return () => window.removeEventListener('beforeunload', unload);
  }, [dirty]);
  function edit(key: string, change: Partial<Draft[string]>) {
    setDraft(value => ({ ...value, [key]: { price: '', published: false, ...value[key], ...change } }));
    setDirty(true); setNotice('');
  }
  async function reload() {
    if (dirty && !window.confirm('重新读取会丢弃尚未保存的输入，确定继续吗？')) return;
    setBusy(true); setError(''); setNotice('');
    try { accept(await shopRequest<Merchant>('admin')); } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }
  async function refresh() {
    setBusy(true); setError(''); setNotice('');
    try {
      const value = await shopRequest<Merchant>('refresh', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      // Keep the merchant revision and all unsaved values: source refresh is not a save.
      setState(old => old ? { ...old, sources: value.sources ?? { gpt: value }, items: value.items, last_success: value.last_success, error: value.error, is_stale: value.is_stale, cooldown_seconds: value.cooldown_seconds, retry_after_seconds: value.retry_after_seconds } : value);
      setDraft(old => { const next = { ...old }; for (const item of value.items) next[item.key] ??= { price: '', published: false }; return next; });
      // Newly discovered products receive effective defaults; existing dirty or
      // explicitly cleared input continues to win over the refreshed response.
      setCopy(old => ({ ...value.products, ...old }));
      setNotice(value.error ? '部分来源暂不可用；售价和未保存输入已保留。' : '来源检查完成；售价和未保存输入已保留。');
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }
  async function save() {
    if (!state) return;
    setError(''); setNotice('');
    const skus: Record<string, Setting> = {};
    for (const [key, value] of Object.entries(draft)) {
      const price = value.price === '' ? null : priceCents(value.price);
      if ((value.price !== '' && price === null) || (value.published && price === null)) { setError('请逐项填写有效售价（最多两位小数）；没有售价的规格不能上架。'); return; }
      skus[key] = { price_cents: price, published: value.published };
    }
    if (telegram && !/^[A-Za-z][A-Za-z0-9_]{4,31}$/.test(telegram)) { setError('请输入 5–32 位 Telegram 用户名，不包含 @ 或链接。'); return; }
    const settings = { revision: state.revision, telegram, skus, products: copy };
    const body = JSON.stringify(settings);
    const savedBody = JSON.stringify({ ...settings, revision: state.revision + 1 });
    const encoder = new TextEncoder();
    if (encoder.encode(body).length > 65536 || encoder.encode(savedBody).length > 65536) { setError('商品设置总内容过长，请缩短商品说明或售后条款后保存。'); return; }
    setBusy(true);
    try {
      accept(await shopRequest<Merchant>('admin', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body }));
      setNotice('商品设置已保存。');
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }
  const products = new Map<string, SourceItem[]>();
  for (const item of state?.items ?? []) {
    const group = products.get(item.product_id) ?? [];
    group.push(item);
    products.set(item.product_id, group);
  }
  return <AdminShell active="shop" pageTitle="商品管理" topbarExtra={<div className="shop-admin-actions"><button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void refresh()}>刷新来源</button><button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void reload()}>重新读取</button></div>}>
    <section className="shop-admin-panel">
      {error ? <p role="alert" className="shop-admin-feedback">{error}</p> : null}<p role="status" className="shop-admin-feedback">{notice}</p>
      {state ? <>
        <div className="shop-admin-source-status shop-note">{Object.entries(state.sources ?? { gpt: state }).map(([provider, source]) => <p key={provider}><strong>{provider === 'anli' ? 'Claude / Grok' : 'GPT'}</strong> · 最近成功更新：{source.last_success ? new Date(source.last_success * 1000).toLocaleString('zh-CN') : '尚未更新'}{source.is_stale ? ' · 商品信息待更新，此来源商品暂不能购买' : ' · 商品信息已更新'}{source.error ? ' · 来源暂不可用，稍后自动重试' : ''}{source.retry_after_seconds > 0 || source.cooldown_seconds > 0 ? ` · 刷新等待 ${Math.max(source.retry_after_seconds, source.cooldown_seconds)} 秒` : ''}</p>)}</div>
        <div className="shop-admin-contact"><label className="shop-telegram-field">商家 Telegram 用户名<input value={telegram} disabled={busy} placeholder="不含 @，例如 my_shop" autoComplete="off" onChange={event => { setTelegram(event.target.value.trim()); setDirty(true); setNotice(''); }}/></label><small>客户将通过此用户名联系商家；留空时无法联系购买。</small></div>
        <div className="shop-admin-products">{Array.from(products, ([productId, items]) => <section key={productId} className="shop-admin-product">
          <header className="shop-admin-product-header"><h2>{items[0]?.title}</h2><span>{items.length} 个规格</span></header>
          <details className="shop-copy-editor"><summary>商品说明与售后条款</summary>
            <p className="shop-note">上架后展示给客户，每项最多 4000 字。请填写本店实际提供的说明与条款。</p>
            {isNoticeProduct(productId) && <p className="shop-note">支持 Markdown 标题、列表和强调。清空并保存后，客户将看到未填写提示。</p>}
            {(['description', 'after_sales'] as const).map(field => <label key={field}>{field === 'description' ? '商品说明' : '售后条款'}<textarea aria-label={`${field === 'description' ? '商品说明' : '售后条款'} ${productId}`} rows={5} maxLength={4000} disabled={busy} value={copy[productId]?.[field] ?? ''} onChange={event => { setCopy(old => ({ ...old, [productId]: { description: '', after_sales: '', ...old[productId], [field]: event.target.value } })); setDirty(true); setNotice(''); }}/></label>)}
          </details>
          <div className="shop-admin-columns" aria-hidden="true"><span>规格</span><span>成本</span><span>售价（元）</span><span>上架</span></div>
          {items.map(item => <article key={item.key} className="shop-admin-row">
            <div className="shop-admin-spec"><strong>{item.label}</strong><small>SKU {item.key} · {item.available ? '来源可用' : '来源售罄'} · {draft[item.key]?.published ? '已选上架' : '草稿'}</small></div>
            <div className="shop-admin-cost"><span className="shop-admin-mobile-label">成本</span><strong>{money(item.cost_cents)}</strong></div>
            <label className="shop-admin-price"><span className="shop-admin-mobile-label">售价（元）</span><input aria-label={`售价 ${item.key}`} inputMode="decimal" value={draft[item.key]?.price ?? ''} disabled={busy} placeholder="待填写" onChange={event => edit(item.key, { price: event.target.value })}/></label>
            <label className="shop-publish"><input aria-label={`上架 ${item.key}`} type="checkbox" checked={draft[item.key]?.published ?? false} disabled={busy} onChange={event => edit(item.key, { published: event.target.checked })}/>上架</label>
          </article>)}
        </section>)}</div>
        {!state.items.length ? <p>尚无来源商品，请刷新来源。获取到的商品会先保存为草稿。</p> : null}
      </> : <p>正在读取商品管理数据…</p>}
      {state ? <div className="shop-admin-savebar"><span className="shop-note">{dirty ? '有尚未保存的修改。' : '设置已与服务器同步。'}</span><button type="button" className="btn btn-primary" disabled={busy} onClick={() => void save()}>{busy ? '处理中…' : '保存商品设置'}</button></div> : null}
    </section>
  </AdminShell>;
}
