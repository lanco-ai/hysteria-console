export type LandingHealth = {
  status?: string;
  observed_ip?: string;
  checked_at?: string;
  error_code?: string;
};

export type LandingNode = {
  id: string;
  name: string;
  exit_ip: string;
  isp: string;
  region: string;
  enabled: boolean;
  health?: LandingHealth;
};

export type LandingAccess = { user: string; revision: string; allowed_ids: string[] };
export type AdminLanding = { ts: string; revision: string; nodes: LandingNode[]; users: LandingAccess[] };
