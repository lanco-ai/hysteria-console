import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { feedback } from './presentation';
import type { Cycle, Mutate, Overview, OverviewUser } from './types';

function fields(form: HTMLFormElement): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [key, value] of new FormData(form)) if (typeof value === 'string') result[key] = value;
  return result;
}
function clearPasswords(form: HTMLFormElement) { form.querySelectorAll<HTMLInputElement>('input[type="password"]').forEach(input => { input.value = ''; }); }
function trapDialogTab(event: KeyboardEvent<HTMLDialogElement>) {
  if (event.key !== 'Tab') return;
  const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
    'button:not([disabled]), input:not([type="hidden"]):not([disabled]), select:not([disabled]), a[href]',
  ));
  const first = controls[0], last = controls[controls.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
}
function Input({ id, name, label, value = '', type = 'text', min, max, maxLength, minLength, hint, placeholder, required, wide }: {
  id: string; name: string; label: string; value?: string | number; type?: string; min?: string | number; max?: string | number; maxLength?: number; minLength?: number; hint?: string | undefined; placeholder?: string; required?: boolean; wide?: boolean;
}) {
  return <div className={`form-field${wide ? ' form-field-wide' : ''}`}>
    <label htmlFor={id}>{label}</label>
    <input id={id} name={name} type={type} defaultValue={value} min={min} max={max} maxLength={maxLength} minLength={minLength} placeholder={placeholder} required={required} autoComplete={type === 'password' ? 'new-password' : 'off'}/>{hint ? <span className="hint">{hint}</span> : null}</div>;
}
function Passwords({ prefix }: { prefix: 'create' | 'edit' }) {
  return <>
    <Input id={`${prefix}-panel-password`} name="panel_password" label="面板密码" type="password" minLength={8} maxLength={256} placeholder={prefix === 'edit' ? '留空保持不变' : '可选'} hint="至少 8 位"/>
    <Input id={`${prefix}-proxy-password`} name="password" label="代理密码" type="password" maxLength={256} placeholder={prefix === 'edit' ? '留空保持不变' : '可选'}/>
  </>;
}
function Options({ prefix, row }: { prefix: string; row?: OverviewUser }) {
  return <div className="form-options">
    <label className="switch">
      <input type="checkbox" name="guest" id={`${prefix}-guest`} defaultChecked={row?.metered ?? true}/>按量用户</label>
    <label className="switch">
      <input type="checkbox" name="tuic_enabled" id={`${prefix}-tuic-enabled`} defaultChecked={row?.tuic_enabled ?? false}/>允许 TUIC</label>
  </div>;
}
function QuotaFields({ prefix, row }: { prefix: 'create' | 'edit'; row?: OverviewUser }) {
  return <>
    <Input id={`${prefix}-quota-gb`} name="quota_gb" label="流量上限 GB" type="number" min={0} max={10240} value={row?.base_quota_gb ?? 150} required={prefix === 'create'} hint={prefix === 'create' ? '0 = 不限' : undefined}/>
    <Input id={`${prefix}-quota-extra-gb`} name="quota_extra_gb" label="加量包 GB" type="number" min={0} max={10240} value={row?.quota_extra_gb ?? 0} required={prefix === 'create'}/>
    <Input id={`${prefix}-expires-at`} name="expires_at" label="到期日" type="date" min="2000-01-01" max="2099-12-31" value={row?.expires_at ?? ''} hint="留空 = 不限期；年份范围 2000–2099"/>
  </>;
}

function invalidField(form: HTMLFormElement, code: string, field: string, edit: boolean) {
  const codeFields = new Map([['err:max_devices_invalid', 'edit-max-devices'], ['err:quota_invalid', 'edit-quota-gb'], ['err:quota_extra_invalid', 'edit-quota-extra-gb'], ['err:expiry_invalid', 'edit-expires-at'], ['err:note_too_long', 'edit-note'], ['err:landing_invalid', 'edit-landing-isp'], ['err:landing_too_long', 'edit-landing-isp'], ['err:landing_ip_invalid', 'edit-landing-ip'], ['err:panel_password_short', 'edit-panel-password'], ['err:panel_password_long', 'edit-panel-password'], ['err:proxy_password_long', 'edit-proxy-password']]);
  const id = edit ? codeFields.get(code) || field.replace(/^create-/, 'edit-') : field;
  const input = Array.from(form.elements).find(item => item.id === id);
  if (input instanceof HTMLElement) { input.setAttribute('aria-invalid', 'true'); input.setAttribute('aria-describedby', edit ? 'edit-error' : 'create-add-error'); input.focus(); }
}

