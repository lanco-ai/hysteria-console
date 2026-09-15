export type SubscriptionProfile = { key: string; label: string; description: string; url: string; qr_path: string };
export type UserLandingNode = { id: string; name: string; exit_ip: string; isp: string; region: string; enabled: boolean; selected: boolean; health_status: string };
export type UserPanel = {
  ts: string; username: string; revision: string; used_bytes: number; total_bytes: number; remain_bytes: number;
  tx_bytes: number; rx_bytes: number; online: number; max_devices: number; percent: number;
  cycle_reset_date: string; cycle_days_left: number; cycle_length_days: number; disabled: boolean; expired: boolean;
  expiry_label: string; can_change_password: boolean; can_select_egress: boolean;
  subscription_profiles: SubscriptionProfile[]; landing_nodes: UserLandingNode[];
};
