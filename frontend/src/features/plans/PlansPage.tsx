import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactElement } from 'react';
import { CodexShell } from '../../shared/CodexShell';
import { loadPlanSnapshot, requestPlanAssistant, savePlanSnapshot, type PlanAssistantSuggestion, type PlanItem, type PlanQuadrant, type PlanSnapshot, type PlanStatus } from './plansApi';

const groups: Array<{ id: PlanQuadrant; title: string; hint: string }> = [
  { id: 'important_urgent', title: '重要且紧急', hint: '优先处理' },
  { id: 'important', title: '重要不紧急', hint: '安排时间，持续推进' },
  { id: 'urgent', title: '紧急不重要', hint: '简化处理或委托' },
  { id: 'later', title: '不紧急不重要', hint: '延后或重新评估' },
];
const statusLabels: Record<PlanStatus, string> = { todo: '待办', in_progress: '进行中', done: '已完成' };

function localDate(timezone: string, date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(date);
  const get = (type: string) => parts.find(part => part.type === type)?.value || '00';
  return `${get('year')}-${get('month')}-${get('day')}`;
}

function shiftDate(value: string, offset: number): string {
  const [year, month, day] = value.split('-').map(Number);
  const shifted = new Date(Date.UTC(year || 2000, (month || 1) - 1, (day || 1) + offset, 12));
  return `${shifted.getUTCFullYear()}-${String(shifted.getUTCMonth() + 1).padStart(2, '0')}-${String(shifted.getUTCDate()).padStart(2, '0')}`;
}

