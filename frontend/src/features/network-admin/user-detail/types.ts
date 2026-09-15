export type UserDetailAlert = { ts: string; kind: string; details: string };
export type UserDetailHourly = { hour: string; bytes: number };
export type UserDetailHeatmap = { date: string; hours: number[] };

export type UserDetail = {
  ts: string;
  uid: string;
  metered: boolean;
  disabled: boolean;
  expired: boolean;
  expires_at: string | null;
  expiry_label: string;
  note: string;
  online: number;
  max_devices: number;
  cycle_used_bytes: number;
  cycle_quota_bytes: number;
  quota_extra_bytes: number;
  current_hour_bytes: number;
  today_bytes: number;
  recent_alerts: UserDetailAlert[];
  hourly_bars: UserDetailHourly[];
  heatmap: UserDetailHeatmap[];
};
