export type IncidentStats = {
  current_hour_bytes: number;
  today_bytes: number;
  yesterday_bytes: number;
  last_7d_bytes: number;
  cycle_bytes: number;
  cycle_day: number;
  cycle_total_days: number;
  online: number;
};
export type IncidentPeakUser = { user: string; bytes: number };
export type IncidentPeak = { hour: string; bytes: number; users: IncidentPeakUser[] };
export type IncidentUser = {
  user: string; revision: string; last_24h_bytes: number; cycle_used_bytes: number;
  quota_bytes: number; quota_percent: number; online: number; disabled: boolean;
  expired: boolean; expiry_label: string; note: string;
};
export type IncidentRadarRow = { key: string; label: string; status: string; ok: boolean; bytes: number; share: number; active_users: number; online: number | null; profile: string; note: string };
export type IncidentRadar = { window_hours: number; total_bytes: number; recommendation: string; reason: string; rows: IncidentRadarRow[] };
export type IncidentAlert = { kind: string; label: string; user: string; key: string };
export type Incidents = { ts: string; stats: IncidentStats; peak_hour: IncidentPeak; users: IncidentUser[]; line_radar: IncidentRadar; cost_calibration: Record<string, unknown>; alerts: IncidentAlert[] };

