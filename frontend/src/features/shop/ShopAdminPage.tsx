import { useEffect, useState } from 'react';
import { AdminShell } from '../../shared/AdminShell';
import { money, priceCents, shopRequest } from './catalog';

type Setting = { price_cents: number | null; published: boolean };
type SourceItem = { key: string; title: string; label: string; cost_cents: number; available: boolean };
type Merchant = { revision: number; telegram: string; skus: Record<string, Setting>; items: SourceItem[]; last_success: number | null; error: string | null; is_stale: boolean; cooldown_seconds: number; retry_after_seconds: number };
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
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  function accept(value: Merchant) { setState(value); setTelegram(value.telegram); setDraft(draftFor(value)); setDirty(false); }
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
      setState(old => old ? { ...old, items: value.items, last_success: value.last_success, error: value.error, is_stale: value.is_stale, cooldown_seconds: value.cooldown_seconds, retry_after_seconds: value.retry_after_seconds } : value);
      setDraft(old => { const next = { ...old }; for (const item of value.items) next[item.key] ??= { price: '', published: false }; return next; });
      setNotice(value.error ? '来源暂不可用，保留上次商品与售价。' : '来源检查完成；售价和未保存输入已保留。');
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
    setBusy(true);
    try {
      accept(await shopRequest<Merchant>('admin', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: state.revision, telegram, skus }) }));
      setNotice('商品设置已保存。');
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }
  return <AdminShell active="shop" pageTitle="商品管理" subtitle="独立售价 · 人工确认与交付">
    <section className="shop-admin-panel">
      <p className="shop-note">成本仅管理员可见。新规格默认草稿，填写售价并勾选上架后才会展示给客户。来源刷新不会覆盖售价。</p>
      <div className="shop-admin-toolbar"><button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void refresh()}>刷新来源</button><button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void reload()}>重新读取</button><button type="button" className="btn btn-primary" disabled={busy || !state} onClick={() => void save()}>{busy ? '处理中…' : '保存商品设置'}</button></div>
      {error ? <p role="alert">{error}</p> : null}<p role="status">{notice}</p>
      {state ? <>
        <p className="shop-note">最近成功更新：{state.last_success ? new Date(state.last_success * 1000).toLocaleString('zh-CN') : '尚未更新'}{state.is_stale ? ' · 商品信息待更新，客户暂不能购买' : ''}{state.error ? ' · 来源暂不可用，稍后自动重试' : ''}</p>
        {state.retry_after_seconds > 0 || state.cooldown_seconds > 0 ? <p className="shop-note">读取时刷新等待：{Math.max(state.retry_after_seconds, state.cooldown_seconds)} 秒。重复点击不会绕过等待。</p> : null}
        <label className="shop-telegram-field">商家 Telegram 用户名<input value={telegram} disabled={busy} placeholder="不含 @，例如 my_shop" autoComplete="off" onChange={event => { setTelegram(event.target.value.trim()); setDirty(true); setNotice(''); }}/><small>填写你自己的收款与交付联系人。留空时客户无法联系购买。</small></label>
        <div className="shop-admin-products">{state.items.map(item => <article key={item.key} className="shop-admin-row"><div><h2>{item.title}</h2><p>{item.label} · {item.available ? '来源可用（数量未公开）' : '来源售罄'} · {draft[item.key]?.published ? '已选上架' : '草稿'}</p><p>成本：<strong>{money(item.cost_cents)}</strong> <small>仅管理员可见</small></p></div><label>售价（元）<input aria-label={`售价 ${item.key}`} inputMode="decimal" value={draft[item.key]?.price ?? ''} disabled={busy} placeholder="待填写" onChange={event => edit(item.key, { price: event.target.value })}/></label><label className="shop-publish"><input aria-label={`上架 ${item.key}`} type="checkbox" checked={draft[item.key]?.published ?? false} disabled={busy} onChange={event => edit(item.key, { published: event.target.checked })}/>上架</label></article>)}</div>
        {!state.items.length ? <p>尚无来源商品，请刷新来源。获取到的商品会先保存为草稿。</p> : null}
        <p className="shop-note">{dirty ? '有尚未保存的修改。' : '设置已与服务器同步。'}</p>
      </> : <p>正在读取商品管理数据…</p>}
    </section>
  </AdminShell>;
}
