import { hasExactKeys, postFormJson } from '../../shared/postForm';

export type PasswordRealm = 'admin' | 'user';
export type PasswordDraft = { current: string; new: string; confirm: string };

type AdminValidationCode = 'password_wrong' | 'password_short' | 'password_long' | 'password_mismatch';
type UserValidationCode = 'current password wrong' | 'new password short' | 'new password long' | 'new password mismatch' | 'new password same';
export type PasswordValidationCode = AdminValidationCode | UserValidationCode;
export type PasswordAccessCode = 'login_required' | 'forbidden' | 'disabled' | 'expired';

export type PasswordResponse =
  | { kind: 'success'; redirectTo: '/admin/settings?msg=password+changed' | '/user/panel' }
  | { kind: 'invalid'; code: PasswordValidationCode }
  | { kind: 'access'; code: PasswordAccessCode };

const paths = {
  admin: '/api/v1/admin/change-password',
  user: '/api/v1/user/change-password',
} as const;

const destinations = {
  admin: '/admin/settings?msg=password+changed',
  user: '/user/panel',
} as const;

const validationCodes = {
  admin: new Set<PasswordValidationCode>(['password_wrong', 'password_short', 'password_long', 'password_mismatch']),
  user: new Set<PasswordValidationCode>(['current password wrong', 'new password short', 'new password long', 'new password mismatch', 'new password same']),
};

const accessCodes = new Set<PasswordAccessCode>(['login_required', 'forbidden', 'disabled', 'expired']);

export async function submitPasswordChange(
  realm: PasswordRealm,
  draft: PasswordDraft,
  signal: AbortSignal,
): Promise<PasswordResponse> {
  const { value, status } = await postFormJson(paths[realm], draft, signal);
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid password response');
  const record = value as Record<string, unknown>;
  if (
    status === 200
    && record.ok === true
    && record.redirect_to === destinations[realm]
    && hasExactKeys(record, ['ok', 'redirect_to'])
  ) {
    return { kind: 'success', redirectTo: destinations[realm] };
  }
  if (
    status === 200
    && record.ok === false
    && typeof record.code === 'string'
    && validationCodes[realm].has(record.code as PasswordValidationCode)
    && hasExactKeys(record, ['ok', 'code'])
  ) {
    return { kind: 'invalid', code: record.code as PasswordValidationCode };
  }
  if (
    ((status === 401 && record.error === 'login_required')
      || (realm === 'user'
        && status === 403
        && typeof record.error === 'string'
        && record.error !== 'login_required'
        && accessCodes.has(record.error as PasswordAccessCode)))
    && hasExactKeys(record, ['error'])
  ) {
    return { kind: 'access', code: record.error as PasswordAccessCode };
  }
  throw new Error('Invalid password response');
}
