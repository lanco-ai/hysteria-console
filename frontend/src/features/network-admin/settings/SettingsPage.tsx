import { useEffect, useState } from 'react';
import { usePasswordChange } from '../../auth/usePasswordChange';
import { validatePasswordPage } from '../../auth/passwordPageTypes';
import { AdminShell } from '../../../shared/AdminShell';
import { LoadingState } from '../../../shared/LoadingState';
import { useReadResource } from '../../../shared/readResource';
import { useInitialFragmentNavigation } from '../../../shared/useInitialFragmentNavigation';
import { AlertsSection } from './AlertsSection';

const fragmentTargets = new Set(['main-content']);
const SECTIONS = [
  { id: 'settings-account', label: '账号与安全', hint: '管理员账号与密码' },
  { id: 'settings-alerts', label: '告警通知', hint: 'Telegram · Webhook' },
  { id: 'settings-interface', label: '界面偏好', hint: '仅当前浏览器' },
] as const;
type SectionId = typeof SECTIONS[number]['id'];
const initialMessages = new Map<string, string>([
  ['password changed', '管理员密码已更新'],
  ['password_wrong', '当前密码不正确'],
  ['password_mismatch', '两次输入的新密码不一致'],
  ['password_short', '新密码至少 8 位'],
]);
// Unambiguous characters only, so a generated password survives being read aloud or retyped.
const GENERATED_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789-_.!@#%';

function initialFeedback(passwordMaxLength: number) {
  const raw = new URLSearchParams(window.location.search).get('msg') || '';
  if (!raw) return { message: '', kind: 'flash' as const };
  const error = raw.startsWith('err:');
  const key = raw.replace(/^err:/, '');
  const message = key === 'password_long'
    ? `密码不能超过 ${passwordMaxLength} 位`
    : (initialMessages.get(key) ?? key);
  return { message, kind: error ? 'err' as const : 'flash' as const };
}

/** A rough guide for people, not a policy: the server enforces only the length limits. */
function passwordStrength(value: string, min: number): { level: 0 | 1 | 2 | 3 | 4; label: string } | null {
  if (!value) return null;
  if (value.length < min) return { level: 0, label: `太短，至少 ${min} 位` };
  const kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter(pattern => pattern.test(value)).length;
  const repeated = /^(.)\1+$/.test(value) || new Set(value).size <= 3;
  const score = (value.length >= 12 ? 1 : 0) + (value.length >= 16 ? 1 : 0) + (kinds >= 3 ? 1 : 0) + 1 - (repeated ? 1 : 0);
  const level = Math.max(1, Math.min(4, score)) as 1 | 2 | 3 | 4;
  return { level, label: ['', '弱', '一般', '较强', '强'][level] ?? '' };
}

function generatePassword(length = 20) {
  const values = crypto.getRandomValues(new Uint32Array(length));
  return Array.from(values, value => GENERATED_ALPHABET[value % GENERATED_ALPHABET.length]).join('');
}

function useActiveSection(): [SectionId, (id: SectionId) => void] {
  const [active, setActive] = useState<SectionId>(SECTIONS[0].id);
  useEffect(() => {
    const nodes = SECTIONS.map(section => document.getElementById(section.id)).filter((node): node is HTMLElement => !!node);
    if (!nodes.length || typeof IntersectionObserver === 'undefined') return;
    const visible = new Map<string, number>();
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) visible.set(entry.target.id, entry.isIntersecting ? entry.intersectionRatio : 0);
      const first = SECTIONS.find(section => (visible.get(section.id) ?? 0) > 0);
      if (first) setActive(first.id);
    }, { rootMargin: '-72px 0px -55% 0px', threshold: [0, 0.01, 0.5, 1] });
    nodes.forEach(node => observer.observe(node));
    return () => observer.disconnect();
  }, []);
  return [active, setActive];
}

