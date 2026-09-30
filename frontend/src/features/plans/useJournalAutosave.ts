import { useCallback, useEffect, useRef, useState } from 'react';
import { createJournal, listJournal, updateJournal, type JournalDraft, type JournalKind, type JournalRecord } from './journalApi';

export type JournalContext = { date: string; kind: JournalKind; timezone: string; weekly: boolean };
type PendingWrite = { draft: JournalDraft; record: JournalRecord | null; key: string };
type SaveState = 'idle' | 'waiting' | 'saving' | 'saved' | 'error';

function today(timezone: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  return ['year', 'month', 'day'].map(type => parts.find(part => part.type === type)?.value).join('-');
}

/** Noon in the record timezone, independent of the browser timezone and DST. */
function noon(date: string, timezone: string): string {
  const desired = Date.parse(`${date}T12:00:00Z`);
  let instant = desired;
  const formatter = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
  for (let attempt = 0; attempt < 3; attempt++) {
    const parts = formatter.formatToParts(new Date(instant));
    const part = (type: string) => parts.find(value => value.type === type)?.value;
    const local = Date.parse(`${part('year')}-${part('month')}-${part('day')}T${part('hour')}:${part('minute')}:${part('second')}Z`);
    instant += desired - local;
  }
  return new Date(instant).toISOString();
}

function newDraft(context: JournalContext, body: string): JournalDraft {
  const day = today(context.timezone);
  const withinWeek = context.weekly && day >= context.date && day <= new Date(Date.parse(`${context.date}T12:00Z`) + 6 * 86400000).toISOString().slice(0, 10);
  return {
    kind: context.kind, occurred_at: context.date === day || withinWeek ? new Date().toISOString() : noon(context.date, context.timezone),
    timezone: context.timezone, ended_at: null, title: '', body,
    question: '', explanation: '', source: '', uncertainty: '', next_check: '', skill: null,
    material: '', raw_result: '', correction: '', previous_view: '', trigger_evidence: '',
    current_view: '', learning_state: '', evidence: '', takeaway: '',
  };
}

function fromRecord(record: JournalRecord): JournalDraft {
  const { id: _id, revision: _revision, local_date: _localDate, created_at: _createdAt, updated_at: _updatedAt, chat_source: _chatSource, ...draft } = record;
  return draft;
}

function matchesDraft(record: JournalRecord, draft: JournalDraft): boolean {
  return Object.entries(draft).every(([key, value]) => {
    const actual = record[key as keyof JournalDraft];
    if ((key === 'occurred_at' || key === 'ended_at') && typeof value === 'string' && typeof actual === 'string') {
      return Date.parse(value) === Date.parse(actual);
    }
    return actual === (typeof value === 'string' ? value.trim() : value);
  });
}

/** One serialized writer. A failed write retains its exact request for safe retry. */
export function useJournalAutosave(context: JournalContext, onSaved: (item: JournalRecord) => void) {
  const [body, setBodyState] = useState('');
  const [record, setRecord] = useState<JournalRecord | null>(null);
  const [state, setState] = useState<SaveState>('idle');
  const [error, setError] = useState('');
  const [dirty, setDirty] = useState(false);
  const [composing, setComposing] = useState(false);
  const [bound, setBound] = useState(context);
  const latestContext = useRef(context);
  const boundRef = useRef(context);
  const bodyRef = useRef('');
  const recordRef = useRef<JournalRecord | null>(null);
  const dirtyRef = useRef(false);
  const savingRef = useRef(false);
  const composingRef = useRef(false);
  const blockedRef = useRef(false);
  const pendingRef = useRef<PendingWrite | null>(null);
  const onSavedRef = useRef(onSaved);
  latestContext.current = context;
  onSavedRef.current = onSaved;

  const reset = useCallback((item: JournalRecord | null = null, discard = false) => {
    if (savingRef.current || (dirtyRef.current && !discard)) return false;
    const next = item ? { date: item.local_date, kind: item.kind, timezone: item.timezone, weekly: false } : latestContext.current;
    boundRef.current = next; setBound(next);
    bodyRef.current = item?.body || ''; setBodyState(bodyRef.current);
    recordRef.current = item; setRecord(item);
    pendingRef.current = null; blockedRef.current = false; dirtyRef.current = false; setDirty(false);
    setError(''); setState(item ? 'saved' : 'idle');
    return true;
  }, []);

  const contextKey = `${context.date}|${context.kind}|${context.timezone}|${context.weekly}`;
  useEffect(() => { reset(); }, [contextKey, reset]);

  const flush = useCallback(async (retry = false) => {
    if (savingRef.current || composingRef.current || !dirtyRef.current || (blockedRef.current && !retry)) return;
    if (!bodyRef.current.trim()) {
      if (recordRef.current) { setError('内容为空，暂未保存；原记录已保留。'); setState('error'); }
      return;
    }
    let pending = pendingRef.current;
    if (!pending) {
      const current = recordRef.current;
      pending = { draft: current ? { ...fromRecord(current), body: bodyRef.current } : newDraft(boundRef.current, bodyRef.current), record: current, key: crypto.randomUUID() };
      pendingRef.current = pending;
    }
    savingRef.current = true; blockedRef.current = false; setState('saving'); setError('');
    try {
      let saved: JournalRecord;
      try {
        const result = pending.record
          ? await updateJournal(pending.record.id, pending.draft, pending.record.revision)
          : await createJournal(pending.draft, pending.key);
        saved = result.item;
        // A keyed retry can return a record subsequently changed on another device.
        if (!pending.record && !matchesDraft(saved, pending.draft)) {
          throw new Error('记录已在其他设备修改。当前文字已保留，请重新读取后再处理。');
        }
      } catch (cause) {
        // If a PUT succeeded but its response was lost, adopt only an exact match.
        if (!pending.record) throw cause;
        const listing = await listJournal({ date: pending.record.local_date }).catch(() => null);
        const candidate = listing?.items.find(item => item.id === pending.record?.id);
        if (!candidate || !matchesDraft(candidate, pending.draft)) throw cause;
        saved = candidate;
      }
      recordRef.current = saved; setRecord(saved); pendingRef.current = null;
      const stillDirty = bodyRef.current.trim() !== saved.body;
      dirtyRef.current = stillDirty; setDirty(stillDirty);
      setState(stillDirty ? 'waiting' : 'saved');
      onSavedRef.current(saved);
    } catch (cause) {
      blockedRef.current = true;
      setError(cause instanceof Error ? cause.message : '保存失败，文字已保留。'); setState('error');
    } finally { savingRef.current = false; }
  }, []);

  function setBody(value: string) {
    if (!recordRef.current && !dirtyRef.current && !savingRef.current) {
      boundRef.current = latestContext.current; setBound(latestContext.current);
    }
    bodyRef.current = value; setBodyState(value);
    const changed = value.trim() !== (recordRef.current?.body || '') || Boolean(pendingRef.current);
    dirtyRef.current = changed; setDirty(changed);
    if (!blockedRef.current && !savingRef.current) { setError(''); setState(changed ? 'waiting' : recordRef.current ? 'saved' : 'idle'); }
  }

  useEffect(() => {
    if (!dirty || composing || state === 'saving' || blockedRef.current) return;
    const timer = window.setTimeout(() => void flush(), 1200);
    return () => window.clearTimeout(timer);
  }, [body, dirty, composing, state, flush]);

  function composition(active: boolean) { composingRef.current = active; setComposing(active); }

  return { body, setBody, record, state, error, dirty, bound, reset, flush, composition };
}
