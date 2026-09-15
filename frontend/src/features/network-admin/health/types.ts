export type HealthStatus = { title: string; ok: boolean; label: string };
export type Health = { ts: string; kpis: HealthStatus[]; services: HealthStatus[] };