export function CreateForm({ data, disabled, mutate }: { data: Overview; disabled: boolean; mutate: Mutate }) {
  const details = useRef<HTMLDetailsElement>(null);
  const [error, setError] = useState('');
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = event.currentTarget;
    const payload = fields(form);
    setError(''); form.querySelectorAll('[aria-invalid]').forEach(input => input.removeAttribute('aria-invalid'));
    try {
      const result = await mutate('create', payload, () => { form.reset(); if (details.current) details.current.open = false; });
      if (result && result.kind !== 'success') {
        setError(feedback(result.code));
        if (result.kind === 'invalid') invalidField(form, result.code, result.field, false);
      }
    } finally { clearPasswords(form); }
  };
  return <details className="create-section" ref={details}>
    <summary className="create-toggle">+ 新增用户</summary>
    <div className="create-form">{error ? <div className="err" id="create-add-error" role="alert">{error}</div> : null}<form method="post" action="/admin/add" className="inline-form" onSubmit={event => { void submit(event); }}>
        <div className="create-grid">
          <Input id="create-user" name="user" label="用户名" maxLength={64} required/>
          <Passwords prefix="create"/>
          <QuotaFields prefix="create"/>
          <div className="form-field">
            <label htmlFor="create-landing-initial-egress">初始家宽出口</label>
            <select className="select" id="create-landing-initial-egress" name="landing_initial_egress_id" defaultValue="" disabled={!data.landing_options.length}>
              <option value="">{data.landing_options.length ? '暂不分配' : '暂无可用节点'}</option>{data.landing_options.map(option => <option key={option.id} value={option.id}>{option.name}</option>)}</select>
            <span className="hint">{data.landing_options.length ? '选中后立即授权并设为初始出口；后续可增加更多节点' : <a href="/admin/landing-egresses">先添加或启用家宽出口节点</a>}</span>
          </div>
          <Input id="create-note" name="note" label="备注" maxLength={200} placeholder="可选" wide/>
        </div>
        <Options prefix="create"/>
        <button className="btn" type="submit" disabled={disabled}>创建</button>
      </form>
    </div>
  </details>;
}

export function EditDialog({ row, currentRevision, disabled, blocked, mutate, refresh, close }: { row: OverviewUser; currentRevision: string | undefined; disabled: boolean; blocked: boolean; mutate: Mutate; refresh: () => void; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [error, setError] = useState('');
  useEffect(() => { const node = dialog.current; node?.showModal(); return () => { node?.close(); }; }, []);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = event.currentTarget;
    const payload = fields(form); setError('');
    form.querySelectorAll('[aria-invalid]').forEach(input => input.removeAttribute('aria-invalid'));
    try {
      const result = await mutate('update', payload, close);
      if (result && result.kind !== 'success') { setError(feedback(result.code)); if (result.kind === 'invalid') invalidField(form, result.code, result.field, true); }
    } finally { clearPasswords(form); }
  };
  return <dialog id="user-edit-dialog" className="admin-dialog" aria-labelledby="user-edit-title" ref={dialog} onKeyDown={trapDialogTab} onCancel={event => { event.preventDefault(); if (!disabled) close(); }}>
    <div className="dialog-inner">
      <div className="dialog-head">
        <h2 className="dialog-title" id="user-edit-title">编辑 {row.user}</h2>
        <button type="button" className="dialog-close" aria-label="关闭" disabled={disabled} onClick={close}>×</button>
      </div>
      {currentRevision !== row.revision ? <div className="err" role="status">此用户配置已更改；草稿保留原版本，保存时可能冲突</div> : null}
      {error ? <div className="err" id="edit-error" role="alert">{error}</div> : null}
      {blocked ? <button type="button" className="btn btn-sm" disabled={disabled} onClick={refresh}>刷新核对</button> : null}
      <form method="post" id="user-edit-form" action="/admin/update" onSubmit={event => { void submit(event); }}>
        <input type="hidden" name="user" id="edit-user-name" value={row.user}/>
        <input type="hidden" name="user_revision" id="edit-user-revision" value={row.revision}/>
        <div className="form-grid">
          <Input id="edit-max-devices" name="max_devices" label="最大设备数" type="number" min={0} max={999} value={row.max_devices}/>
          <QuotaFields prefix="edit" row={row}/>
          <Input id="edit-note" name="note" label="备注" maxLength={200} value={row.note} placeholder="可选" wide/>
          <Input id="edit-landing-isp" name="landing_isp" label="落地运营商" maxLength={120} value={row.landing_isp} placeholder="可选，仅展示"/>
          <Input id="edit-landing-region" name="landing_region" label="落地地区" maxLength={120} value={row.landing_region} placeholder="可选，仅展示"/>
          <Input id="edit-landing-ip" name="landing_ip" label="家宽 IP" maxLength={45} value={row.landing_ip} placeholder="可选，仅支持 IPv4 / IPv6"/>
          <Input id="edit-landing-note" name="landing_note" label="落地说明" maxLength={120} value={row.landing_note} placeholder="可选，仅展示，不影响出口" wide/>
          <Passwords prefix="edit"/>
        </div>
        <Options prefix="edit" row={row}/>
        <div className="dialog-foot">
          <button type="button" className="btn ghost btn-sm" disabled={disabled} onClick={close}>取消</button>
          <button type="submit" className="btn primary btn-sm" disabled={disabled || blocked}>保存更改</button>
        </div>
      </form>
    </div>
  </dialog>;
}

export function CycleForm({ cycle, disabled, mutate }: { cycle: Cycle; disabled: boolean; mutate: Mutate }) {
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = event.currentTarget;
    if (disabled || !window.confirm('更改结算日或周期会重新锚定计费日历，确认保存？')) return;
    const result = await mutate('cycle', fields(form));
    if (result?.kind === 'invalid') document.getElementById(result.field)?.focus();
  };
  return <form method="post" action="/admin/cycle-config" className="inline-form-row cycle-config-form" onSubmit={event => { void submit(event); }}>
    <label htmlFor="cycle-day" className="small">结算日</label>
    <input id="cycle-day" name="day" type="number" min={1} max={28} defaultValue={cycle.settlement_day} required/>
    <label htmlFor="cycle-length" className="small">周期</label>
    <input id="cycle-length" name="length" type="number" min={cycle.length_min} max={cycle.length_max} defaultValue={cycle.length_days} required/>
    <span className="small">天</span>
    <button className="btn ghost btn-sm" type="submit" disabled={disabled}>保存</button>
  </form>;
}
