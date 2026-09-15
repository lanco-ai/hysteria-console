import { useCallback, useEffect, useRef, useState } from 'react';
import { ResourceError } from '../../../shared/readResource';
import { feedback } from './presentation';
import { parseOverview, parsePoll, parseReload, readJson, write } from './requests';
import type { Action, Mutate, MutationResult, Overview } from './types';

const UNKNOWN = '操作结果尚未确认，请刷新核对后再操作';
const RELOAD = '用户列表或配置已更改，请刷新加载；未保存草稿保留原版本，保存时可能冲突';

export function useOverview() {
  const [data, setData] = useState<Overview>();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [auth, setAuth] = useState(false);
  const [message, setMessage] = useState(() => feedback(new URLSearchParams(location.search).get('msg') || ''));
  const [readError, setReadError] = useState('');
  const [pollStatus, setPollStatus] = useState('自动更新 · 30 s');
  const [reloadStatus, setReloadStatus] = useState('');
  const state = useRef({ active: false, suspended: false, epoch: 0, pending: false, blocked: false, data: undefined as Overview | undefined, failures: 0, pollTimer: 0, watcherTimer: 0, read: undefined as AbortController | undefined, readKind: undefined as 'bootstrap' | 'poll' | undefined, post: undefined as AbortController | undefined, watcher: undefined as AbortController | undefined });
  const pollRef = useRef<() => Promise<void>>(async () => {});

  const usable = useCallback((epoch: number) => state.current.active && !state.current.suspended && state.current.epoch === epoch, []);
  const invalidate = useCallback(() => {
    const s = state.current;
    s.epoch++;
    clearTimeout(s.pollTimer); clearTimeout(s.watcherTimer);
    s.read?.abort(); s.watcher?.abort();
    s.read = undefined; s.readKind = undefined; s.watcher = undefined;
    if (s.active) setLoading(false);
  }, []);
  const lock = useCallback((value: boolean) => { state.current.blocked = value; setBlocked(value); }, []);
  const loseAuth = useCallback(() => {
    state.current.data = undefined;
    setData(undefined); setAuth(true); lock(true);
    setReadError('登录已失效，请重新登录管理员账号');
  }, [lock]);
  const schedule = useCallback(() => {
    const s = state.current;
    clearTimeout(s.pollTimer);
    if (!s.active || s.suspended || s.pending || s.blocked || !s.data || document.hidden) return;
    const delay = Math.min(240_000, 30_000 * 2 ** s.failures + (s.failures ? Math.random() * 4_000 : 0));
    s.pollTimer = window.setTimeout(() => { void pollRef.current(); }, delay);
  }, []);

  const bootstrap = useCallback(async (): Promise<boolean> => {
    const s = state.current;
    if (!s.active || s.suspended) return false;
    s.read?.abort();
    const controller = new AbortController(), epoch = s.epoch;
    s.read = controller; s.readKind = 'bootstrap';
    setLoading(true); setReadError('');
    const timeout = window.setTimeout(() => controller.abort(), 10_000);
    try {
      const next = await readJson('/api/v1/admin/overview-page', controller.signal, parseOverview);
      if (!usable(epoch) || s.read !== controller) return false;
      s.data = next; s.failures = 0;
      setData(next); setAuth(false); lock(false); setPollStatus('自动更新 · 30 s');
      return true;
    } catch (error) {
      if (!usable(epoch) || s.read !== controller) return false;
      if (error instanceof ResourceError && error.status === 401) loseAuth();
      else {
        lock(true);
        setReadError(s.data ? '已保留操作结果；数据尚未更新，请刷新核对后再操作' : '总览加载失败或数据格式无效，请重试');
      }
      return false;
    } finally {
      clearTimeout(timeout);
      if (usable(epoch) && s.read === controller) { s.read = undefined; s.readKind = undefined; setLoading(false); schedule(); }
    }
  }, [lock, loseAuth, schedule, usable]);

  const poll = useCallback(async () => {
    const s = state.current;
    clearTimeout(s.pollTimer);
    if (!s.active || s.suspended || s.pending || s.blocked || s.read || !s.data || document.hidden) return;
    const controller = new AbortController(), epoch = s.epoch;
    s.read = controller; s.readKind = 'poll'; setPollStatus('正在更新…');
    const timeout = window.setTimeout(() => controller.abort(), 10_000);
    try {
      const next = await readJson('/api/v1/admin/overview', controller.signal, parsePoll);
      if (!usable(epoch) || s.read !== controller || !s.data) return;
      const rows = new Map(next.users.map(row => [row.user, row]));
      if (rows.size !== s.data.users.length || s.data.users.some(row => rows.get(row.user)?.revision !== row.revision)) {
        lock(true); setReadError(RELOAD); setPollStatus('需要刷新'); return;
      }
      const merged = { ...s.data, cycle: { ...s.data.cycle, total_used: next.total_used }, users: s.data.users.map(row => ({ ...row, ...rows.get(row.user)! })) };
      s.data = merged; s.failures = 0; setData(merged); setPollStatus('自动更新 · 30 s');
    } catch (error) {
      if (!usable(epoch) || s.read !== controller) return;
      if (error instanceof ResourceError && error.status === 401) loseAuth();
      else { s.failures = Math.min(3, s.failures + 1); setPollStatus('更新失败 · 点击重试'); }
    } finally {
      clearTimeout(timeout);
      if (usable(epoch) && s.read === controller) { s.read = undefined; s.readKind = undefined; schedule(); }
    }
  }, [lock, loseAuth, schedule, usable]);
  pollRef.current = poll;

  const watchReload = useCallback(() => {
    const s = state.current, epoch = s.epoch;
    clearTimeout(s.watcherTimer); s.watcher?.abort();
    const controller = new AbortController(); s.watcher = controller;
    const started = Date.now();
    const deadline = window.setTimeout(() => {
      controller.abort();
      if (usable(epoch) && s.watcher === controller) { clearTimeout(s.watcherTimer); setReloadStatus('配置已保存，服务重载仍待确认'); s.watcher = undefined; }
    }, 9_000);
    const check = async () => {
      try {
        const pending = await readJson('/api/v1/admin/reload-status', controller.signal, parseReload);
        if (!usable(epoch) || s.watcher !== controller) return;
        if (!pending) { clearTimeout(deadline); s.watcher = undefined; setReloadStatus(''); return; }
        setReloadStatus('配置已保存，正在等待服务重载…');
        if (Date.now() - started < 9_000) s.watcherTimer = window.setTimeout(() => { void check(); }, 850);
      } catch (error) {
        if (!usable(epoch) || s.watcher !== controller) return;
        clearTimeout(deadline); s.watcher = undefined;
        if (error instanceof ResourceError && error.status === 401) loseAuth();
        setReloadStatus('配置已保存，服务重载状态暂不可用');
      }
    };
    controller.signal.addEventListener('abort', () => clearTimeout(deadline), { once: true });
    s.watcherTimer = window.setTimeout(() => { void check(); }, 600);
  }, [loseAuth, usable]);

  const mutate: Mutate = useCallback(async (action: Action, fields: Record<string, string>, confirmed?: () => void) => {
    const s = state.current;
    // This synchronous gate protects every form and row before React rerenders.
    if (!s.active || s.suspended || s.pending || s.blocked || !s.data) return;
    s.pending = true; setBusy(true); invalidate(); setReloadStatus(''); setMessage('正在提交…');
    const epoch = s.epoch, controller = new AbortController(); s.post = controller;
    const timeout = window.setTimeout(() => controller.abort(), 10_000);
    let result: MutationResult;
    try { result = await write(action, fields, controller.signal); }
    catch { result = { kind: 'unknown', code: UNKNOWN }; }
    finally { clearTimeout(timeout); }
    if (!usable(epoch)) return;
    s.post = undefined;
    invalidate(); // Responses from both sides of the mutation boundary are obsolete.
    setMessage(feedback(result.code));
    if (result.kind === 'success') {
      confirmed?.();
      lock(true);
      await bootstrap();
      if (!usable(epoch + 1)) return result;
      watchReload();
    } else if (result.kind === 'auth') loseAuth();
    else if (result.kind !== 'invalid') { lock(true); setReadError(result.kind === 'unknown' ? UNKNOWN : result.code); }
    s.pending = false; setBusy(false); schedule();
    return result;
  }, [bootstrap, invalidate, lock, loseAuth, schedule, usable, watchReload]);

  const refresh = useCallback(() => {
    const s = state.current;
    if (s.pending || !s.active || s.suspended) return;
    invalidate();
    void bootstrap();
  }, [bootstrap, invalidate]);
  const retryPoll = useCallback(() => {
    if (state.current.blocked || !state.current.data) refresh();
    else void poll();
  }, [poll, refresh]);

  useEffect(() => {
    const s = state.current; s.active = true; s.suspended = false;
    void bootstrap();
    const visibility = () => {
      clearTimeout(s.pollTimer);
      if (document.hidden) { if (!s.pending && s.readKind === 'poll') { s.read?.abort(); s.read = undefined; s.readKind = undefined; } }
      else if (!s.pending && !s.blocked && !s.read) { if (s.data) void pollRef.current(); else void bootstrap(); }
    };
    const hide = () => {
      const writeUnconfirmed = Boolean(s.post);
      s.suspended = true; invalidate(); s.post?.abort(); s.post = undefined;
      if (s.pending) {
        s.pending = false; lock(true);
        if (writeUnconfirmed) setMessage(UNKNOWN);
      }
      setBusy(false); setLoading(false);
    };
    const show = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      s.suspended = false; s.pending = false; setBusy(false); lock(true); invalidate(); void bootstrap();
    };
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('pagehide', hide); window.addEventListener('pageshow', show);
    return () => {
      s.active = false; invalidate(); s.post?.abort(); s.pending = false;
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('pagehide', hide); window.removeEventListener('pageshow', show);
    };
  }, [bootstrap, invalidate, lock]);

  return { data, loading, busy, blocked, auth, message, readError, pollStatus, reloadStatus, mutate, refresh, retryPoll };
}
