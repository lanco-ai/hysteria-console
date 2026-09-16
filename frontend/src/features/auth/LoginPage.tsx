import { useRef, useState, type FormEvent } from 'react';
import { useFormAction } from '../../shared/useFormAction';
import { useInitialFragmentNavigation } from '../../shared/useInitialFragmentNavigation';
import { submitLogin, type LoginRealm } from './loginRequest';

const fragmentTargets = new Set(['main-content']);
const TRANSPORT_ERROR = '登录结果未确认，请检查网络后重试。';

function LockIcon() {
  return <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>;
}

export function LoginPage({ passwordMaxLength, realm = 'admin' }: { passwordMaxLength: number; realm?: LoginRealm }) {
  const isUser = realm === 'user';
  const fieldPrefix = isUser ? 'user' : 'admin';
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [progress, setProgress] = useState('');
  const [feedback, setFeedback] = useState('');
  const usernameRef = useRef(username);
  const passwordRef = useRef(password);
  const { busy, run } = useFormAction(() => {
    setProgress('');
    setPasswordVisible(false);
  });

  useInitialFragmentNavigation(fragmentTargets);

  const changeUsername = (value: string) => {
    usernameRef.current = value;
    setUsername(value);
  };
  const changePassword = (value: string) => {
    passwordRef.current = value;
    setPassword(value);
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const submitted = { username: usernameRef.current, password: passwordRef.current };
    void run(signal => submitLogin(submitted, signal, realm), {
      onStart: () => {
        setFeedback('');
        setProgress('正在验证登录信息');
      },
      onResult: result => {
        setProgress('');
        if (result.ok) {
          window.location.assign(result.redirect_to);
          return;
        }
        setFeedback(result.message);
        setPasswordVisible(false);
        if (usernameRef.current === submitted.username) changeUsername(submitted.username.trim());
        if (passwordRef.current === submitted.password) changePassword('');
      },
      onError: () => {
        setProgress('');
        setFeedback(TRANSPORT_ERROR);
      },
    });
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
            <div className="login-eyebrow"><span/> {isUser ? 'USER · PANEL' : 'NETWORK CONSOLE'}</div>
            <h1 id="login-story-title">{isUser ? <>查看用量，<br/><span>管理订阅。</span></> : <>连接网络，<br/><span>掌控全局。</span></>}</h1>
            <p className="login-description">{isUser ? <>实时查看流量与订阅状态。<br/>安全、清晰地管理你的连接。</> : <>让每一次连接，清晰可见。<br/>在一个控制台中，管理你的网络。</>}</p>
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
            <div className="login-story-foot"><span>HYSTERIA</span><span>{isUser ? '查看 · 管理 · 订阅' : '连接 · 洞察 · 管理'}</span></div>
          </section>
          <section className="login-panel" aria-labelledby="login-title">
            <div className="login-panel-kicker"><LockIcon/><span>{isUser ? '用户面板访问' : '管理员访问'}</span></div>
            <h2 id="login-title">{isUser ? '用户登录' : '登录控制台'}</h2>
            <p className="login-subtitle">{isUser ? '登录后查看用量和订阅信息。' : '使用管理员账号登录。'}</p>
            <form method="post" action={isUser ? '/user/login' : '/login'} className="login-form" id={isUser ? 'form-user' : 'form-admin'} onSubmit={onSubmit}>
              <div className="login-feedback">{feedback ? <div className="err" role="alert" aria-live="assertive" aria-atomic="true">{feedback}</div> : null}</div>
              <div className="field">
                <label className="label" htmlFor={`${fieldPrefix}-username`}>{isUser ? '用户名' : '管理员账号'}</label>
                <input className="input" id={`${fieldPrefix}-username`} name={isUser ? 'user_username' : 'admin_username'} value={username} onChange={event => changeUsername(event.target.value)} required autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder={isUser ? '输入用户名' : '输入管理员账号'}/>
              </div>
              <div className="field">
                <label className="label" htmlFor={`${fieldPrefix}-password`}>{isUser ? '面板密码' : '密码'}</label>
                <div className="login-password">
                  <input className="input" id={`${fieldPrefix}-password`} name={isUser ? 'user_password' : 'admin_password'} type={passwordVisible ? 'text' : 'password'} value={password} onChange={event => changePassword(event.target.value)} required maxLength={passwordMaxLength} autoComplete="current-password" placeholder={isUser ? '输入面板密码' : '输入密码'}/>
                  <button type="button" id="login-password-toggle" aria-controls={`${fieldPrefix}-password`} aria-label={passwordVisible ? '隐藏密码' : '显示密码'} aria-pressed={passwordVisible} onClick={() => setPasswordVisible(value => !value)}>{passwordVisible ? '隐藏' : '显示'}</button>
                </div>
              </div>
              <button className="btn btn-primary login-submit auth-submit" type="submit" disabled={busy} aria-busy={busy ? true : undefined}><span className="auth-submit-text">{busy ? '正在验证…' : (isUser ? '登录用户面板' : '登录控制台')}</span><span aria-hidden="true">→</span></button>
              <span id="login-progress" className="sr-only" role="status" aria-live="polite">{progress}</span>
            </form>
            <div className="login-panel-foot"><LockIcon/><span>{isUser ? '面板密码由管理员设置' : '仅限授权管理员访问'}</span></div>
          </section>
        </div>
        <footer className="login-footer">Hysteria <span>／</span> Network Console</footer>
      </main>
    </main>
  </>;
}
