import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { useFormAction } from '../../shared/useFormAction';
import { submitLogin, type LoginRealm } from './loginRequest';

const TRANSPORT_ERROR = '登录结果未确认，请检查网络后重试。';
const GENERIC_LOGIN_FAILURE = '登录失败，请检查登录信息后重试。';
const USER_LOGIN_FAILURE = '请使用管理员账号登录控制台。';
const ADMIN_LOGIN_MESSAGES = new Set([
  '用户名或密码错误',
  '请输入用户名和密码',
  '登录尝试过于频繁，请 1 小时后再试',
  '账号已停用，请联系管理员',
  '账号已到期，请联系管理员续费',
]);

export type LoginModalProps = {
  open: boolean;
  realm: LoginRealm;
  passwordMaxLength: number;
  returnTo?: string;
  onAuthenticated: (returnTo?: string) => Promise<void> | void;
  onClose?: () => void;
};

export function resolveSameOriginReturnTo(value?: string): string | undefined {
  if (!value) return undefined;
  try {
    const destination = new URL(value, window.location.origin);
    if (destination.origin !== window.location.origin || !destination.pathname.startsWith('/')) return undefined;
    return `${destination.pathname}${destination.search}${destination.hash}`;
  } catch {
    return undefined;
  }
}

function loginFeedback(message: string, realm: LoginRealm): string {
  if (realm === 'user') return USER_LOGIN_FAILURE;
  return ADMIN_LOGIN_MESSAGES.has(message) ? message : GENERIC_LOGIN_FAILURE;
}

export function LoginModal({ open, realm, passwordMaxLength, returnTo, onAuthenticated, onClose }: LoginModalProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [feedback, setFeedback] = useState('');
  const [progress, setProgress] = useState('');
  const usernameInput = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const { busy, run } = useFormAction(() => {
    setProgress('');
    setPassword('');
  });

  useEffect(() => {
    if (open) {
      const frame = window.requestAnimationFrame(() => usernameInput.current?.focus());
      return () => window.cancelAnimationFrame(frame);
    }
    setPassword('');
    setFeedback('');
    setProgress('');
  }, [open]);

  if (!open) return null;

  const close = () => {
    if (!busy) onClose?.();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== 'Tab') return;
    const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    ) || []);
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void run(signal => submitLogin({ username, password }, signal, realm), {
      onStart: () => {
        setFeedback('');
        setProgress('正在验证登录信息');
      },
      onResult: result => {
        setProgress('');
        if (!result.ok) {
          setFeedback(loginFeedback(result.message, realm));
          setPassword('');
          return;
        }
        // App owns navigation. Validate the server-provided return target before it refreshes session state.
        const safeReturnTo = resolveSameOriginReturnTo(returnTo);
        void Promise.resolve(onAuthenticated(safeReturnTo)).catch(() => setFeedback(TRANSPORT_ERROR));
      },
      onError: () => {
        setProgress('');
        setFeedback(TRANSPORT_ERROR);
      },
    });
  };

  const title = realm === 'user' ? '登录用户面板' : '登录控制台';
  const usernameLabel = realm === 'user' ? '用户名' : '管理员账号';
  return <div className="login-modal-layer" onKeyDown={onKeyDown}>
    <button className="login-modal-backdrop" type="button" tabIndex={-1} aria-label="关闭登录窗口" onClick={close}/>
    <div className="login-modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="login-modal-title">
      <header className="login-modal-header">
        <h2 id="login-modal-title">{title}</h2>
        {onClose ? <button type="button" className="dialog-close" aria-label="关闭" disabled={busy} onClick={close}>×</button> : null}
      </header>
      <form className="login-form" onSubmit={onSubmit}>
        {feedback ? <div className="err" role="alert" aria-live="assertive" aria-atomic="true">{feedback}</div> : null}
        <div className="field">
          <label className="label" htmlFor="login-modal-username">{usernameLabel}</label>
          <input ref={usernameInput} className="input" id="login-modal-username" value={username} onChange={event => setUsername(event.target.value)} required autoComplete="username" autoCapitalize="none" spellCheck={false}/>
        </div>
        <div className="field">
          <label className="label" htmlFor="login-modal-password">密码</label>
          <input className="input" id="login-modal-password" type="password" value={password} onChange={event => setPassword(event.target.value)} required maxLength={passwordMaxLength} autoComplete="current-password"/>
        </div>
        <button className="btn btn-primary auth-submit" type="submit" disabled={busy} aria-busy={busy ? true : undefined}>{busy ? '正在验证…' : '登录'}</button>
        <span className="sr-only" role="status" aria-live="polite">{progress}</span>
      </form>
    </div>
  </div>;
}
