import { useCallback, useState, type FormEvent } from 'react';
import { useFormAction } from '../../shared/useFormAction';
import { submitLogout, type LogoutRealm } from './logoutRequest';

const LOGOUT_ERROR = '退出结果未确认，请检查网络后重试。';

export function useLogout(realm: LogoutRealm) {
  const [feedback, setFeedback] = useState('');
  const [failureCount, setFailureCount] = useState(0);
  const { busy, run } = useFormAction();

  const onSubmit = useCallback((event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void run(
      signal => submitLogout(realm, signal),
      {
        onStart: () => setFeedback(''),
        onResult: result => window.location.assign(result.redirect_to),
        onError: () => {
          setFeedback(LOGOUT_ERROR);
          setFailureCount(count => count + 1);
        },
      },
    );
  }, [realm, run]);

  return { busy, feedback, failureCount, onSubmit };
}