function localInputValue(value: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '';
  const pad = (number: number) => String(number).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function isoFromLocalInput(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toISOString() : null;
}

function dateHeading(value: string): string {
  const [year, month, day] = value.split('-').map(Number);
  const date = new Date(Date.UTC(year || 2000, (month || 1) - 1, day || 1, 12));
  return new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long', timeZone: 'UTC' }).format(date);
}

function timestamp(): string { return new Date().toISOString(); }
function newId(): string { return globalThis.crypto?.randomUUID?.() || `task-${Date.now()}-${Math.random().toString(16).slice(2)}`; }

type PlanSuggestionDraft = PlanAssistantSuggestion & { draftId: string; selected: boolean };

function reminderFromStart(date: string, startTime: string, offset: number): string | null {
  if (!startTime || offset <= 0) return null;
  const start = new Date(`${date}T${startTime}:00`);
  if (!Number.isFinite(start.getTime())) return null;
  return new Date(start.getTime() - offset * 60_000).toISOString();
}

export function PlansPage(): ReactElement {
  const timezone = useMemo(() => {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; }
  }, []);
  const [selectedDate, setSelectedDate] = useState(() => localDate(timezone));
  const [items, setItems] = useState<PlanItem[]>([]);
  const [revision, setRevision] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [title, setTitle] = useState('');
  const [quadrant, setQuadrant] = useState<PlanQuadrant>('important_urgent');
  const [estimate, setEstimate] = useState(30);
  const [reminderInput, setReminderInput] = useState('');
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [assistantRequest, setAssistantRequest] = useState('');
  const [assistantBusy, setAssistantBusy] = useState(false);
  const [assistantError, setAssistantError] = useState('');
  const [assistantSummary, setAssistantSummary] = useState('');
  const [assistantModel, setAssistantModel] = useState('');
  const [assistantSuggestions, setAssistantSuggestions] = useState<PlanSuggestionDraft[]>([]);
  const editVersion = useRef(0);
  const itemsRef = useRef<PlanItem[]>([]);

  const replaceItems = useCallback((next: PlanItem[]) => { itemsRef.current = next; setItems(next); }, []);

  const reload = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setError('');
    try {
      const snapshot = await loadPlanSnapshot(signal);
      if (signal?.aborted) return;
      replaceItems(snapshot.items); setRevision(snapshot.revision); setDirty(false);
    } catch (value) {
      if (!signal?.aborted) setError(value instanceof Error ? value.message : '计划读取失败');
    } finally { if (!signal?.aborted) setLoading(false); }
  }, [replaceItems]);

  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [reload]);

  const persist = async (next: PlanItem[]) => {
    if (saving) return false;
    const submittedEditVersion = editVersion.current;
    const baseItems = itemsRef.current;
    const baseById = new Map(baseItems.map(item => [item.id, item]));
    setSaving(true); setError(''); setFeedback('');
    try {
      const snapshot = await savePlanSnapshot({ items: next, revision });
      setRevision(snapshot.revision);
      if (editVersion.current === submittedEditVersion) {
        replaceItems(snapshot.items); setDirty(false); setFeedback('计划已保存');
      } else {
        const latestById = new Map(itemsRef.current.map(item => [item.id, item]));
        const mergedItems = snapshot.items.flatMap(savedItem => {
          const baseline = baseById.get(savedItem.id);
          const latest = latestById.get(savedItem.id);
          latestById.delete(savedItem.id);
          if (baseline && !latest) return [];
          if (baseline && latest && JSON.stringify(latest) !== JSON.stringify(baseline)) return [latest];
          return [savedItem];
        });
        mergedItems.push(...latestById.values());
        replaceItems(mergedItems);
        setDirty(true); setFeedback('保存完成；保存期间的新修改尚未同步，请再次保存');
      }
      return true;
    } catch (value) {
      setError(value instanceof Error ? value.message : '计划保存失败');
      return false;
    } finally { setSaving(false); }
  };

  const changeItems = (next: PlanItem[]) => { editVersion.current += 1; replaceItems(next); setDirty(true); setFeedback(''); };
  const selectedItems = items.filter(item => item.plan_date === selectedDate);
  const completed = selectedItems.filter(item => item.status === 'done').length;

  const addTask = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!title.trim()) return;
    const current = timestamp();
    const item: PlanItem = {
      id: newId(), title: title.trim(), notes: '', quadrant, plan_date: selectedDate, timezone,
      start_time: null, due_at: null, estimate_minutes: estimate,
      reminder_at: isoFromLocalInput(reminderInput), status: 'todo', created_at: current, updated_at: current,
    };
    changeItems([item, ...items]); setTitle(''); setReminderInput('');
  };

  const updateTask = (id: string, patch: Partial<PlanItem>) => {
    changeItems(items.map(item => item.id === id ? { ...item, ...patch, updated_at: timestamp() } : item));
  };

  const removeTask = (id: string) => {
    if (!window.confirm('删除这条计划？')) return;
    changeItems(items.filter(item => item.id !== id));
  };

  const generateSuggestions = async () => {
    if (!assistantRequest.trim() || assistantBusy) return;
    setAssistantBusy(true); setAssistantError(''); setAssistantSummary(''); setAssistantSuggestions([]);
    try {
      const result = await requestPlanAssistant({
        date: selectedDate,
        timezone,
        request: assistantRequest.trim(),
        existing_tasks: selectedItems.map(({ title, quadrant, status, estimate_minutes }) => ({ title, quadrant, status, estimate_minutes })),
      });
      setAssistantSummary(result.summary);
      setAssistantModel(`${result.service_name} · ${result.model}`);
      setAssistantSuggestions(result.suggestions.map(item => ({ ...item, draftId: newId(), selected: true })));
    } catch (value) {
      setAssistantError(value instanceof Error ? value.message : 'Gemini 建议暂时不可用。');
    } finally { setAssistantBusy(false); }
  };

  const applySuggestions = async () => {
    const selected = assistantSuggestions.filter(item => item.selected);
    if (!selected.length || saving) return;
    const current = timestamp();
    const additions: PlanItem[] = selected.map(item => ({
      id: newId(), title: item.title.trim(), notes: item.notes.trim(), quadrant: item.quadrant,
      plan_date: selectedDate, timezone, start_time: item.start_time || null, due_at: null,
      estimate_minutes: item.estimate_minutes,
      reminder_at: reminderFromStart(selectedDate, item.start_time, item.reminder_offset_minutes),
      status: 'todo' as const, created_at: current, updated_at: current,
    })).filter(item => item.title);
    if (await persist([...additions, ...items])) {
      setAssistantOpen(false); setAssistantSuggestions([]); setAssistantSummary(''); setAssistantRequest('');
    }
  };

  return <CodexShell active="plans" pageTitle="今日计划">
    <section className="plans-page" aria-label="今日计划">
      <header className="plans-header">
        <div><p className="plans-eyebrow">PERSONAL WORKSPACE</p><h2>{dateHeading(selectedDate)}</h2><p className="plans-summary">{completed} / {selectedItems.length} 项完成 · 时区 {timezone}</p></div>
        <div className="plans-header-actions"><button className="btn btn-secondary" type="button" onClick={() => { setSelectedDate(localDate(timezone)); }}>今天</button><button className="btn btn-secondary" type="button" onClick={() => void reload()} disabled={loading || saving || dirty} title={dirty ? '请先保存或放弃未保存修改' : undefined}>刷新</button><button className="btn btn-secondary" type="button" onClick={() => setAssistantOpen(value => !value)}>Gemini 建议</button><button className="btn btn-primary" type="button" onClick={() => document.getElementById('plan-title')?.focus()}>＋ 新计划</button></div>
        <nav className="plans-date-nav" aria-label="日期选择"><button type="button" className="btn btn-ghost" aria-label="前一天" onClick={() => setSelectedDate(value => shiftDate(value, -1))}>‹</button><input aria-label="计划日期" type="date" value={selectedDate} onChange={event => setSelectedDate(event.target.value)} /><button type="button" className="btn btn-ghost" aria-label="后一天" onClick={() => setSelectedDate(value => shiftDate(value, 1))}>›</button></nav>
      </header>

      <form className="plans-create" onSubmit={addTask}>
        <label className="sr-only" htmlFor="plan-title">计划标题</label><input id="plan-title" value={title} onChange={event => setTitle(event.target.value)} maxLength={160} placeholder="添加今天要做的事…" required />
        <label className="sr-only" htmlFor="plan-quadrant">计划分类</label><select id="plan-quadrant" value={quadrant} onChange={event => setQuadrant(event.target.value as PlanQuadrant)}>{groups.map(group => <option key={group.id} value={group.id}>{group.title}</option>)}</select>
        <label className="plans-estimate"><span>预计</span><input aria-label="预计分钟" type="number" min={5} max={1440} step={5} value={estimate} onChange={event => setEstimate(Math.max(5, Math.min(1440, Number(event.target.value) || 5)))} /><span>分钟</span></label>
        <label className="plans-reminder"><span>提醒</span><input aria-label="提醒时间" type="datetime-local" value={reminderInput} onChange={event => setReminderInput(event.target.value)} /></label>
        <button className="btn btn-primary" type="submit">添加</button>
      </form>

      <div className="plans-toolbar"><div className="plans-save-status" role="status">{loading ? '正在读取计划…' : saving ? '保存中…' : dirty && feedback ? feedback : dirty ? '有未保存修改' : feedback || '已同步'}</div><div><button className="btn btn-ghost" type="button" onClick={() => void reload()} disabled={!dirty || saving}>放弃修改</button><button className="btn btn-primary" type="button" onClick={() => void persist(items)} disabled={!dirty || saving}>{saving ? '保存中…' : '保存计划'}</button></div></div>
      {error ? <div className="err plans-error" role="alert">{error}{error.includes('其他设备') ? <button className="btn btn-ghost btn-sm" type="button" onClick={() => { if (window.confirm('重新加载会丢弃尚未保存的修改，继续？')) void reload(); }}>重新加载</button> : null}</div> : null}

      {assistantOpen ? <section className="plans-assistant" aria-label="Gemini 每日计划建议">
        <header><div><p className="plans-eyebrow">GEMINI 助手</p><h3>把目标整理成可选计划</h3><p>建议先预览和编辑；只有点击“添加选中建议并保存”后才会写入计划。</p></div><button className="btn btn-ghost btn-sm" type="button" onClick={() => setAssistantOpen(false)}>关闭</button></header>
        <label htmlFor="plan-assistant-request">告诉 Gemini 你的目标<textarea id="plan-assistant-request" value={assistantRequest} onChange={event => setAssistantRequest(event.target.value)} maxLength={4000} rows={3} placeholder="例如：今天先完成项目方案，下午运动，给重要任务留出专注时间。" /></label>
        <div className="plans-assistant-actions"><button className="btn btn-primary" type="button" onClick={() => void generateSuggestions()} disabled={assistantBusy || !assistantRequest.trim()}>{assistantBusy ? '正在整理建议…' : '生成建议'}</button><a href="/admin/services?tab=ai">管理 Gemini 服务</a></div>
        {assistantError ? <p className="plans-assistant-error" role="alert">{assistantError}</p> : null}
        {assistantSummary ? <div className="plans-assistant-preview"><p>{assistantSummary}</p><small>{assistantModel} · 预览不会自动保存</small>
          <ul>{assistantSuggestions.map(item => <li key={item.draftId}>
            <label className="plans-assistant-select"><input type="checkbox" checked={item.selected} onChange={event => setAssistantSuggestions(current => current.map(draft => draft.draftId === item.draftId ? { ...draft, selected: event.target.checked } : draft))} /><span className="sr-only">选择建议</span></label>
            <div className="plans-assistant-suggestion"><input aria-label="建议任务标题" maxLength={160} value={item.title} onChange={event => setAssistantSuggestions(current => current.map(draft => draft.draftId === item.draftId ? { ...draft, title: event.target.value } : draft))} /><p>{item.reason}</p>{item.notes ? <small>{item.notes}</small> : null}
              <div><select aria-label="建议任务分类" value={item.quadrant} onChange={event => setAssistantSuggestions(current => current.map(draft => draft.draftId === item.draftId ? { ...draft, quadrant: event.target.value as PlanQuadrant } : draft))}>{groups.map(group => <option key={group.id} value={group.id}>{group.title}</option>)}</select><label>预计分钟<input aria-label="建议预计分钟" type="number" min={5} max={1440} step={5} value={item.estimate_minutes} onChange={event => setAssistantSuggestions(current => current.map(draft => draft.draftId === item.draftId ? { ...draft, estimate_minutes: Math.max(5, Math.min(1440, Number(event.target.value) || 5)) } : draft))} /></label><label>开始时间<input aria-label="建议开始时间" type="time" value={item.start_time} onChange={event => setAssistantSuggestions(current => current.map(draft => draft.draftId === item.draftId ? { ...draft, start_time: event.target.value, reminder_offset_minutes: event.target.value ? draft.reminder_offset_minutes : 0 } : draft))} /></label></div>
            </div>
          </li>)}</ul>
          <button className="btn btn-primary" type="button" onClick={() => void applySuggestions()} disabled={saving || !assistantSuggestions.some(item => item.selected && item.title.trim())}>{saving ? '保存中…' : '添加选中建议并保存'}</button>
        </div> : null}
      </section> : null}

      {loading && !items.length ? null : <div className="plans-grid">{groups.map(group => {
        const groupItems = selectedItems.filter(item => item.quadrant === group.id);
        return <section className={`plans-quadrant plans-quadrant-${group.id}`} key={group.id} aria-label={group.title}>
          <header><div><h3>{group.title}</h3><p>{group.hint}</p></div><span>{groupItems.length}</span></header>
          {groupItems.length ? <ul>{groupItems.map(item => <li className={item.status === 'done' ? 'is-done' : ''} key={item.id}>
            <label className="plans-task-check"><input type="checkbox" checked={item.status === 'done'} onChange={event => updateTask(item.id, { status: event.target.checked ? 'done' : 'todo' })} /><span className="sr-only">标记完成</span></label>
            <div className="plans-task-body"><strong>{item.title}</strong>{item.notes ? <p>{item.notes}</p> : null}<small>{item.estimate_minutes} 分钟{item.start_time ? ` · ${item.start_time}` : ''}{item.due_at ? ` · 截止 ${new Date(item.due_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}</small>
              <div className="plans-task-controls"><select aria-label={`${item.title}状态`} value={item.status} onChange={event => updateTask(item.id, { status: event.target.value as PlanStatus })}>{Object.entries(statusLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select><select aria-label={`${item.title}分类`} value={item.quadrant} onChange={event => updateTask(item.id, { quadrant: event.target.value as PlanQuadrant })}>{groups.map(option => <option value={option.id} key={option.id}>{option.title}</option>)}</select><input aria-label={`${item.title}提醒`} type="datetime-local" value={localInputValue(item.reminder_at)} onChange={event => updateTask(item.id, { reminder_at: isoFromLocalInput(event.target.value) })} /></div>
            </div><button className="btn btn-ghost btn-sm plans-delete" type="button" aria-label={`删除 ${item.title}`} onClick={() => removeTask(item.id)}>删除</button>
          </li>)}</ul> : <p className="plans-empty">暂无计划</p>}
        </section>;
      })}</div>}
      <footer className="plans-footer"><span>Gemini 只生成建议草稿；任务仅在你确认后保存。</span><a href="/admin/services?tab=ai">服务中心</a></footer>
    </section>
  </CodexShell>;
}
