import { useEffect, useRef, useState } from 'react';
import { shopRequest, type Catalog } from '../shop/catalog';
import { purchaseSummary, type CartItem } from '../shop/cart';

export function PurchaseDialog({ items, catalogVersion, onClose }: { items: CartItem[]; catalogVersion: Catalog | null; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const requestId = useRef(0);
  const [validated, setValidated] = useState<{ summary: string; telegram: string; wechat: string } | null>(null);
  const [busy, setBusy] = useState(true);
  const [notice, setNotice] = useState('');
  useEffect(() => {
    const id = ++requestId.current;
    const location = window.location.href;
    setValidated(null); setBusy(true); setNotice('');
    dialog.current?.showModal();
    void shopRequest<Catalog>('catalog').then(catalog => {
      const summary = purchaseSummary(catalog, items);
      if (id === requestId.current && location === window.location.href) setValidated({ summary, telegram: catalog.telegram, wechat: catalog.wechat ?? '' });
    }).catch(reason => { if (id === requestId.current && location === window.location.href) setNotice((reason as Error).message); }).finally(() => { if (id === requestId.current && location === window.location.href) setBusy(false); });
    return () => { requestId.current += 1; };
  }, [items, catalogVersion]);
  async function act(action: 'copy' | 'contact' | 'wechat') {
    if (busy || !validated) return;
    const id = ++requestId.current;
    const location = window.location.href;
    setBusy(true); setNotice(''); setValidated(null);
    try {
      const catalog = await shopRequest<Catalog>('catalog');
      const next = { summary: purchaseSummary(catalog, items), telegram: catalog.telegram, wechat: catalog.wechat ?? '' };
      if (id !== requestId.current || location !== window.location.href) return;
      setValidated(next);
      if (next.summary !== validated.summary || next.telegram !== validated.telegram || next.wechat !== validated.wechat) { setNotice('商品价格或联系方式已更新，请核对下方最新信息后再次操作。'); return; }
      if (action === 'copy' || action === 'wechat') {
        // WeChat has no link that opens a chat with an ID, so its ID is copied for 添加朋友.
        const text = action === 'copy' ? next.summary : next.wechat;
        const done = action === 'copy' ? '购买信息已复制，请发送给商家确认。' : `微信号 ${next.wechat} 已复制。请在微信「添加朋友」中搜索，添加后把购买信息发给商家。`;
        try { await navigator.clipboard.writeText(text); if (id === requestId.current && location === window.location.href) setNotice(done); }
        catch { if (id === requestId.current && location === window.location.href) setNotice(action === 'copy' ? '无法自动复制，请选中下方购买信息手动复制。' : `无法自动复制，请手动记下微信号 ${next.wechat}。`); }
      } else window.location.assign(`https://t.me/${next.telegram}`);
    } catch (reason) { if (id === requestId.current && location === window.location.href) { setValidated(null); setNotice((reason as Error).message); } }
    finally { if (id === requestId.current && location === window.location.href) setBusy(false); }
  }
  return <dialog ref={dialog} className="shop-purchase" aria-labelledby="purchase-title" onCancel={() => { requestId.current += 1; }} onClose={() => { requestId.current += 1; onClose(); }}>
    <header><div><p className="portal-eyebrow">PURCHASE DETAILS</p><h2 id="purchase-title">购买信息</h2></div><button type="button" className="btn btn-secondary" onClick={() => { requestId.current += 1; dialog.current?.close(); }} aria-label="关闭购买信息">关闭</button></header>
    <p className="shop-note">待商家人工确认 · 请联系商家确认商品及金额，人工付款后交付。</p>
    {busy ? <p role="status">正在核对最新商品信息…</p> : null}
    <p role="status">{notice}</p>
    {validated ? <><textarea aria-label="可复制的购买信息" readOnly value={validated.summary} rows={10}/>{validated.wechat ? <p className="shop-purchase-wechat">商家微信：<strong>{validated.wechat}</strong></p> : null}<div className="shop-purchase-actions"><button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void act('copy')}>复制购买信息</button>{validated.wechat ? <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void act('wechat')}>复制微信号</button> : null}{validated.telegram ? <a className={`btn ${validated.wechat ? 'btn-secondary' : 'btn-primary'}${busy ? ' is-disabled' : ''}`} aria-disabled={busy} href={`https://t.me/${validated.telegram}`} onClick={event => { event.preventDefault(); void act('contact'); }}>联系商家 Telegram</a> : null}</div></> : !busy ? <p>本次购买未生成，请关闭后检查商品并重试。</p> : null}
  </dialog>;
}
