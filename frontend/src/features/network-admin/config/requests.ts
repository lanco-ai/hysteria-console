import { postFormJson } from '../../../shared/postForm';
import { ResourceError } from '../../../shared/readResource';
import type { AdminTemplate, TemplateMutation } from './types';

export const CONFIG_ENDPOINT = '/api/v1/admin/config';
function invalid(): never { throw new Error('响应数据格式无效'); }
function record(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid(); return value as Record<string, unknown>; }
function revision(value: unknown): string { if (typeof value !== 'string' || !/^[0-9a-f]{64}$/.test(value)) return invalid(); return value; }
export function parseConfig(value: unknown): AdminTemplate { const v = record(value); return { config: record(v.config), revision: revision(v.revision) }; }
export function parseMutation(value: unknown): TemplateMutation { const v = record(value); if (v.ok === true && typeof v.revision === 'string') return { ok: true, revision: revision(v.revision) }; if (v.ok === false && typeof v.error === 'string') { const result: { ok: false; error: string; code?: string } = { ok: false, error: v.error }; if (typeof v.code === 'string') result.code = v.code; return result; } return invalid(); }
export async function saveConfig(fields: Record<string, string>, signal: AbortSignal): Promise<TemplateMutation> { const { value, status } = await postFormJson('/api/v1/admin/config/save', fields, signal); if (status !== 200 && status !== 409 && status !== 422) throw new ResourceError(`HTTP ${status}`, status); return parseMutation(value); }
