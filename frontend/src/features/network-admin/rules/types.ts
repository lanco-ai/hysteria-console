export type AdminRulePack = { key: string; label: string; description: string };
export type AdminRules = { rules: string[]; revision: string; packs: AdminRulePack[]; users: string[] };
export type RulesMutation = { ok: true; revision: string } | { ok: false; error: string; code?: string };
export type RulesOperationAction = 'add' | 'delete' | 'pack';
export type RulesOperationMutation =
  | { ok: true; action: RulesOperationAction; revision?: string; user?: string }
  | { ok: false; action: RulesOperationAction; error: string; code?: string };
