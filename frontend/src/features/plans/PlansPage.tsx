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
function planSemanticTitle(value: string): string {
  const normalized = normalizePlanTitle(value);
  return normalized
    .replace(/^(完成|做好|处理|安排|进行|继续|开始|优先|尽快|请)\s*/u, '')
    .replace(/[与和及、\s\-_:：/]+/gu, '')
    .replace(/任务$/u, '') || normalized;
}

type PlanSuggestionDraft = PlanAssistantSuggestion & { draftId: string; taskId: string; selected: boolean; targetId: string; targetManuallySelected: boolean };
const UNRESOLVED_SUGGESTION_TARGET = '__unresolved__';

function suggestedTarget(title: string, existing: PlanItem[]): string {
  const exact = existing.filter(item => normalizePlanTitle(item.title) === normalizePlanTitle(title));
  if (exact.length === 1) return exact[0]!.id;
  if (exact.length > 1) return UNRESOLVED_SUGGESTION_TARGET;
  const semantic = existing.filter(item => planSemanticTitle(item.title) === planSemanticTitle(title));
  return semantic.length === 1 ? semantic[0]!.id : semantic.length > 1 ? UNRESOLVED_SUGGESTION_TARGET : '';
}

function combinedNotes(existing: string, suggested: string): string {
  const detail = suggested.trim();
  if (!detail) return existing;
  const normalizeLines = (value: string) => value.trim().split(/\r?\n/u).map(line => normalizePlanTitle(line)).filter(Boolean);
  const currentLines = normalizeLines(existing);
  const detailLines = normalizeLines(detail);
  const alreadyIncluded = detailLines.length > 0 && currentLines.some((_, start) =>
    detailLines.every((line, offset) => currentLines[start + offset] === line),
  );
  if (alreadyIncluded) return existing;
  const current = existing.trim();
  return current ? `${existing.trimEnd()}\n${detail}` : detail;
}

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
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
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
  const hasProtectedDraft = dirty || hasFormDraft || Boolean(conflictDraft) || Boolean(pendingDeleteId)
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
  const pendingDeleteIdRef = useRef<string | null>(null);
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
        latestById.forEach((latest, id) => {
          if (pendingDeleteIdRef.current === id) return;
          const baseline = baseById.get(id);
          if (!baseline || JSON.stringify(latest) !== JSON.stringify(baseline)) mergedItems.push(latest);
        });
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
  const suggestionIssues = new Map<string, string>();
  const suggestionWarnings = new Map<string, string>();
  const usedTargets = new Set<string>();
  const claimedTitles = new Set<string>();
  for (const suggestion of assistantSuggestions.filter(item => item.selected)) {
    const title = suggestion.title.trim();
    if (!title) { suggestionIssues.set(suggestion.draftId, '请填写标题，或取消选择这条建议。'); continue; }
    const key = planSemanticTitle(title);
    if (suggestion.targetId === UNRESOLVED_SUGGESTION_TARGET) {
      suggestionIssues.set(suggestion.draftId, '有多个相似任务；请选择要更新的任务，或明确选择添加为新计划。');
    } else if (suggestion.targetId) {
      const target = selectedItems.find(item => item.id === suggestion.targetId);
      if (!target) suggestionIssues.set(suggestion.draftId, '目标任务已不在所选日期，请重新选择。');
      else if (usedTargets.has(target.id)) suggestionIssues.set(suggestion.draftId, '另一条建议已选择此任务，请改选目标或取消选择。');
      else if (selectedItems.some(item => item.id !== target.id && planSemanticTitle(item.title) === key)) suggestionIssues.set(suggestion.draftId, '这个标题与另一条现有任务重复，请修改标题或选择对应任务。');
      else if (claimedTitles.has(key)) suggestionIssues.set(suggestion.draftId, '另一条建议已使用这个标题，请修改标题或取消选择。');
      else if (Array.from(combinedNotes(target.notes, suggestion.notes)).length > 5000) suggestionIssues.set(suggestion.draftId, '补充后备注会超过 5000 字符限制；请缩短或清空 AI 备注。');
      usedTargets.add(suggestion.targetId);
    } else {
      const matches = selectedItems.filter(item => planSemanticTitle(item.title) === key);
      if (claimedTitles.has(key)) suggestionIssues.set(suggestion.draftId, '另一条建议已使用这个标题，请修改标题或取消选择。');
      else if (matches.length && !suggestion.targetManuallySelected) suggestionIssues.set(suggestion.draftId, '计划中已有相似任务；请选择更新目标，或明确选择添加为新计划。');
      else if (matches.length) suggestionWarnings.set(suggestion.draftId, '计划中已有相似任务；确认后仍会新增一条计划。');
    }
    claimedTitles.add(key);
  }

  const addTask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (editingBlocked || saving || !title.trim()) return;
    if (dirty && !window.confirm('添加计划会同时保存当前其他未保存的修改。继续？')) return;
    const current = timestamp();
    const item: PlanItem = {
      id: newId(), title: title.trim(), notes: '', quadrant, plan_date: selectedDate, timezone,
      start_time: null, due_at: null, estimate_minutes: estimate,
      reminder_at: isoFromLocalInput(reminderInput), status: 'todo', created_at: current, updated_at: current,
    };
    const nextItems = [item, ...itemsRef.current];
    changeItems(nextItems); formDraftVersion.current += 1; setTitle(''); setReminderInput('');
    if (!(await persist(nextItems))) setFeedback('添加未保存，任务已保留为本地草稿；点击“保存计划”可重试。');
  };

  const updateTask = (id: string, patch: Partial<PlanItem>) => {
    if (editingBlocked || pendingDeleteIdRef.current === id) return;
    changeItems(items.map(item => item.id === id ? { ...item, ...patch, updated_at: timestamp() } : item));
    if (patch.status === 'done') setFeedback('已标记完成；点击“保存计划”后同步');
    else if (patch.status === 'todo' || patch.status === 'in_progress') setFeedback('状态已修改；点击“保存计划”后同步');
  };

  const removeTask = async (id: string) => {
    if (editingBlocked || saving) return;
    if (!window.confirm('删除这条计划？')) return;
    if (dirty && !window.confirm('删除计划会同时保存当前其他未保存的修改。继续？')) return;
    const nextItems = itemsRef.current.filter(item => item.id !== id);
    pendingDeleteIdRef.current = id;
    setPendingDeleteId(id);
    try {
      if (!(await persist(nextItems))) setFeedback('删除未保存，任务仍在计划中；请重试删除。');
    } finally {
      pendingDeleteIdRef.current = null;
      setPendingDeleteId(null);
    }
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
      const existing = itemsRef.current.filter(item => item.plan_date === submittedDate);
      setAssistantSuggestions(result.suggestions.map(item => ({ ...item, draftId: newId(), taskId: newId(), selected: true, targetId: suggestedTarget(item.title, existing), targetManuallySelected: false })));
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
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
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
    return () => { document.removeEventListener('keydown', onAssistantKeyDown); document.body.style.overflow = previousOverflow; };
  }, [assistantOpen, closeAssistant]);

  const applySuggestions = async () => {
    const selected = assistantSuggestions.filter(item => item.selected);
    if (!selected.length || suggestionIssues.size || saving || editingBlocked) return;
    const duplicateAddCount = selected.filter(suggestion => !suggestion.targetId && suggestion.targetManuallySelected
      && selectedItems.some(item => planSemanticTitle(item.title) === planSemanticTitle(suggestion.title))).length;
    if (duplicateAddCount && !window.confirm(`${duplicateAddCount} 条建议与已有计划相似；仍然添加会产生重复计划。继续？`)) return;
    if (dirty && !window.confirm('保存 AI 建议会同时保存当前其他未保存的修改。继续？')) return;
    const current = timestamp();
    const additions: PlanItem[] = [];
    const updates = new Map<string, PlanItem>();
    for (const suggestion of selected) {
      if (suggestion.targetId) {
        const existing = selectedItems.find(item => item.id === suggestion.targetId);
        if (!existing) { setAssistantError('目标任务已变化，请重新选择后保存。'); return; }
        const nextTitle = suggestion.title.trim();
        const nextNotes = combinedNotes(existing.notes, suggestion.notes);
        if (nextTitle !== existing.title || nextNotes !== existing.notes) {
          updates.set(existing.id, { ...existing, title: nextTitle, notes: nextNotes, updated_at: current });
        }
      } else {
        additions.push({
          id: suggestion.taskId, title: suggestion.title.trim(), notes: suggestion.notes.trim(), quadrant: suggestion.quadrant,
          plan_date: selectedDate, timezone, start_time: suggestion.start_time || null, due_at: null,
          estimate_minutes: suggestion.estimate_minutes,
          reminder_at: reminderFromStart(selectedDate, suggestion.start_time, suggestion.reminder_offset_minutes),
          status: 'todo', created_at: current, updated_at: current,
        });
      }
    }
    if (!additions.length && !updates.size) {
      setAssistantOpen(false); setAssistantSuggestions([]); setAssistantSummary('');
      setAssistantApplyPending(false); setAssistantRequest(''); assistantRequestRef.current = '';
      setFeedback('所选建议与现有计划一致，无需保存。');
      return;
    }
    setAssistantApplyPending(true);
    const nextItems = [
      ...additions,
      ...itemsRef.current.map(item => updates.get(item.id) || item),
    ];
    if (await persist(nextItems)) {
      setAssistantOpen(false); setAssistantSuggestions([]); setAssistantSummary(''); setAssistantRequest('');
      assistantRequestRef.current = ''; setAssistantApplyPending(false);
      if (lastPersistWasClean.current) setFeedback(`已保存 ${additions.length} 条新计划、更新 ${updates.size} 条现有计划`);
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
        <div><h2>{dateHeading(selectedDate)}</h2><p className="plans-summary">{completed} / {selectedItems.length} 项完成 · 时区 {timezone}</p></div>
        <div className="plans-header-actions"><button className="btn btn-secondary" type="button" onClick={() => changeSelectedDate(() => localDate(timezone))} disabled={editingBlocked}>今天</button><button className="btn btn-secondary" type="button" onClick={() => void reload()} disabled={loading || saving || hasProtectedDraft} title={hasProtectedDraft ? '请先保存或明确放弃未保存的修改' : undefined}>刷新</button><button className="btn btn-secondary" type="button" ref={assistantTriggerRef} onClick={() => { if (assistantOpen) closeAssistant(); else setAssistantOpen(true); }} disabled={editingBlocked}>AI 建议</button></div>
        <nav className="plans-date-nav" aria-label="日期选择"><button type="button" className="btn btn-ghost" aria-label="前一天" onClick={() => changeSelectedDate(value => shiftDate(value, -1))} disabled={editingBlocked}>‹</button><input aria-label="计划日期" type="date" value={selectedDate} onChange={event => changeSelectedDate(() => event.target.value)} disabled={editingBlocked} /><button type="button" className="btn btn-ghost" aria-label="后一天" onClick={() => changeSelectedDate(value => shiftDate(value, 1))} disabled={editingBlocked}>›</button></nav>
      </header>

      <form className="plans-create" onSubmit={addTask}>
        <label className="sr-only" htmlFor="plan-title">计划标题</label><input id="plan-title" value={title} onChange={event => { formDraftVersion.current += 1; setTitle(event.target.value); setFeedback(''); }} maxLength={160} placeholder="添加今天要做的事…" required disabled={editingBlocked} />
        <label className="sr-only" htmlFor="plan-quadrant">计划分类</label><select id="plan-quadrant" value={quadrant} onChange={event => setQuadrant(event.target.value as PlanQuadrant)} disabled={editingBlocked}>{groups.map(group => <option key={group.id} value={group.id}>{group.title}</option>)}</select>
        <label className="plans-estimate"><span>预计</span><input aria-label="预计分钟" type="number" min={5} max={1440} step={5} value={estimate} onChange={event => setEstimate(Math.max(5, Math.min(1440, Number(event.target.value) || 5)))} disabled={editingBlocked} /><span>分钟</span></label>
        <label className="plans-reminder"><span>提醒</span><input aria-label="提醒时间" type="datetime-local" value={reminderInput} onChange={event => { formDraftVersion.current += 1; setReminderInput(event.target.value); setFeedback(''); }} disabled={editingBlocked} /></label>
        <button className="btn btn-primary" type="submit" disabled={editingBlocked || saving}>{saving ? '保存中…' : '添加并保存'}</button>
      </form>

      <div className="plans-toolbar"><div className="plans-toolbar-status"><div className="plans-save-status" role="status">{loading ? '正在读取计划…' : loadFailed ? '计划读取失败' : saveState === 'saving' ? '保存中…' : saveState === 'failure' ? '保存失败' : hasProtectedDraft || saveState === 'unsaved' ? '未保存' : '已保存'}</div>{lastSavedAt ? <small className="plans-saved-at">最近成功保存：{new Date(lastSavedAt).toLocaleString()}</small> : null}{feedback ? <small className="plans-save-feedback">{feedback}</small> : hasFormDraft ? <small className="plans-save-feedback">表单草稿尚未加入计划；提交后立即保存。</small> : null}</div><div className="plans-toolbar-actions"><button className="btn btn-ghost" type="button" onClick={abandonLocalChanges} disabled={!(dirty || hasFormDraft || assistantApplyPending) || saving || loading || editingBlocked}>放弃修改</button><button className="btn btn-primary" type="button" onClick={() => void persist(items)} disabled={!dirty || saving || editingBlocked}>{saving ? '保存中…' : '保存计划'}</button></div></div>
      {!assistantOpen && error ? renderPlanError() : null}
      {!assistantOpen && conflictDraft && !error ? renderConflictNotice() : null}

        {assistantOpen ? <><button className="plans-assistant-backdrop" type="button" tabIndex={-1} aria-label="关闭 AI 建议" onClick={closeAssistant} /><section className="plans-assistant" role="dialog" aria-modal="true" aria-labelledby="plans-assistant-title" tabIndex={-1}>
        <div className="plans-assistant-dialog">
        <header><div><p className="plans-eyebrow">AI 助手</p><h3 id="plans-assistant-title">把目标整理成可选计划</h3><p>预览后可以将建议添加为新计划，或选择现有任务补充内容。</p></div><button className="btn btn-ghost btn-sm" type="button" ref={assistantCloseRef} onClick={closeAssistant}>关闭</button></header>
        {error ? renderPlanError('err plans-error plans-assistant-save-state') : conflictDraft ? renderConflictNotice('plans-conflict-draft plans-assistant-save-state') : null}
        <label htmlFor="plan-assistant-request">告诉 Gemini 你的目标<textarea id="plan-assistant-request" value={assistantRequest} onChange={event => changeAssistantRequest(event.target.value)} maxLength={4000} rows={3} placeholder="例如：今天先完成项目方案，下午运动，给重要任务留出专注时间。" disabled={editingBlocked} /></label>
        <div className="plans-assistant-actions"><button className="btn btn-primary" type="button" onClick={() => void generateSuggestions()} disabled={editingBlocked || assistantBusy || !assistantRequest.trim()}>{assistantBusy ? '正在整理建议…' : '生成建议'}</button><a href="/admin/services?tab=ai">选择服务和模型</a></div>
        {assistantError ? <p className="plans-assistant-error" role="alert">{assistantError}</p> : null}
        {assistantSummary ? <div className="plans-assistant-preview"><p>{assistantSummary}</p><small>{assistantModel} · 预览不会自动保存 · {assistantOutputMode === 'json_text_fallback' ? '结构化参数不支持，已降级为严格 JSON 文本并完成校验' : assistantOutputMode === 'json_schema' ? 'JSON Schema 输出已通过结构校验' : 'Gemini 原生结构化输出已通过校验'}</small>
          <ul>{assistantSuggestions.map(item => <li key={item.draftId}>
            <label className="plans-assistant-select"><input type="checkbox" aria-label={`选择建议：${item.title || '未命名计划'}`} checked={item.selected} onChange={event => setAssistantSuggestions(current => current.map(draft => draft.draftId === item.draftId ? { ...draft, selected: event.target.checked } : draft))} /></label>
            <div className="plans-assistant-suggestion"><input aria-label="建议任务标题" maxLength={160} value={item.title} onChange={event => { const nextTitle = event.target.value; setAssistantSuggestions(current => current.map(draft => draft.draftId === item.draftId ? { ...draft, title: nextTitle, targetId: draft.targetManuallySelected ? draft.targetId : suggestedTarget(nextTitle, selectedItems) } : draft)); }} /><p>{item.reason}</p>
              <label className="plans-assistant-target">如何应用<select aria-label={`如何应用建议：${item.title || '未命名计划'}`} value={item.targetId} onChange={event => setAssistantSuggestions(current => current.map(draft => draft.draftId === item.draftId ? { ...draft, targetId: event.target.value, targetManuallySelected: true } : draft))}><option value={UNRESOLVED_SUGGESTION_TARGET}>请选择新增或更新</option><option value="">添加为新计划</option>{selectedItems.map(existing => <option key={existing.id} value={existing.id}>更新：{existing.title}</option>)}</select></label>
              <label className="plans-assistant-notes">补充备注<textarea aria-label={`建议备注：${item.title || '未命名计划'}`} value={item.notes} onChange={event => setAssistantSuggestions(current => current.map(draft => draft.draftId === item.draftId ? { ...draft, notes: event.target.value } : draft))} rows={2} maxLength={2000} /></label>
              {item.targetId === UNRESOLVED_SUGGESTION_TARGET ? <small>有多个相似任务，请先选择新增或要更新的计划。</small> : item.targetId ? <small>将保留原任务状态、分类、预计时长和时间安排；新备注会接在原备注后。</small> : <div><select aria-label="建议任务分类" value={item.quadrant} onChange={event => setAssistantSuggestions(current => current.map(draft => draft.draftId === item.draftId ? { ...draft, quadrant: event.target.value as PlanQuadrant } : draft))}>{groups.map(group => <option key={group.id} value={group.id}>{group.title}</option>)}</select><label>预计分钟<input aria-label="建议预计分钟" type="number" min={5} max={1440} step={5} value={item.estimate_minutes} onChange={event => setAssistantSuggestions(current => current.map(draft => draft.draftId === item.draftId ? { ...draft, estimate_minutes: Math.max(5, Math.min(1440, Number(event.target.value) || 5)) } : draft))} /></label><label>开始时间<input aria-label="建议开始时间" type="time" value={item.start_time} onChange={event => setAssistantSuggestions(current => current.map(draft => draft.draftId === item.draftId ? { ...draft, start_time: event.target.value, reminder_offset_minutes: event.target.value ? draft.reminder_offset_minutes : 0 } : draft))} /></label></div>}
              {suggestionIssues.get(item.draftId) ? <small className="plans-assistant-issue" role="alert">{suggestionIssues.get(item.draftId)}</small> : null}
              {suggestionWarnings.get(item.draftId) ? <small className="plans-assistant-warning" role="status">{suggestionWarnings.get(item.draftId)}</small> : null}
            </div>
          </li>)}</ul>
          <button className="btn btn-primary" type="button" onClick={() => void applySuggestions()} disabled={saving || suggestionIssues.size > 0 || !assistantSuggestions.some(item => item.selected)}>{saving ? '保存中…' : '保存选中建议'}</button>
        </div> : null}
        </div></section></> : null}

      {loading && !items.length ? null : <div className="plans-grid">{groups.map(group => {
        const groupItems = selectedItems.filter(item => item.quadrant === group.id);
        return <section className={`plans-quadrant plans-quadrant-${group.id}`} key={group.id} aria-label={group.title}>
          <header><div><h3>{group.title}</h3><p>{group.hint}</p></div><span>{groupItems.length}</span></header>
          {groupItems.length ? <ul>{groupItems.map(item => <li className={item.status === 'done' ? 'is-done' : ''} key={item.id}>
            <label className="plans-task-check"><input type="checkbox" checked={item.status === 'done'} onChange={event => updateTask(item.id, { status: event.target.checked ? 'done' : 'todo' })} disabled={editingBlocked || pendingDeleteId === item.id} /><span className="sr-only">标记完成</span></label>
            <div className="plans-task-body"><strong>{item.title}</strong>{item.notes ? <p>{item.notes}</p> : null}<small>{item.estimate_minutes} 分钟{item.start_time ? ` · ${item.start_time}` : ''}{item.due_at ? ` · 截止 ${new Date(item.due_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}</small>
              <div className="plans-task-controls"><select aria-label={`${item.title}状态`} value={item.status} onChange={event => updateTask(item.id, { status: event.target.value as PlanStatus })} disabled={editingBlocked || pendingDeleteId === item.id}>{Object.entries(statusLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select><select aria-label={`${item.title}分类`} value={item.quadrant} onChange={event => updateTask(item.id, { quadrant: event.target.value as PlanQuadrant })} disabled={editingBlocked || pendingDeleteId === item.id}>{groups.map(option => <option value={option.id} key={option.id}>{option.title}</option>)}</select><input aria-label={`${item.title}提醒`} type="datetime-local" value={localInputValue(item.reminder_at)} onChange={event => updateTask(item.id, { reminder_at: isoFromLocalInput(event.target.value) })} disabled={editingBlocked || pendingDeleteId === item.id} /></div>
            </div><button className="btn btn-ghost btn-sm plans-delete" type="button" aria-label={`删除 ${item.title}`} onClick={() => void removeTask(item.id)} disabled={editingBlocked || saving}>删除</button>
          </li>)}</ul> : <p className="plans-empty">暂无计划</p>}
        </section>;
      })}</div>}
      <footer className="plans-footer"><span>AI 只生成建议草稿；任务仅在你确认后保存。</span><a href="/admin/services?tab=ai">服务中心</a></footer>
    </section>
  </CodexShell>;
}
