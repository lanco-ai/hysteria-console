import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useInitialFragmentNavigation } from '../../shared/useInitialFragmentNavigation';
import { submitLogin } from './loginRequest';

const fragmentTargets = new Set(['main-content']);
const TRANSPORT_ERROR = '登录结果未确认，请检查网络后重试。';

function LockIcon() {
  return <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>;
}

export function LoginPage({ passwordMaxLength }: { passwordMaxLength: number }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');
  const [feedback, setFeedback] = useState('');
  const usernameRef = useRef(username);
  const passwordRef = useRef(password);
  const pendingRef = useRef(false);
  const requestRef = useRef<{ controller: AbortController; generation: number } | null>(null);
  const generationRef = useRef(0);

  useInitialFragmentNavigation(fragmentTargets);

  const resetInteraction = () => {
    generationRef.current += 1;
    requestRef.current?.controller.abort();
    requestRef.current = null;
    pendingRef.current = false;
    setBusy(false);
    setProgress('');
    setPasswordVisible(false);
  };

  useEffect(() => {
    const onPageShow = () => resetInteraction();
    const onPageHide = () => {
      generationRef.current += 1;
      requestRef.current?.controller.abort();
      requestRef.current = null;
      pendingRef.current = false;
    };
    window.addEventListener('pageshow', onPageShow);
    window.addEventListener('pagehide', onPageHide);
    return () => {
      window.removeEventListener('pageshow', onPageShow);
      window.removeEventListener('pagehide', onPageHide);
      generationRef.current += 1;
      requestRef.current?.controller.abort();
      requestRef.current = null;
      pendingRef.current = false;
    };
  }, []);

  const changeUsername = (value: string) => {
    usernameRef.current = value;
    setUsername(value);
  };
  const changePassword = (value: string) => {
    passwordRef.current = value;
    setPassword(value);
  };

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pendingRef.current) return;
    pendingRef.current = true;
    const submitted = { username: usernameRef.current, password: passwordRef.current };
    const controller = new AbortController();
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    requestRef.current = { controller, generation };
    setBusy(true);
    setFeedback('');
    setProgress('正在验证登录信息');
    const timeout = window.setTimeout(() => controller.abort(), 15_000);
    try {
      const result = await submitLogin(submitted, controller.signal);
      if (generationRef.current !== generation) return;
      if (result.ok) {
        window.location.assign(result.redirect_to);
        return;
      }
      setFeedback(result.message);
      setPasswordVisible(false);
      if (usernameRef.current === submitted.username) changeUsername(submitted.username.trim());
      if (passwordRef.current === submitted.password) changePassword('');
    } catch {
      if (generationRef.current !== generation) return;
      setFeedback(TRANSPORT_ERROR);
    } finally {
      window.clearTimeout(timeout);
      if (generationRef.current === generation) {
        requestRef.current = null;
        pendingRef.current = false;
        setBusy(false);
        setProgress('');
      }
    }
  };

  return <>
    <a className="skip-link" href="#main-content">跳到主内容</a>
    <main id="main-content" tabIndex={-1}>
      <header className="auth-header">
        <div className="auth-header-inner">
          <a href="/" className="auth-header-logo"><span className="login-mark" aria-hidden="true">H</span>Hysteria</a>
          <a href="/" className="auth-header-back">← 返回首页</a>
        </div>
      </header>
      <main className="login-stage">
        <div className="login-layout">
          <section className="login-story" aria-labelledby="login-story-title">
            <div className="login-eyebrow"><span/> NETWORK CONSOLE</div>
            <h1 id="login-story-title">连接网络，<br/><span>掌控全局。</span></h1>
            <p className="login-description">让每一次连接，清晰可见。<br/>在一个控制台中，管理你的网络。</p>
            <div className="login-network" aria-hidden="true">
              <svg viewBox="0 0 460 220" fill="none">
                <defs><linearGradient id="login-line"><stop stopColor="#b9d8e1"/><stop offset="1" stopColor="#68a8bf"/></linearGradient></defs>
                <path d="M28 162H94L155 101H253L314 40H425M94 162H235L285 112H422M155 101V46H212M253 101V182H375" stroke="#d1dce0" strokeWidth="1"/>
                <path d="M28 162H94L155 101H253L314 40H425" stroke="url(#login-line)" strokeWidth="2"/>
                <circle cx="155" cy="101" r="20" fill="#dceef3" fillOpacity=".65"/>
                <circle cx="155" cy="101" r="6" fill="#508da3" stroke="white" strokeWidth="3"/>
                <circle cx="253" cy="101" r="5" fill="#fff" stroke="#7aa5b5" strokeWidth="2"/>
                <circle cx="314" cy="40" r="5" fill="#fff" stroke="#7aa5b5" strokeWidth="2"/>
                <circle cx="285" cy="112" r="4" fill="#9bbbc6"/>
                <rect x="365" y="170" width="30" height="24" rx="5" fill="#fff" stroke="#cad8dd"/>
                <path d="M375 178h10m-10 7h6" stroke="#83a4b0" strokeWidth="2" strokeLinecap="round"/>
                <circle cx="425" cy="40" r="3" fill="#83a4b0"/>
                <circle cx="28" cy="162" r="3" fill="#83a4b0"/>
              </svg>
            </div>
            <div className="login-story-foot"><span>HYSTERIA</span><span>连接 · 洞察 · 管理</span></div>
          </section>
          <section className="login-panel" aria-labelledby="login-title">
            <div className="login-panel-kicker"><LockIcon/><span>管理员访问</span></div>
            <h2 id="login-title">登录控制台</h2>
            <p className="login-subtitle">使用管理员账号登录。</p>
            <form method="post" action="/login" className="login-form" id="form-admin" onSubmit={onSubmit}>
              <div className="login-feedback">{feedback ? <div className="err" role="alert" aria-live="assertive" aria-atomic="true">{feedback}</div> : null}</div>
              <div className="field">
                <label className="label" htmlFor="admin-username">管理员账号</label>
                <input className="input" id="admin-username" name="admin_username" value={username} onChange={event => changeUsername(event.target.value)} required autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder="输入管理员账号"/>
              </div>
              <div className="field">
                <label className="label" htmlFor="admin-password">密码</label>
                <div className="login-password">
                  <input className="input" id="admin-password" name="admin_password" type={passwordVisible ? 'text' : 'password'} value={password} onChange={event => changePassword(event.target.value)} required maxLength={passwordMaxLength} autoComplete="current-password" placeholder="输入密码"/>
                  <button type="button" id="login-password-toggle" aria-controls="admin-password" aria-label={passwordVisible ? '隐藏密码' : '显示密码'} aria-pressed={passwordVisible} onClick={() => setPasswordVisible(value => !value)}>{passwordVisible ? '隐藏' : '显示'}</button>
                </div>
              </div>
              <button className="btn btn-primary login-submit auth-submit" type="submit" disabled={busy} aria-busy={busy ? true : undefined}><span className="auth-submit-text">{busy ? '正在验证…' : '登录控制台'}</span><span aria-hidden="true">→</span></button>
              <span id="login-progress" className="sr-only" role="status" aria-live="polite">{progress}</span>
            </form>
            <div className="login-panel-foot"><LockIcon/><span>仅限授权管理员访问</span></div>
          </section>
        </div>
        <footer className="login-footer">Hysteria <span>／</span> Network Console</footer>
      </main>
    </main>
  </>;
}
