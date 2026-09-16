import { hasExactKeys, postFormJson } from '../../shared/postForm';

export type LoginResponse =
  | {
      ok: true;
      redirect_to: '/admin?msg=login+success' | '/user/panel' | '/user/change-password';
    }
  | { ok: false; message: string };

type Credentials = { username: string; password: string };
export type LoginRealm = 'admin' | 'user';

const destinations = new Set([
  '/admin?msg=login+success',
  '/user/panel',
  '/user/change-password',
]);

function validateLoginResponse(value: unknown, status: number): LoginResponse {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid login response');
  const record = value as Record<string, unknown>;
  if (
    status === 200
    && record.ok === true
    && typeof record.redirect_to === 'string'
    && destinations.has(record.redirect_to)
    && hasExactKeys(record, ['ok', 'redirect_to'])
  ) {
    return record as LoginResponse;
  }
  if (
    (status === 200 || status === 429)
    && record.ok === false
    && typeof record.message === 'string'
    && hasExactKeys(record, ['ok', 'message'])
  ) {
    return { ok: false, message: record.message };
  }
  throw new Error('Invalid login response');
}

export async function submitLogin(
  credentials: Credentials,
  signal: AbortSignal,
  realm: LoginRealm = 'admin',
): Promise<LoginResponse> {
  const fields = realm === 'user'
    ? {
        user_username: credentials.username,
        user_password: credentials.password,
      }
    : {
        admin_username: credentials.username,
        admin_password: credentials.password,
      };
  const { value, status } = await postFormJson('/api/v1/login', {
    ...fields,
  }, signal);
  return validateLoginResponse(value, status);
}
