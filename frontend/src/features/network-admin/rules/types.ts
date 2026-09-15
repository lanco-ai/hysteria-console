export type AdminRules = { rules: string[]; revision: string };
export type RulesMutation = { ok: true; revision: string } | { ok: false; error: string; code?: string };

