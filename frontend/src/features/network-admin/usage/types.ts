export type UsageStats = {
  current_hour_bytes: number;
  today_bytes: number;
  yesterday_bytes: number;
  last_7d_bytes: number;
  cycle_bytes: number;
  cycle_day: number;
  cycle_total_days: number;
  online: number;
};

export type UsageHourlyPoint = { hour: string; bytes: number };
export type UsageHeatmapRow = { date: string; hours: number[] };
export type UsageTopUser = { uid: string; last_24h_bytes: number; spark: number[] };
export type UsageSummary = { ts: string; stats: UsageStats };
export type Usage = UsageSummary & {
  hourly_totals: UsageHourlyPoint[];
  heatmap: UsageHeatmapRow[];
  top_n: UsageTopUser[];
};

export type UsageHistoryUser = { uid: string; values: number[] };
export type UsageHistory = {
  ts: string;
  retention_days: number;
  dates: string[];
  users: UsageHistoryUser[];
  totals: number[];
};

