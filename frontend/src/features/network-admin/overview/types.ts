export type Counters = { user: string; tx: number; rx: number; used: number; total: number; percent: number; online: number; revision: string; disabled: boolean };
export type OverviewUser = Counters & {
  max_devices: number; base_quota_gb: number; quota_extra_gb: number;
  metered: boolean; tuic_enabled: boolean; expires_at: string; expired: boolean;
  expiry_label: string; note: string; landing_isp: string; landing_region: string;
  landing_note: string; landing_ip: string; panel_url: string; subscription_url: string;
  spark: [string, number][] | null;
};
export type Cycle = { key: string; total_used: number; range: string; settlement_day: number; length_days: number; length_min: number; length_max: number };
export type Overview = { cycle: Cycle; users: OverviewUser[]; landing_options: { id: string; name: string }[] };
export type Poll = { ts: string; total_used: number; users: Counters[] };
export type Action = 'create' | 'update' | 'cycle' | 'reset-usage' | 'refresh-usage' | 'reset-usage-all' | 'pause-user' | 'toggle-user' | 'rotate-token' | 'delete';
export type MutationResult = { kind: 'success'; code: string } | { kind: 'invalid'; code: string; field: string } | { kind: 'conflict' | 'missing' | 'auth' | 'unknown'; code: string };
export type Mutate = (action: Action, fields: Record<string, string>, confirmed?: () => void) => Promise<MutationResult | undefined>;
