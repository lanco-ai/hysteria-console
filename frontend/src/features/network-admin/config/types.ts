export type AdminTemplate = { config: Record<string, unknown>; revision: string };
export type TemplateMutation = { ok: true; revision: string } | { ok: false; error: string; code?: string };

