import { hasExactKeys } from '../../shared/postForm';

export type PasswordPageData = {
  username: string;
  password_min_length: number;
  password_max_length: number;
};

export function validatePasswordPage(value: unknown): PasswordPageData {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid password page');
  const record = value as Record<string, unknown>;
  if (
    !hasExactKeys(record, ['username', 'password_min_length', 'password_max_length'])
    || typeof record.username !== 'string'
    || !Number.isInteger(record.password_min_length)
    || !Number.isInteger(record.password_max_length)
    || (record.password_min_length as number) <= 0
    || (record.password_max_length as number) < (record.password_min_length as number)
  ) {
    throw new Error('Invalid password page');
  }
  return record as PasswordPageData;
}
