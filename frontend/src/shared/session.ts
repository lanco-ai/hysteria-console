import { useCallback, useEffect, useRef, useState } from 'react';

export type SessionRole = 'admin' | 'user';
export type SessionStatus = 'loading' | 'anonymous' | 'authenticated' | 'unavailable';

type SessionState = {
  status: SessionStatus;
  role?: SessionRole;
};

function validRole(value: unknown): value is SessionRole {
  return value === 'admin' || value === 'user';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

async function readSession(signal: AbortSignal): Promise<SessionState> {
  const response = await fetch('/api/v1/session', {
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { Accept: 'application/json' },
    signal,
  });
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { status: 'unavailable' };
  }
  if (response.status === 401 && isRecord(payload) && payload.error === 'login_required') {
    return { status: 'anonymous' };
  }
  if (!response.ok) return { status: 'unavailable' };
  if (!isRecord(payload) || !validRole(payload.role)) return { status: 'unavailable' };
  return { status: 'authenticated', role: payload.role };
}

export function useSession(requiredRole?: 'admin' | 'user', enabled = true): {
  status: 'loading' | 'anonymous' | 'authenticated' | 'unavailable';
  role?: 'admin' | 'user';
  refresh: () => Promise<void>;
} {
  const [session, setSession] = useState<SessionState>({ status: enabled ? 'loading' : 'authenticated' });
  const generation = useRef(0);
  const activeController = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    if (!enabled) {
      setSession({ status: 'authenticated' });
      return;
    }
    const request = generation.current + 1;
    generation.current = request;
    activeController.current?.abort();
    const controller = new AbortController();
    activeController.current = controller;
    setSession({ status: 'loading' });
    try {
      const next = await readSession(controller.signal);
      if (controller.signal.aborted || generation.current !== request) return;
      if (next.status === 'authenticated' && requiredRole && next.role !== requiredRole) {
        setSession({ status: 'anonymous' });
        return;
      }
      setSession(next);
    } catch {
      if (controller.signal.aborted || generation.current !== request) return;
      setSession({ status: 'unavailable' });
    }
  }, [enabled, requiredRole]);

  useEffect(() => {
    if (!enabled) {
      generation.current += 1;
      activeController.current?.abort();
      activeController.current = null;
      setSession({ status: 'authenticated' });
      return;
    }
    void refresh();
    return () => {
      generation.current += 1;
      activeController.current?.abort();
      activeController.current = null;
    };
  }, [enabled, refresh]);

  return { ...session, refresh };
}
