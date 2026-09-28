import { useCallback, useEffect, useState, type ReactElement } from 'react';

type DueReminder = { id: string; title: string; plan_date: string; reminder_at: string };

export function PlanReminderCenter(): ReactElement | null {
  const [items, setItems] = useState<DueReminder[]>([]);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await fetch('/api/plans/reminders', {
        credentials: 'same-origin', cache: 'no-store', ...(signal ? { signal } : {}),
      });
      if (response.status === 401 || response.status === 403) { setItems([]); return; }
      if (!response.ok) throw new Error('reminders unavailable');
      const payload = await response.json() as { items?: unknown };
      setItems(Array.isArray(payload.items) ? payload.items.filter((item): item is DueReminder => !!item && typeof item === 'object'
        && typeof (item as DueReminder).id === 'string' && typeof (item as DueReminder).title === 'string'
        && typeof (item as DueReminder).plan_date === 'string' && typeof (item as DueReminder).reminder_at === 'string') : []);
      setError('');
    } catch {
      if (!signal?.aborted) setError('提醒暂时无法读取');
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const check = () => { if (!document.hidden) void refresh(controller.signal); };
    check();
    const timer = window.setInterval(check, 60_000);
    document.addEventListener('visibilitychange', check);
    return () => { controller.abort(); window.clearInterval(timer); document.removeEventListener('visibilitychange', check); };
  }, [refresh]);

  const act = async (item: DueReminder, action: 'dismiss' | 'snooze' | 'complete') => {
    setBusy(item.id); setError('');
    try {
      const response = await fetch(`/api/plans/reminders/${encodeURIComponent(item.id)}`, {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      if (!response.ok) throw new Error('reminder action failed');
      await refresh();
    } catch { setError('提醒操作未保存，请重试'); }
    finally { setBusy(''); }
  };

  if (!items.length && !error) return null;
  return <aside className="plans-global-reminders" aria-label="今日计划提醒">
    <header><strong>计划提醒</strong><span>{items.length}</span></header>
    {error ? <p role="status">{error}</p> : null}
    {items.map(item => <article key={item.id}>
      <div><strong>{item.title}</strong><small>{item.plan_date} · {new Date(item.reminder_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</small></div>
      <div className="plans-global-reminder-actions"><button className="btn btn-primary btn-sm" type="button" disabled={!!busy} onClick={() => void act(item, 'complete')}>完成</button><button className="btn btn-ghost btn-sm" type="button" disabled={!!busy} onClick={() => void act(item, 'snooze')}>稍后</button><button className="btn btn-ghost btn-sm" type="button" aria-label={`关闭 ${item.title} 提醒`} disabled={!!busy} onClick={() => void act(item, 'dismiss')}>关闭</button></div>
    </article>)}
  </aside>;
}
