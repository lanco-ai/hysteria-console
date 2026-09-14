import { useCallback, useRef, useState, type FormEvent } from 'react';
import { useFormAction } from '../../shared/useFormAction';
import {
  submitPasswordChange,
  type PasswordDraft,
  type PasswordRealm,
  type PasswordValidationCode,
} from './passwordRequest';

const TRANSPORT_ERROR = '修改结果未确认，请检查网络或重新登录后再试。';

const messages: Record<PasswordValidationCode, string> = {
  password_wrong: '当前密码不正确',
  password_short: '新密码至少 8 位',
  password_long: '密码不能超过 {max} 位',
  password_mismatch: '两次输入的新密码不一致',
  'current password wrong': '当前密码不正确',
  'new password short': '新密码至少需要 8 位',
  'new password long': '新密码不能超过 {max} 位',
  'new password mismatch': '两次输入的新密码不一致',
  'new password same': '新密码不能与当前密码相同',
};

function accessDestination(realm: PasswordRealm, code: string): '/login' | '/user/panel' {
  if (realm === 'user' && (code === 'disabled' || code === 'expired')) return '/user/panel';
  return '/login';
}

export function usePasswordChange(
  realm: PasswordRealm,
  passwordMaxLength: number,
  initialFeedback = '',
  initialFeedbackKind: 'err' | 'flash' = 'err',
) {
  const [draft, setDraft] = useState<PasswordDraft>({ current: '', new: '', confirm: '' });
  const [feedback, setFeedback] = useState(initialFeedback);
  const [feedbackKind, setFeedbackKind] = useState<'err' | 'flash'>(initialFeedbackKind);
  const [progress, setProgress] = useState('');
  const draftRef = useRef(draft);
  const { busy, run } = useFormAction(() => setProgress(''));

  const change = useCallback((field: keyof PasswordDraft, value: string) => {
    const next = { ...draftRef.current, [field]: value };
    draftRef.current = next;
    setDraft(next);
  }, []);

  const onSubmit = useCallback((event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const submitted = { ...draftRef.current };
    void run(signal => submitPasswordChange(realm, submitted, signal), {
      onStart: () => {
        setFeedback('');
        setFeedbackKind('err');
        setProgress('正在更新密码');
      },
      onResult: result => {
        setProgress('');
        if (result.kind === 'success') {
          window.location.assign(result.redirectTo);
          return;
        }
        if (result.kind === 'access') {
          window.location.assign(accessDestination(realm, result.code));
          return;
        }
        setFeedback(messages[result.code].replace('{max}', String(passwordMaxLength)));
        setFeedbackKind('err');
        (Object.keys(submitted) as (keyof PasswordDraft)[]).forEach(field => {
          if (draftRef.current[field] === submitted[field]) change(field, '');
        });
      },
      onError: () => {
        setProgress('');
        setFeedback(TRANSPORT_ERROR);
        setFeedbackKind('err');
      },
    });
  }, [change, passwordMaxLength, realm, run]);

  return { busy, draft, feedback, feedbackKind, progress, change, onSubmit };
}
