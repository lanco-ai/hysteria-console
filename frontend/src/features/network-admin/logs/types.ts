export type LogRow = {
  time: string;
  actor: string;
  ip: string;
  action: string;
  target: string;
  month: string;
  detail: string;
};

export type LogsPayload = {
  limit: number;
  rows: LogRow[];
};

export type SessionPayload =
  | { role: 'admin' }
  | { role: 'user'; username: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function validateSession(value: unknown): SessionPayload {
  if (!isRecord(value)) throw new Error('invalid session response');
  if (value.role === 'admin') return { role: 'admin' };
  if (value.role === 'user' && typeof value.username === 'string') {
    return { role: 'user', username: value.username };
  }
  throw new Error('invalid session response');
}

export function validateLogs(value: unknown): LogsPayload {
  if (!isRecord(value) || !Number.isInteger(value.limit) || !Array.isArray(value.rows)) {
    throw new Error('invalid logs response');
  }
  const fields: (keyof LogRow)[] = ['time', 'actor', 'ip', 'action', 'target', 'month', 'detail'];
  const rows = value.rows.map(row => {
    if (!isRecord(row) || !fields.every(field => typeof row[field] === 'string')) {
      throw new Error('invalid log row');
    }
    return Object.fromEntries(fields.map(field => [field, row[field]])) as LogRow;
  });
  return { limit: value.limit as number, rows };
}
