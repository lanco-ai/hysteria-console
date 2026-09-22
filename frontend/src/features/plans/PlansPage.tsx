import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type ReactElement } from 'react';
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
function normalizePlanTitle(value: string): string { return value.trim().replace(/\s+/g, ' ').toLocaleLowerCase(); }
function planIdentity(item: Pick<PlanItem, 'plan_date' | 'title'>): string { return `${item.plan_date}::${normalizePlanTitle(item.title)}`; }
function planSemanticTitle(value: string): string {
  return normalizePlanTitle(value)
    .replace(/^(完成|做好|处理|安排|进行|继续|开始|优先|尽快|请)\s*/u, '')
    .replace(/[与和及、\s\-_:：/]+/gu, '')
    .replace(/任务$/u, '');
}
function planSemanticIdentity(item: Pick<PlanItem, 'plan_date' | 'title'>): string {
  return `${item.plan_date}::${planSemanticTitle(item.title)}`;
}

type PlanSuggestionDraft = PlanAssistantSuggestion & { draftId: string; taskId: string; selected: boolean };

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
  const [loadFailed, setLoadFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saveState, setSaveState] = useState<'unsaved' | 'saving' | 'saved' | 'failure'>('saved');
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);
  const [conflictDraft, setConflictDraft] = useState<PlanItem[] | null>(null);
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
  const [assistantOutputMode, setAssistantOutputMode] = useState('');
  const [assistantSuggestions, setAssistantSuggestions] = useState<PlanSuggestionDraft[]>([]);
  const [assistantApplyPending, setAssistantApplyPending] = useState(false);
  const hasFormDraft = Boolean(title.trim() || reminderInput);
  const hasProtectedDraft = dirty || hasFormDraft || Boolean(conflictDraft)
    || (assistantApplyPending && assistantSuggestions.length > 0);
  const editingBlocked = loading || loadFailed || !revision;
  const selectedDateRef = useRef(selectedDate);
  const assistantRequestRef = useRef(assistantRequest);
  const editVersion = useRef(0);
  const formDraftVersion = useRef(0);
  const protectedDraftRef = useRef(false);
  protectedDraftRef.current = hasProtectedDraft;
  const itemsRef = useRef<PlanItem[]>([]);
  const saveLock = useRef(false);
  const lastPersistWasClean = useRef(false);
  const assistantBusyRef = useRef(false);
  const generationVersion = useRef(0);
  const confirmedNavigation = useRef(false);
  const reversingHistoryRef = useRef(false);
  const assistantTriggerRef = useRef<HTMLButtonElement | null>(null);
  const assistantCloseRef = useRef<HTMLButtonElement | null>(null);
  const assistantWasOpenRef = useRef(false);

  const replaceItems = useCallback((next: PlanItem[]) => { itemsRef.current = next; setItems(next); }, []);
  const changeSelectedDate = (update: (current: string) => string) => {
    const next = update(selectedDateRef.current);
    selectedDateRef.current = next;
    setSelectedDate(next);
  };
  const changeAssistantRequest = (next: string) => {
    assistantRequestRef.current = next;
    setAssistantRequest(next);
  };

  const reload = useCallback(async (signal?: AbortSignal, allowDiscard = false, discardProtectedDrafts = false) => {
    if (!signal && protectedDraftRef.current && !allowDiscard) return;
    const requestedEditVersion = editVersion.current;
    const requestedFormVersion = formDraftVersion.current;
    setLoading(true); setError('');
    try {
      const snapshot = await loadPlanSnapshot(signal);
      if (signal?.aborted) return;
      if (editVersion.current !== requestedEditVersion || formDraftVersion.current !== requestedFormVersion) {
        setError('读取期间检测到新的本地修改；服务器快照未替换当前内容。');
        return;
      }
      replaceItems(snapshot.items); setRevision(snapshot.revision); setDirty(false); setSaveState('saved'); setLoadFailed(false);
      if (discardProtectedDrafts) {
        formDraftVersion.current += 1;
        setTitle(''); setReminderInput(''); setConflictDraft(null);
        generationVersion.current += 1;
        assistantBusyRef.current = false;
        setAssistantBusy(false); setAssistantApplyPending(false); setAssistantOpen(false);
        setAssistantSuggestions([]); setAssistantSummary(''); setAssistantRequest('');
        assistantRequestRef.current = '';
      }
    } catch (value) {
      if (!signal?.aborted) { setError(value instanceof Error ? value.message : '计划读取失败'); setLoadFailed(true); }
    } finally { if (!signal?.aborted) setLoading(false); }
  }, [replaceItems]);

  const retryRead = () => {
    const preserveConflictDraft = Boolean(conflictDraft);
    if (hasProtectedDraft && !window.confirm(
      preserveConflictDraft
        ? '重新读取会用服务器快照替换当前计划；已保留的冲突草稿仍可在读取成功后恢复。继续？'
        : '重新读取成功后会放弃当前未保存计划和表单草稿。读取失败则草稿仍保留。继续？',
    )) return;
    void reload(undefined, true, hasProtectedDraft && !preserveConflictDraft);
  };

  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [reload]);

  useLayoutEffect(() => {
    const beforeClientPopState = (event: Event) => {
      const guardEvent = event as CustomEvent<{ state: unknown; sameRoute: boolean; fromIndex: number }>;
      const rawTargetIndex = guardEvent.detail.state && typeof guardEvent.detail.state === 'object'
        ? (guardEvent.detail.state as Record<string, unknown>).__hysteriaReactHistoryIndex
        : null;
      const targetIndex = typeof rawTargetIndex === 'number' && Number.isSafeInteger(rawTargetIndex) ? rawTargetIndex : null;
      if (reversingHistoryRef.current) {
        reversingHistoryRef.current = false;
        return;
      }
      if (guardEvent.detail.sameRoute || !protectedDraftRef.current) return;
      if (window.confirm('有未保存的计划修改或草稿，确定离开此页面吗？')) {
        confirmedNavigation.current = true;
        window.setTimeout(() => { confirmedNavigation.current = false; }, 0);
        return;
      }

      event.preventDefault();
      const delta = targetIndex === null ? 1 : guardEvent.detail.fromIndex - targetIndex;
      reversingHistoryRef.current = true;
      window.history.go(delta || 1);
    };
    window.addEventListener('hysteria:before-client-popstate', beforeClientPopState);
    return () => {
      window.removeEventListener('hysteria:before-client-popstate', beforeClientPopState);
    };
  }, []);

  useEffect(() => {
    if (!hasProtectedDraft) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (confirmedNavigation.current) return;
      event.preventDefault();
      event.returnValue = '';
    };
    const onDocumentClick = (event: MouseEvent) => {
      if (!(event.target instanceof Element)) return;
      const link = event.target.closest<HTMLAnchorElement>('a[href]');
      if (!link || link.target || link.hasAttribute('download')) return;
      const target = new URL(link.href, window.location.href);
      if (target.origin !== window.location.origin || target.pathname === window.location.pathname && target.search === window.location.search && target.hash) return;
      if (window.confirm('有未保存的计划修改或草稿，确定离开此页面吗？')) {
        confirmedNavigation.current = true;
        window.setTimeout(() => { confirmedNavigation.current = false; }, 0);
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation?.();
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    document.addEventListener('click', onDocumentClick, true);
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload);
      document.removeEventListener('click', onDocumentClick, true);
    };
  }, [hasProtectedDraft]);

  const persist = async (next: PlanItem[]) => {
    if (saveLock.current || editingBlocked) return false;
    saveLock.current = true;
    lastPersistWasClean.current = false;
    const submittedEditVersion = editVersion.current;
    const baseItems = itemsRef.current;
    const baseById = new Map(baseItems.map(item => [item.id, item]));
    setSaving(true); setSaveState('saving'); setError(''); setFeedback('');
    try {
      const snapshot = await savePlanSnapshot({ items: next, revision });
      setRevision(snapshot.revision);
      if (editVersion.current === submittedEditVersion) {
        replaceItems(snapshot.items); setDirty(false); setSaveState('saved'); setLastSavedAt(new Date().toISOString()); setFeedback(''); setConflictDraft(null);
        lastPersistWasClean.current = true;
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
        setDirty(true); setSaveState('unsaved'); setLastSavedAt(new Date().toISOString()); setFeedback('保存期间的新修改尚未同步，请再次保存');
      }
      return true;
    } catch (value) {
      setSaveState('failure');
      if (value instanceof Error && value.message.includes('其他设备')) setConflictDraft([...itemsRef.current]);
      setError(value instanceof Error ? value.message : '计划保存失败');
      return false;
    } finally { saveLock.current = false; setSaving(false); }
  };

  const changeItems = (next: PlanItem[]) => { editVersion.current += 1; replaceItems(next); setDirty(true); setSaveState(saveLock.current ? 'saving' : 'unsaved'); setFeedback(''); };
  const selectedItems = items.filter(item => item.plan_date === selectedDate);
  const completed = selectedItems.filter(item => item.status === 'done').length;

  const addTask = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (editingBlocked || !title.trim()) return;
    const current = timestamp();
    const item: PlanItem = {
      id: newId(), title: title.trim(), notes: '', quadrant, plan_date: selectedDate, timezone,
      start_time: null, due_at: null, estimate_minutes: estimate,
      reminder_at: isoFromLocalInput(reminderInput), status: 'todo', created_at: current, updated_at: current,
    };
    changeItems([item, ...items]); setTitle(''); setReminderInput('');
  };

  const updateTask = (id: string, patch: Partial<PlanItem>) => {
    if (editingBlocked) return;
    changeItems(items.map(item => item.id === id ? { ...item, ...patch, updated_at: timestamp() } : item));
    if (patch.status === 'done') setFeedback('已标记完成；点击“保存计划”后同步');
    else if (patch.status === 'todo' || patch.status === 'in_progress') setFeedback('状态已修改；点击“保存计划”后同步');
  };

  const removeTask = (id: string) => {
    if (editingBlocked) return;
    if (!window.confirm('删除这条计划？')) return;
    changeItems(items.filter(item => item.id !== id));
    setFeedback('已从当前草稿删除；点击“保存计划”后同步');
  };

  const restoreConflictDraft = () => {
    if (!conflictDraft || !window.confirm('恢复本地冲突草稿会将它作为完整计划快照提交，可能替换服务器上的其他改动。继续？')) return;
    changeItems(conflictDraft);
    setConflictDraft(null);
    setError('');
    setFeedback('已恢复本地草稿；保存会提交完整计划快照');
  };

  const generateSuggestions = async () => {
    if (editingBlocked || !assistantRequest.trim() || assistantBusyRef.current) return;
    const requestVersion = ++generationVersion.current;
    const submittedEditVersion = editVersion.current;
    const submittedDate = selectedDate;
    const submittedRequest = assistantRequest.trim();
    assistantBusyRef.current = true;
    setAssistantBusy(true); setAssistantApplyPending(false); setAssistantError(''); setAssistantSummary(''); setAssistantSuggestions([]);
    try {
      const result = await requestPlanAssistant({
        date: submittedDate,
        timezone,
        request: submittedRequest,
        existing_tasks: selectedItems.map(({ title, quadrant, status, estimate_minutes }) => ({ title, quadrant, status, estimate_minutes })),
      });
      if (generationVersion.current !== requestVersion) return;
      if (editVersion.current !== submittedEditVersion || selectedDateRef.current !== submittedDate || assistantRequestRef.current.trim() !== submittedRequest) {
        setAssistantError('计划或需求在生成期间发生了变化，请重新生成建议。');
        return;
      }
      setAssistantSummary(result.summary);
      setAssistantModel(`${result.service_name} · ${result.model}`);
      setAssistantOutputMode(result.structured_output);
      setAssistantSuggestions(result.suggestions.map(item => ({ ...item, draftId: newId(), taskId: newId(), selected: true })));
    } catch (value) {
      if (generationVersion.current !== requestVersion) return;
      setAssistantError(value instanceof Error ? value.message : 'AI 建议暂时不可用。');
    } finally {
      if (generationVersion.current === requestVersion) {
        assistantBusyRef.current = false;
        setAssistantBusy(false);
      }
    }
  };

  const closeAssistant = useCallback(() => {
    generationVersion.current += 1;
    assistantBusyRef.current = false;
    setAssistantBusy(false);
    setAssistantOpen(false);
  }, []);

  useEffect(() => {
    if (!assistantOpen) {
      if (assistantWasOpenRef.current) assistantTriggerRef.current?.focus({ preventScroll: true });
      assistantWasOpenRef.current = false;
      return;
    }
    assistantWasOpenRef.current = true;
    assistantCloseRef.current?.focus({ preventScroll: true });
    const onAssistantKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeAssistant();
        return;
      }
      if (event.key !== 'Tab') return;
      const panel = document.querySelector<HTMLElement>('.plans-assistant');
      if (!panel) return;
      const focusable = Array.from(panel.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
      )).filter(element => element.getClientRects().length > 0);
      if (!focusable.length) {
        event.preventDefault();
        panel.focus({ preventScroll: true });
        return;
      }
      const first = focusable[0]!;
      const last = focusable[focusable.length - 1]!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus({ preventScroll: true });
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus({ preventScroll: true });
      }
    };
    document.addEventListener('keydown', onAssistantKeyDown);
    return () => document.removeEventListener('keydown', onAssistantKeyDown);
  }, [assistantOpen, closeAssistant]);

  const applySuggestions = async () => {
    const selected = assistantSuggestions.filter(item => item.selected);
    if (!selected.length || saving || editingBlocked) return;
    const current = timestamp();
    const existingIds = new Set(items.map(item => item.id));
    const additionIds = new Set<string>();
    const existingKeys = new Set(items.map(planIdentity));
    const additionKeys = new Set<string>();
    const existingSemantic = new Map(items.map(item => [planSemanticIdentity(item), item]));
    const additions: PlanItem[] = [];
    const additionsSemantic = new Map<string, PlanItem>();
    const updates = new Map<string, PlanItem>();
    selected.map(item => ({
      id: item.taskId, title: item.title.trim(), notes: item.notes.trim(), quadrant: item.quadrant,
      plan_date: selectedDate, timezone, start_time: item.start_time || null, due_at: null,
      estimate_minutes: item.estimate_minutes,
      reminder_at: reminderFromStart(selectedDate, item.start_time, item.reminder_offset_minutes),
      status: 'todo' as const, created_at: current, updated_at: current,
    })).forEach(item => {
      if (!item.title) return;
      const key = planIdentity(item);
      if (existingIds.has(item.id) || additionIds.has(item.id) || existingKeys.has(key) || additionKeys.has(key)) return;
      const existing = existingSemantic.get(planSemanticIdentity(item));
      if (existing) {
        updates.set(existing.id, {
          ...existing,
          title: item.title,
          notes: item.notes || existing.notes,
          quadrant: item.quadrant,
          start_time: item.start_time,
          estimate_minutes: item.estimate_minutes,
          reminder_at: item.reminder_at,
          updated_at: current,
        });
        return;
      }
      const semanticKey = planSemanticIdentity(item);
      const added = additionsSemantic.get(semanticKey);
      if (added) {
        const replacement = {
          ...added,
          title: item.title,
          notes: item.notes || added.notes,
          quadrant: item.quadrant,
          start_time: item.start_time,
          estimate_minutes: item.estimate_minutes,
          reminder_at: item.reminder_at,
          updated_at: current,
        };
        const index = additions.findIndex(candidate => candidate.id === added.id);
        if (index >= 0) additions[index] = replacement;
        additionsSemantic.set(semanticKey, replacement);
        return;
      }
      additionIds.add(item.id);
      additionKeys.add(key);
      additions.push(item);
      additionsSemantic.set(semanticKey, item);
    });
    if (!additions.length && !updates.size) {
      setAssistantOpen(false); setAssistantSuggestions([]); setAssistantSummary('');
      setAssistantApplyPending(false); setAssistantRequest(''); assistantRequestRef.current = '';
      setFeedback('所选建议已在计划中，无需重复添加');
      return;
    }
    setAssistantApplyPending(true);
    const nextItems = [
      ...additions,
      ...items.filter(item => !updates.has(item.id)).map(item => updates.get(item.id) || item),
      ...Array.from(updates.values()),
    ];
    if (await persist(nextItems)) {
      setAssistantOpen(false); setAssistantSuggestions([]); setAssistantSummary(''); setAssistantRequest('');
      assistantRequestRef.current = ''; setAssistantApplyPending(false);
      if (lastPersistWasClean.current) setFeedback(updates.size ? `已合并 ${updates.size} 条相似建议并保存` : '已添加建议并保存');
    }
  };

  const abandonLocalChanges = () => {
    if (!window.confirm('放弃所有未保存修改和表单草稿？服务器上已确认保存的内容不会更改。')) return;
    if (dirty) {
      void reload(undefined, true, true);
      return;
    }
    formDraftVersion.current += 1;
    setTitle(''); setReminderInput('');
    setAssistantApplyPending(false); setAssistantOpen(false); setAssistantSuggestions([]); setAssistantSummary('');
    setFeedback(''); setError('');
  };

  const renderPlanError = (className = 'err plans-error') => <div className={className} role="alert">
    <span>{error}</span>
    <div className="plans-error-actions">
      {loadFailed ? <button className="btn btn-ghost btn-sm" type="button" onClick={retryRead} disabled={loading || saving}>重试读取</button> : null}
      {error.includes('其他设备') ? <>
        <button className="btn btn-ghost btn-sm" type="button" onClick={() => { if (window.confirm('加载服务器最新版本会替换当前页面内容；本地冲突草稿会保留，可随后恢复。继续？')) void reload(undefined, true); }} disabled={loading || saving}>加载服务器最新版本</button>
        {conflictDraft ? <button className="btn btn-ghost btn-sm" type="button" onClick={restoreConflictDraft} disabled={editingBlocked}>恢复本地冲突草稿</button> : null}
      </> : null}
    </div>
  </div>;

  const renderConflictNotice = (className = 'plans-conflict-draft') => <div className={className} role="status">
    <span>冲突前的本地草稿仍保留。恢复后会以完整快照待保存。</span>
    <div className="plans-error-actions"><button className="btn btn-ghost btn-sm" type="button" onClick={restoreConflictDraft}>恢复本地冲突草稿</button><button className="btn btn-ghost btn-sm" type="button" onClick={() => { if (window.confirm('丢弃本地冲突草稿？')) setConflictDraft(null); }}>丢弃本地草稿</button></div>
  </div>;

  return <CodexShell active="plans" pageTitle="今日计划">
    <section className="plans-page" aria-label="今日计划">
      <header className="plans-header">
        <div><p className="plans-eyebrow">PERSONAL WORKSPACE</p><h2>{dateHeading(selectedDate)}</h2><p className="plans-summary">{completed} / {selectedItems.length} 项完成 · 时区 {timezone}</p></div>
        <div className="plans-header-actions"><button className="btn btn-secondary" type="button" onClick={() => changeSelectedDate(() => localDate(timezone))} disabled={editingBlocked}>今天</button><button className="btn btn-secondary" type="button" onClick={() => void reload()} disabled={loading || saving || hasProtectedDraft} title={hasProtectedDraft ? '请先保存或明确放弃未保存的修改' : undefined}>刷新</button><button className="btn btn-secondary" type="button" ref={assistantTriggerRef} onClick={() => { if (assistantOpen) closeAssistant(); else setAssistantOpen(true); }} disabled={editingBlocked}>AI 建议</button><button className="btn btn-primary" type="button" onClick={() => document.getElementById('plan-title')?.focus()} disabled={editingBlocked}>＋ 新计划</button></div>
        <nav className="plans-date-nav" aria-label="日期选择"><button type="button" className="btn btn-ghost" aria-label="前一天" onClick={() => changeSelectedDate(value => shiftDate(value, -1))} disabled={editingBlocked}>‹</button><input aria-label="计划日期" type="date" value={selectedDate} onChange={event => changeSelectedDate(() => event.target.value)} disabled={editingBlocked} /><button type="button" className="btn btn-ghost" aria-label="后一天" onClick={() => changeSelectedDate(value => shiftDate(value, 1))} disabled={editingBlocked}>›</button></nav>
      </header>

      <form className="plans-create" onSubmit={addTask}>
        <label className="sr-only" htmlFor="plan-title">计划标题</label><input id="plan-title" value={title} onChange={event => { formDraftVersion.current += 1; setTitle(event.target.value); setFeedback(''); }} maxLength={160} placeholder="添加今天要做的事…" required disabled={editingBlocked} />
        <label className="sr-only" htmlFor="plan-quadrant">计划分类</label><select id="plan-quadrant" value={quadrant} onChange={event => setQuadrant(event.target.value as PlanQuadrant)} disabled={editingBlocked}>{groups.map(group => <option key={group.id} value={group.id}>{group.title}</option>)}</select>
        <label className="plans-estimate"><span>预计</span><input aria-label="预计分钟" type="number" min={5} max={1440} step={5} value={estimate} onChange={event => setEstimate(Math.max(5, Math.min(1440, Number(event.target.value) || 5)))} disabled={editingBlocked} /><span>分钟</span></label>
        <label className="plans-reminder"><span>提醒</span><input aria-label="提醒时间" type="datetime-local" value={reminderInput} onChange={event => { formDraftVersion.current += 1; setReminderInput(event.target.value); setFeedback(''); }} disabled={editingBlocked} /></label>
        <button className="btn btn-primary" type="submit" disabled={editingBlocked}>添加</button>
      </form>

      <div className="plans-toolbar"><div className="plans-toolbar-status"><div className="plans-save-status" role="status">{loading ? '正在读取计划…' : loadFailed ? '计划读取失败' : saveState === 'saving' ? '保存中…' : saveState === 'failure' ? '保存失败' : hasProtectedDraft || saveState === 'unsaved' ? '未保存' : '已保存'}</div>{lastSavedAt ? <small className="plans-saved-at">最近成功保存：{new Date(lastSavedAt).toLocaleString()}</small> : null}{feedback ? <small className="plans-save-feedback">{feedback}</small> : hasFormDraft ? <small className="plans-save-feedback">表单草稿尚未加入计划；添加后才能保存。</small> : null}</div><div className="plans-toolbar-actions"><button className="btn btn-ghost" type="button" onClick={abandonLocalChanges} disabled={!(dirty || hasFormDraft || assistantApplyPending) || saving || loading || editingBlocked}>放弃修改</button><button className="btn btn-primary" type="button" onClick={() => void persist(items)} disabled={!dirty || saving || editingBlocked}>{saving ? '保存中…' : '保存计划'}</button></div></div>
      {!assistantOpen && error ? renderPlanError() : null}
      {!assistantOpen && conflictDraft && !error ? renderConflictNotice() : null}

        {assistantOpen ? <><button className="plans-assistant-backdrop" type="button" tabIndex={-1} aria-label="关闭 AI 建议" onClick={closeAssistant} /><section className="plans-assistant" role="region" aria-label="AI 每日计划建议" tabIndex={-1}>
        <div className="plans-assistant-dialog" role="dialog" aria-modal="true" aria-labelledby="plans-assistant-title">
        <header><div><p className="plans-eyebrow">AI 助手</p><h3 id="plans-assistant-title">把目标整理成可选计划</h3><p>建议先预览和编辑；只有点击“添加选中建议并保存”后才会写入计划。</p></div><button className="btn btn-ghost btn-sm" type="button" ref={assistantCloseRef} onClick={closeAssistant}>关闭</button></header>
        {error ? renderPlanError('err plans-error plans-assistant-save-state') : conflictDraft ? renderConflictNotice('plans-conflict-draft plans-assistant-save-state') : null}
        <label htmlFor="plan-assistant-request">告诉 Gemini 你的目标<textarea id="plan-assistant-request" value={assistantRequest} onChange={event => changeAssistantRequest(event.target.value)} maxLength={4000} rows={3} placeholder="例如：今天先完成项目方案，下午运动，给重要任务留出专注时间。" disabled={editingBlocked} /></label>
        <div className="plans-assistant-actions"><button className="btn btn-primary" type="button" onClick={() => void generateSuggestions()} disabled={editingBlocked || assistantBusy || !assistantRequest.trim()}>{assistantBusy ? '正在整理建议…' : '生成建议'}</button><a href="/admin/services?tab=ai">选择服务和模型</a></div>
        {assistantError ? <p className="plans-assistant-error" role="alert">{assistantError}</p> : null}
        {assistantSummary ? <div className="plans-assistant-preview"><p>{assistantSummary}</p><small>{assistantModel} · 预览不会自动保存 · {assistantOutputMode === 'json_text_fallback' ? '结构化参数不支持，已降级为严格 JSON 文本并完成校验' : assistantOutputMode === 'json_schema' ? 'JSON Schema 输出已通过结构校验' : 'Gemini 原生结构化输出已通过校验'}</small>
          <ul>{assistantSuggestions.map(item => <li key={item.draftId}>
            <label className="plans-assistant-select"><input type="checkbox" checked={item.selected} onChange={event => setAssistantSuggestions(current => current.map(draft => draft.draftId === item.draftId ? { ...draft, selected: event.target.checked } : draft))} /><span className="sr-only">选择建议</span></label>
            <div className="plans-assistant-suggestion"><input aria-label="建议任务标题" maxLength={160} value={item.title} onChange={event => setAssistantSuggestions(current => current.map(draft => draft.draftId === item.draftId ? { ...draft, title: event.target.value } : draft))} /><p>{item.reason}</p>{item.notes ? <small>{item.notes}</small> : null}
              <div><select aria-label="建议任务分类" value={item.quadrant} onChange={event => setAssistantSuggestions(current => current.map(draft => draft.draftId === item.draftId ? { ...draft, quadrant: event.target.value as PlanQuadrant } : draft))}>{groups.map(group => <option key={group.id} value={group.id}>{group.title}</option>)}</select><label>预计分钟<input aria-label="建议预计分钟" type="number" min={5} max={1440} step={5} value={item.estimate_minutes} onChange={event => setAssistantSuggestions(current => current.map(draft => draft.draftId === item.draftId ? { ...draft, estimate_minutes: Math.max(5, Math.min(1440, Number(event.target.value) || 5)) } : draft))} /></label><label>开始时间<input aria-label="建议开始时间" type="time" value={item.start_time} onChange={event => setAssistantSuggestions(current => current.map(draft => draft.draftId === item.draftId ? { ...draft, start_time: event.target.value, reminder_offset_minutes: event.target.value ? draft.reminder_offset_minutes : 0 } : draft))} /></label></div>
            </div>
          </li>)}</ul>
          <button className="btn btn-primary" type="button" onClick={() => void applySuggestions()} disabled={saving || !assistantSuggestions.some(item => item.selected && item.title.trim())}>{saving ? '保存中…' : '添加选中建议并保存'}</button>
        </div> : null}
        </div></section></> : null}

      {loading && !items.length ? null : <div className="plans-grid">{groups.map(group => {
        const groupItems = selectedItems.filter(item => item.quadrant === group.id);
        return <section className={`plans-quadrant plans-quadrant-${group.id}`} key={group.id} aria-label={group.title}>
          <header><div><h3>{group.title}</h3><p>{group.hint}</p></div><span>{groupItems.length}</span></header>
          {groupItems.length ? <ul>{groupItems.map(item => <li className={item.status === 'done' ? 'is-done' : ''} key={item.id}>
            <label className="plans-task-check"><input type="checkbox" checked={item.status === 'done'} onChange={event => updateTask(item.id, { status: event.target.checked ? 'done' : 'todo' })} disabled={editingBlocked} /><span className="sr-only">标记完成</span></label>
            <div className="plans-task-body"><strong>{item.title}</strong>{item.notes ? <p>{item.notes}</p> : null}<small>{item.estimate_minutes} 分钟{item.start_time ? ` · ${item.start_time}` : ''}{item.due_at ? ` · 截止 ${new Date(item.due_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}</small>
              <div className="plans-task-controls"><select aria-label={`${item.title}状态`} value={item.status} onChange={event => updateTask(item.id, { status: event.target.value as PlanStatus })} disabled={editingBlocked}>{Object.entries(statusLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select><select aria-label={`${item.title}分类`} value={item.quadrant} onChange={event => updateTask(item.id, { quadrant: event.target.value as PlanQuadrant })} disabled={editingBlocked}>{groups.map(option => <option value={option.id} key={option.id}>{option.title}</option>)}</select><input aria-label={`${item.title}提醒`} type="datetime-local" value={localInputValue(item.reminder_at)} onChange={event => updateTask(item.id, { reminder_at: isoFromLocalInput(event.target.value) })} disabled={editingBlocked} /></div>
            </div><button className="btn btn-ghost btn-sm plans-delete" type="button" aria-label={`删除 ${item.title}`} onClick={() => removeTask(item.id)} disabled={editingBlocked}>删除</button>
          </li>)}</ul> : <p className="plans-empty">暂无计划</p>}
        </section>;
      })}</div>}
      <footer className="plans-footer"><span>AI 只生成建议草稿；任务仅在你确认后保存。</span><a href="/admin/services?tab=ai">服务中心</a></footer>
    </section>
  </CodexShell>;
}
