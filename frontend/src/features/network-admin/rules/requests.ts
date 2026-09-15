import { postFormJson } from '../../../shared/postForm';
import { ResourceError } from '../../../shared/readResource';
import type { AdminRules, RulesMutation } from './types';

export const RULES_ENDPOINT = '/api/v1/admin/rules';
function invalid(): never { throw new Error('响应数据格式无效'); }
function record(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid(); return value as Record<string, unknown>; }
function revision(value: unknown): string { if (typeof value !== 'string' || !/^[0-9a-f]{64}$/.test(value)) return invalid(); return value; }
export function parseRules(value: unknown): AdminRules { const v = record(value); if (!Array.isArray(v.rules) || v.rules.some(rule => typeof rule !== 'string')) return invalid(); return { rules: v.rules as string[], revision: revision(v.revision) }; }
export function parseMutation(value: unknown): RulesMutation { const v = record(value); if (v.ok === true && typeof v.revision === 'string') return { ok: true, revision: revision(v.revision) }; if (v.ok === false && typeof v.error === 'string') { const result: { ok: false; error: string; code?: string } = { ok: false, error: v.error }; if (typeof v.code === 'string') result.code = v.code; return result; } return invalid(); }
export async function saveRules(fields: Record<string, string>, signal: AbortSignal): Promise<RulesMutation> { const { value, status } = await postFormJson('/api/v1/admin/rules/save', fields, signal); if (status !== 200 && status !== 409 && status !== 422) throw new ResourceError(`HTTP ${status}`, status); return parseMutation(value); }