function SettingsContent({ username, min, max }: { username: string; min: number; max: number }) {
  const initial = initialFeedback(max);
  const password = usePasswordChange('admin', max, initial.message, initial.kind);
  const [motion, setMotion] = useState(() => document.documentElement.classList.contains('sidebar-motion-enabled'));
  const [revealed, setRevealed] = useState(false);
  const [active, setActive] = useActiveSection();
  const strength = passwordStrength(password.draft.new, min);
  const confirmState = !password.draft.confirm ? null : password.draft.confirm === password.draft.new ? 'match' : 'mismatch';

  useEffect(() => {
    const fragment = window.location.hash.slice(1);
    if (SECTIONS.some(section => section.id === fragment)) document.getElementById(fragment)?.scrollIntoView();
  }, []);

  const changeMotion = (checked: boolean) => {
    setMotion(checked);
    document.documentElement.classList.toggle('sidebar-motion-enabled', checked);
    try {
      if (checked) localStorage.setItem('hy2.sidebar-motion', 'enabled');
      else localStorage.removeItem('hy2.sidebar-motion');
    } catch {
      // Storage may be unavailable; the visible preference still applies.
    }
  };
  const fillGenerated = () => {
    const value = generatePassword();
    password.change('new', value);
    password.change('confirm', value);
    setRevealed(true);
  };
  const inputType = revealed ? 'text' : 'password';

  return <>
    {password.feedback ? <div className={password.feedbackKind} role={password.feedbackKind === 'err' ? 'alert' : 'status'} aria-live={password.feedbackKind === 'err' ? 'assertive' : 'polite'} aria-atomic="true">{password.feedback}</div> : null}
    <div className="admin-page settings-page">
      <nav className="settings-nav" aria-label="设置分类">
        {SECTIONS.map(section => <a key={section.id} href={`#${section.id}`} aria-current={active === section.id ? 'location' : undefined} onClick={() => setActive(section.id)}>
          <span>{section.label}</span><small>{section.hint}</small>
        </a>)}
      </nav>

      <div className="settings-sections">
        <section id="settings-account" className="settings-card" aria-labelledby="settings-account-title">
          <header className="settings-card-head">
            <h2 id="settings-account-title">账号与安全</h2>
            <p>控制台只有一个管理员账号；用户面板的账号在「用户」页管理。</p>
          </header>
          <div className="settings-account">
            <span className="settings-avatar" aria-hidden="true">{(username.trim()[0] || '·').toUpperCase()}</span>
            <div className="settings-account-text">
              <span className="settings-account-label">管理员账号</span>
              <code>{username}</code>
            </div>
            <span className="settings-chip is-on"><i aria-hidden="true"/>当前登录</span>
          </div>

          <form method="post" action="/admin/change-password" className="settings-password" onSubmit={password.onSubmit}>
            <h3>修改密码</h3>
            <div className="form-field">
              <label htmlFor="settings-current-password">当前密码</label>
              <input id="settings-current-password" name="current" type={inputType} maxLength={max} autoComplete="current-password" required value={password.draft.current} onChange={event => password.change('current', event.target.value)}/>
            </div>
            <div className="form-field">
              <label htmlFor="settings-new-password">新密码（至少 {min} 位）</label>
              <input id="settings-new-password" name="new" type={inputType} minLength={min} maxLength={max} autoComplete="new-password" required aria-describedby="settings-password-strength" value={password.draft.new} onChange={event => password.change('new', event.target.value)}/>
              <div className="password-strength" data-level={strength?.level ?? 'empty'}>
                <span className="password-strength-bar" aria-hidden="true"><i/><i/><i/><i/></span>
                <span id="settings-password-strength">{strength ? `强度：${strength.label}` : '建议 12 位以上，混合大小写、数字和符号'}</span>
              </div>
            </div>
            <div className="form-field">
              <label htmlFor="settings-confirm-password">确认新密码</label>
              <input id="settings-confirm-password" name="confirm" type={inputType} minLength={min} maxLength={max} autoComplete="new-password" required aria-describedby="settings-password-match" value={password.draft.confirm} onChange={event => password.change('confirm', event.target.value)}/>
              <span id="settings-password-match" className={`password-match${confirmState ? ` is-${confirmState}` : ''}`}>{confirmState === 'match' ? '✓ 两次输入一致' : confirmState === 'mismatch' ? '两次输入还不一致' : ''}</span>
            </div>
            <div className="settings-actions">
              <button className="btn btn-ghost btn-sm" type="button" aria-pressed={revealed} onClick={() => setRevealed(value => !value)}>{revealed ? '隐藏密码' : '显示密码'}</button>
              <button className="btn btn-ghost btn-sm" type="button" disabled={password.busy} onClick={fillGenerated}>生成随机密码</button>
              <button className="btn btn-primary" type="submit" disabled={password.busy} aria-busy={password.busy ? true : undefined}>{password.busy ? '正在更新…' : '更新密码'}</button>
            </div>
            <span className="sr-only" role="status" aria-live="polite">{password.progress}</span>
            <p className="settings-muted">更新后将注销所有已登录会话（其它设备需重新登录），但本设备会保持登录。生成的密码请先保存到密码管理器。</p>
          </form>
        </section>

        <section id="settings-alerts" className="settings-card" aria-labelledby="settings-alerts-title">
          <header className="settings-card-head">
            <h2 id="settings-alerts-title">告警通知</h2>
            <p id="settings-alerts-desc">用户流量达到 80% / 100%、套餐即将到期或已到期、单日流量异常时推送提醒。令牌、地址和密钥保存后只写不读，页面不会再显示。</p>
          </header>
          <AlertsSection/>
        </section>

        <section id="settings-interface" className="settings-card" aria-labelledby="settings-interface-title">
          <header className="settings-card-head">
            <h2 id="settings-interface-title">界面偏好</h2>
            <p>只保存在当前浏览器，不影响其他设备。</p>
          </header>
          <div className="settings-pref">
            <div>
              <label htmlFor="sidebar-motion-toggle">侧边栏动画</label>
              <small>默认跟随系统的减少动态效果设置；打开后本浏览器强制显示侧边栏展开与收起动画，只影响桌面侧边栏。</small>
            </div>
            <input className="settings-switch" type="checkbox" role="switch" id="sidebar-motion-toggle" checked={motion} onChange={event => changeMotion(event.target.checked)}/>
          </div>
        </section>
      </div>
    </div>
  </>;
}

export function SettingsPage({ publicHost }: { publicHost: string }) {
  const resource = useReadResource('/api/v1/admin/settings', { validate: validatePasswordPage });
  useInitialFragmentNavigation(fragmentTargets);

  useEffect(() => {
    if (resource.status === 'error' && resource.error.status === 401) {
      window.location.assign('/login');
    }
  }, [resource]);

  let content;
  if (resource.status === 'success') {
    content = <SettingsContent username={resource.data.username} min={resource.data.password_min_length} max={resource.data.password_max_length}/>;
  } else if (resource.status === 'error' && resource.error.status !== 401) {
    content = <div className="card"><div className="err" role="alert" aria-live="assertive" aria-atomic="true">加载失败：{resource.error.message}</div><div className="row mt-md"><button className="btn secondary" type="button" onClick={resource.retry}>重试</button></div></div>;
  } else {
    content = <LoadingState label="正在加载设置…"/>;
  }
  return <AdminShell active="settings" badge={publicHost} pageTitle="设置">{content}</AdminShell>;
}
