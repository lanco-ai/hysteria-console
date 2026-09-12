import { hasExactKeys, postFormJson } from '../../shared/postForm';

export type LogoutRealm = 'admin' | 'user';
export type LogoutResponse = { ok: true; redirect_to: '/login' };

const paths = {
  admin: '/api/v1/logout',
  user: '/api/v1/user/logout',
} as const;

export async function submitLogout(realm: LogoutRealm, signal: AbortSignal): Promise<LogoutResponse> {
  const { value, status } = await postFormJson(paths[realm], {}, signal);
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid logout response');
  const record = value as Record<string, unknown>;
  if (
    status !== 200
    || record.ok !== true
    || record.redirect_to !== '/login'
    || !hasExactKeys(record, ['ok', 'redirect_to'])
  ) {
    throw new Error('Invalid logout response');
  }
  return record as LogoutResponse;
}
