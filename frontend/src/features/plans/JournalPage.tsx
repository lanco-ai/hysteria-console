import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type ReactElement } from 'react';
import { JournalDrawer } from './JournalDrawer';
import { createJournal, deleteJournal, downloadJournal, listJournal, loadJournalSummary, updateJournal, type JournalDraft, type JournalKind, type JournalRecord, type JournalSummary } from './journalApi';

const labels: Record<JournalKind, string> = {
  life: '生活', ai_storage: 'AI / 存储学习', paper: '论文阅读', ielts: '雅思练习',
  thought: '思考变化', daily_review: '每日复盘', weekly_review: '每周回顾',
};
const fieldLimits: Partial<Record<keyof JournalDraft, number>> = {
  question: 2000, explanation: 4000, source: 2000, uncertainty: 2000, next_check: 2000,
  material: 2000, raw_result: 4000, correction: 4000, previous_view: 2000,
  trigger_evidence: 2000, current_view: 2000, learning_state: 1000, evidence: 4000, takeaway: 2000,
};
const detailLabels: Partial<Record<keyof JournalDraft, string>> = {
  question: '研究问题', explanation: '自己的解释', source: '依据或来源', uncertainty: '仍有疑点',
  next_check: '下一步验证', material: '练习材料', raw_result: '原始输出或结果', correction: '纠错',
  previous_view: '原先认为', trigger_evidence: '触发证据', current_view: '现在认为',
  learning_state: '学习状态', evidence: '支持这个判断的证据', takeaway: '收获',
};

function recordDetails(item: JournalRecord): Array<[string, string]> {
  const values = Object.entries(detailLabels).flatMap(([key, label]) => {
    const value = item[key as keyof JournalDraft];
    return typeof value === 'string' && value ? [[label, value] as [string, string]] : [];
  });
  if (item.skill) values.unshift(['练习项目', { listening: '听力', speaking: '口语', reading: '阅读', writing: '写作' }[item.skill]]);
  return values;
}

function recordSummary(item: JournalRecord): string {
  return item.title || item.body.slice(0, 48) || recordDetails(item).find(([label]) => label !== '练习项目')?.[1].slice(0, 48) || labels[item.kind];
}

function localInput(value: Date | string): string {
  const date = typeof value === 'string' ? new Date(value) : value;
  const pad = (number: number) => String(number).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function blankDraft(timezone: string, kind: JournalKind = 'life'): JournalDraft {
  return {
    kind, occurred_at: new Date().toISOString(), ended_at: null, timezone,
    title: '', body: '', question: '', explanation: '', source: '', uncertainty: '', next_check: '',
    skill: null, material: '', raw_result: '', correction: '', previous_view: '', trigger_evidence: '',
    current_view: '', learning_state: '', evidence: '', takeaway: '',
  };
}

function weekStart(day: string): string {
  const date = new Date(`${day}T12:00:00Z`);
  const weekday = date.getUTCDay();
  date.setUTCDate(date.getUTCDate() - (weekday === 0 ? 6 : weekday - 1));
  return date.toISOString().slice(0, 10);
}

function shiftWeek(day: string, weeks: number): string {
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + weeks * 7);
  return date.toISOString().slice(0, 10);
}

function weekEnd(day: string): string {
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + 6);
  return date.toISOString().slice(0, 10);
}

function todayInTimezone(timezone: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  const part = (type: string) => parts.find(value => value.type === type)?.value || '';
  return `${part('year')}-${part('month')}-${part('day')}`;
}

function weeklyOccurrence(selectedWeek: string, timezone: string): string {
  return selectedWeek === weekStart(todayInTimezone(timezone))
    ? new Date().toISOString()
    : new Date(`${selectedWeek}T12:00:00`).toISOString();
}

export function JournalPage({ panel, onClose, selectedDate, onSelectDate, timezone, onDraftProtectionChange }: {
  panel: 'timeline' | 'review' | null; onClose: () => void;
  selectedDate: string; onSelectDate: (day: string) => void; timezone: string;
  onDraftProtectionChange: (protectedDraft: boolean) => void;
}): ReactElement {
  const [draft, setDraft] = useState<JournalDraft>(() => blankDraft(timezone));
  const [timeMode, setTimeMode] = useState<'now' | 'manual' | 'week'>('now');
  const [editing, setEditing] = useState<JournalRecord | null>(null);
  const [items, setItems] = useState<JournalRecord[]>([]);
  const [kindFilter, setKindFilter] = useState('');
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [readFailed, setReadFailed] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [summary, setSummary] = useState<JournalSummary | null>(null);
  const [summaryErrorWeek, setSummaryErrorWeek] = useState<string | null>(null);
  const [reviewWeek, setReviewWeek] = useState(() => weekStart(selectedDate));
  const readGeneration = useRef(0);
  const [showEditor, setShowEditor] = useState(false);
  const editorKindRef = useRef<HTMLSelectElement>(null);
  const composeRef = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(false);

  useEffect(() => { setShowEditor(false); }, [panel]);
  useEffect(() => {
    if (showEditor) editorKindRef.current?.focus({ preventScroll: true });
    else if (wasEditing.current && panel) composeRef.current?.focus({ preventScroll: true });
    wasEditing.current = showEditor;
  }, [showEditor, panel]);

  useEffect(() => { setReviewWeek(weekStart(selectedDate)); }, [selectedDate]);

  const visibleSummary = summary?.week_start === reviewWeek ? summary : null;

  const hasDraftContent = Boolean(editing || draft.title.trim() || draft.body.trim() || draft.skill || draft.ended_at
    || timeMode === 'manual' || Object.keys(detailLabels).some(key => String(draft[key as keyof JournalDraft] || '').trim()));
  useLayoutEffect(() => { onDraftProtectionChange(hasDraftContent); }, [hasDraftContent, onDraftProtectionChange]);

  const filters = useMemo(() => ({ date: query || kindFilter ? undefined : selectedDate, kind: kindFilter || undefined, q: query.trim() || undefined }), [selectedDate, kindFilter, query]);
  const reload = useCallback(async (signal?: AbortSignal) => {
    const generation = ++readGeneration.current;
    setLoading(true);
    try {
      const result = await listJournal(filters, signal);
      if (!signal?.aborted && generation === readGeneration.current) { setItems(result.items); setReadFailed(false); setError(''); }
    } catch (cause) {
      if (!signal?.aborted && generation === readGeneration.current) {
        setItems([]);
        setReadFailed(true);
        setError(`记录读取失败，请重试。${cause instanceof Error ? ` ${cause.message}` : ''}`);
      }
    } finally { if (!signal?.aborted && generation === readGeneration.current) setLoading(false); }
  }, [filters]);

  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [reload]);

  useEffect(() => {
    const controller = new AbortController();
    setSummaryErrorWeek(null);
    void loadJournalSummary(reviewWeek, controller.signal).then(result => {
      if (!controller.signal.aborted) setSummary(result);
    }).catch(() => {
      if (!controller.signal.aborted) {
        setSummaryErrorWeek(reviewWeek);
      }
    });
    return () => controller.abort();
  }, [reviewWeek, items]);

  useEffect(() => {
    if (timeMode !== 'now' || editing) return;
    const refresh = () => setDraft(current => ({ ...current, occurred_at: new Date().toISOString() }));
    const refreshIfVisible = () => { if (!document.hidden) refresh(); };
    const timer = window.setInterval(refresh, 30_000);
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refreshIfVisible);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', refreshIfVisible);
    };
  }, [timeMode, editing]);

  function changeDraft<Key extends keyof JournalDraft>(key: Key, value: JournalDraft[Key]) {
    setDraft(current => ({ ...current, [key]: value }));
    setNotice('');
  }

  function confirmDiscardDraft(): boolean {
    const hasText = Boolean(draft.title || draft.body || Object.keys(detailLabels).some(key => String(draft[key as keyof JournalDraft] || '').trim()));
    return !(editing || hasText) || window.confirm('当前记录草稿尚未保存，确定放弃这些内容？');
  }

  function selectKind(kind: JournalKind, force = false): boolean {
    if (kind === draft.kind && !force || !confirmDiscardDraft()) return false;
    setEditing(null);
    const next = blankDraft(timezone, kind);
    if (kind === 'weekly_review') next.occurred_at = weeklyOccurrence(reviewWeek, timezone);
    setDraft(next);
    setTimeMode(kind === 'weekly_review' ? 'week' : 'now');
    setError(''); setNotice('');
    return true;
  }

  function startReview(kind: 'daily_review' | 'weekly_review') {
    if (!selectKind(kind, true)) return;
    setShowEditor(true);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const contentFields: Partial<Record<JournalKind, Array<keyof JournalDraft>>> = {
      ai_storage: ['question', 'explanation', 'source', 'uncertainty', 'next_check'],
      paper: ['question', 'explanation', 'source', 'uncertainty', 'next_check'],
      ielts: ['material', 'raw_result', 'correction', 'next_check'],
      thought: ['previous_view', 'trigger_evidence', 'current_view', 'next_check'],
      daily_review: ['learning_state', 'explanation', 'evidence', 'takeaway', 'next_check'],
      weekly_review: ['evidence', 'takeaway', 'next_check'],
    };
    if (!draft.body.trim() && !(contentFields[draft.kind] || []).some(field => String(draft[field] || '').trim())) { setError('请填写正文或至少一项结构化内容。'); return; }
    setBusy(true); setError(''); setNotice('');
    try {
      const submitted = !editing && timeMode === 'now' ? { ...draft, occurred_at: new Date().toISOString() } : draft;
      const result = editing
        ? await updateJournal(editing.id, submitted, editing.revision)
        : await createJournal(submitted);
      setEditing(null);
      setDraft(blankDraft(timezone, draft.kind));
      setTimeMode('now');
      onSelectDate(result.item.local_date);
      setNotice('记录已保存。');
      setShowEditor(false);
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存失败；草稿已保留。');
    } finally { setBusy(false); }
  }

  function edit(item: JournalRecord) {
    if (!confirmDiscardDraft()) return;
    const { id: _id, revision: _revision, local_date: _localDate, created_at: _createdAt, updated_at: _updatedAt, chat_source: _chatSource, ...values } = item;
    setDraft(values); setEditing(item); setError(''); setNotice('');
    setTimeMode('manual');
    setShowEditor(true);
  }

  async function remove(item: JournalRecord) {
    if (!window.confirm(`确定删除「${item.title || labels[item.kind]}」？删除后无法恢复。`)) return;
    setBusy(true); setError('');
    try {
      await deleteJournal(item.id, item.revision);
      setNotice('记录已删除。');
      await reload();
    } catch (cause) { setError(cause instanceof Error ? cause.message : '删除失败'); }
    finally { setBusy(false); }
  }

  function textField(label: string, key: keyof JournalDraft, rows = 2): ReactElement {
    return <label className="journal-field" key={key}>{label}<textarea value={String(draft[key] ?? '')} onChange={event => changeDraft(key, event.target.value as never)} maxLength={fieldLimits[key]} rows={rows} /></label>;
  }

  const structured = draft.kind === 'paper' || draft.kind === 'ai_storage'
    ? <div className="journal-fields">{textField('研究问题', 'question')}{textField('自己的解释', 'explanation')}{textField('依据或来源', 'source')}{textField('仍有疑点', 'uncertainty')}{textField('下次验证', 'next_check')}</div>
    : draft.kind === 'ielts'
      ? <div className="journal-fields"><label className="journal-field">练习项目<select value={draft.skill || ''} onChange={event => changeDraft('skill', event.target.value ? event.target.value as JournalDraft['skill'] : null)}><option value="">可选</option><option value="listening">听力</option><option value="speaking">口语</option><option value="reading">阅读</option><option value="writing">写作</option></select></label>{textField('练习材料', 'material')}{textField('原始输出或结果', 'raw_result')}{textField('纠错', 'correction')}{textField('下一步', 'next_check')}</div>
      : draft.kind === 'thought'
        ? <div className="journal-fields">{textField('原先认为', 'previous_view')}{textField('触发证据', 'trigger_evidence')}{textField('现在认为', 'current_view')}{textField('待验证', 'next_check')}</div>
        : draft.kind === 'daily_review'
          ? <div className="journal-fields">{textField('学习状态', 'learning_state')}{textField('自己的解释', 'explanation')}{textField('支持这个判断的证据', 'evidence')}{textField('今天的收获', 'takeaway')}{textField('下一步验证', 'next_check')}</div>
          : draft.kind === 'weekly_review'
            ? <div className="journal-fields">{textField('本周收获', 'takeaway')}{textField('支持判断的证据', 'evidence')}{textField('下周验证', 'next_check')}</div>
            : null;

  return <JournalDrawer open={panel !== null} onClose={onClose} viewKey={`${panel}-${showEditor}`} title={showEditor ? (editing ? '编辑记录' : labels[draft.kind]) : panel === 'review' ? '回顾' : '生活与学习时间线'}
    tools={!showEditor && panel === 'timeline' ? <div className="journal-filters"><label className="journal-search">搜索<input aria-label="搜索记录" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="标题、正文或结构化内容" /></label><label>查看日期<input aria-label="记录日期" type="date" value={selectedDate} onChange={event => onSelectDate(event.target.value)} /></label><label>分类<select aria-label="筛选分类" value={kindFilter} onChange={event => setKindFilter(event.target.value)}><option value="">全部</option>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div> : null}
    footer={!showEditor ? <><p className="journal-privacy-note">记录存于本机私人空间；正文不会发送给 AI 服务。</p><div className="journal-drawer-footer-actions">{panel === 'timeline' ? <button className="btn btn-ghost btn-sm journal-export" type="button" onClick={() => void downloadJournal().catch(cause => setError(cause instanceof Error ? cause.message : '导出失败'))}>导出 JSON</button> : null}<button ref={composeRef} className="btn btn-primary" type="button" onClick={() => setShowEditor(true)}>{hasDraftContent ? '继续填写' : '写记录'}</button></div></> : null}>
    <div className="journal-page">
    {showEditor ? <div className="journal-drawer-toolbar"><button className="btn btn-secondary btn-sm" type="button" onClick={() => setShowEditor(false)}>← 返回{panel === 'review' ? '回顾' : '时间线'}</button><span>关闭面板后，草稿会保留在当前页面。</span></div> : null}
    {error ? <p className="journal-message journal-error" role="alert">{error}</p> : null}
    {notice ? <p className="journal-message" role="status">{notice}</p> : null}
    <section className="daily-timeline-section" aria-label="时间线记录" hidden={showEditor || panel !== 'timeline'}>
    <div className="journal-timeline-primary">
      {query || kindFilter ? <p className="journal-filter-note">搜索范围为全部日期；清空搜索与分类后按日期查看。</p> : null}
      {loading ? <p>正在读取记录…</p> : readFailed ? <div className="journal-read-failed"><p>当前筛选的记录暂时无法显示。</p><button className="btn btn-secondary" type="button" onClick={() => void reload()}>重试读取记录</button></div> : items.length ? <ol className="journal-timeline">{items.map(item => <li key={item.id}><article><header><div><time dateTime={item.occurred_at}>{new Intl.DateTimeFormat('zh-CN', { timeZone: item.timezone, dateStyle: 'medium', timeStyle: 'short' }).format(new Date(item.occurred_at))}</time><span className="journal-kind">{labels[item.kind]}</span></div><div><button className="btn btn-ghost btn-sm" type="button" onClick={() => edit(item)} aria-label={`编辑 ${item.title || labels[item.kind]}`} disabled={busy}>编辑</button><button className="btn btn-ghost btn-sm" type="button" onClick={() => void remove(item)} aria-label={`删除 ${item.title || labels[item.kind]}`} disabled={busy}>删除</button></div></header>{item.chat_source ? <a href={`/admin/chat?conversation=${encodeURIComponent(item.chat_source.conversation_id)}`}>来自 AI 对话：{item.chat_source.title}</a> : null}{item.title ? <h3>{item.title}</h3> : null}{item.body ? <p>{item.body}</p> : null}{recordDetails(item).length ? <dl className="journal-details">{recordDetails(item).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl> : null}{item.ended_at ? <small>结束：{new Intl.DateTimeFormat('zh-CN', { timeZone: item.timezone, timeStyle: 'short' }).format(new Date(item.ended_at))}</small> : null}</article></li>)}</ol> : <p className="journal-empty">这一天暂无记录。可以先记下一件小事。</p>}
    </div>
    </section>
    <form className="journal-editor" hidden={!showEditor} onSubmit={event => void save(event)}>
      <fieldset className="journal-editor-fieldset" disabled={busy}>
      <div className="journal-editor-heading"><h3>{editing ? '编辑记录' : draft.kind === 'daily_review' ? '写每日复盘' : draft.kind === 'weekly_review' ? '写每周回顾' : '快速记录'}</h3>{editing ? <button className="btn btn-ghost btn-sm" type="button" onClick={() => { if (confirmDiscardDraft()) { setEditing(null); setDraft(blankDraft(timezone)); setTimeMode('now'); } }}>取消编辑</button> : null}</div>
      <div className="journal-core-fields"><label className="journal-field">记录类型<select ref={editorKindRef} aria-label="记录类型" value={draft.kind} onChange={event => selectKind(event.target.value as JournalKind)}>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="journal-field">开始时间<input aria-label="开始时间" type="datetime-local" value={localInput(draft.occurred_at)} onFocus={() => { if (timeMode === 'now') setTimeMode('manual'); }} onChange={event => { const value = new Date(event.target.value); if (Number.isFinite(value.getTime())) { changeDraft('occurred_at', value.toISOString()); setTimeMode('manual'); } }} required />{timeMode === 'now' && !editing ? <small>自动使用保存时的当前时间</small> : null}</label>
      </div>
      <label className="journal-field journal-body">内容 <span>写下实际发生的事、你的理解或感受即可；也可只填写下方提示。</span><textarea aria-label="记录内容" value={draft.body} onChange={event => changeDraft('body', event.target.value)} rows={4} maxLength={12000} /></label>
      <details className="journal-extra-fields"><summary>更多记录选项</summary><div className="journal-extra-fields-content"><label className="journal-field">结束时间 <small>可选</small><input aria-label="结束时间" type="datetime-local" value={draft.ended_at ? localInput(draft.ended_at) : ''} onChange={event => { const value = event.target.value ? new Date(event.target.value) : null; changeDraft('ended_at', value && Number.isFinite(value.getTime()) ? value.toISOString() : null); }} /></label>
        <label className="journal-field">标题 <small>可选</small><input aria-label="记录标题" value={draft.title} onChange={event => changeDraft('title', event.target.value)} maxLength={160} /></label></div></details>
      {structured ? <details className="journal-optional" open={draft.kind !== 'life'}><summary>结构化提示（可选）</summary>{structured}</details> : null}
      <div className="journal-editor-actions"><button className="btn btn-primary" type="submit" disabled={busy}>{busy ? '保存中…' : editing ? '保存修改' : '保存记录'}</button><span>时间与至少一项内容必填。</span></div>
      </fieldset>
    </form>
    <section className="daily-review-section" aria-label="每周回顾" hidden={showEditor || panel !== 'review'}>
      <div className="journal-review"><div className="journal-review-actions"><button className="btn btn-secondary" type="button" onClick={() => startReview('daily_review')} disabled={busy}>写每日复盘</button><button className="btn btn-secondary" type="button" onClick={() => startReview('weekly_review')} disabled={busy}>写每周回顾</button></div><div className="journal-week-nav"><button className="btn btn-ghost" type="button" onClick={() => setReviewWeek(day => shiftWeek(day, -1))}>上周</button><strong>{reviewWeek} — {weekEnd(reviewWeek)}</strong><button className="btn btn-ghost" type="button" onClick={() => setReviewWeek(day => shiftWeek(day, 1))}>下周</button></div><h3>本周记录</h3>{summaryErrorWeek === reviewWeek ? <p className="journal-message journal-error" role="alert">本周记录读取失败</p> : visibleSummary ? <><p>共 {visibleSummary.total} 条；只统计记录数量，不把篇数当作学习进步。</p><ul className="journal-counts">{Object.entries(visibleSummary.counts).map(([kind, count]) => <li key={kind}>{labels[kind as JournalKind]}：{count}</li>)}</ul><ul className="journal-review-list">{visibleSummary.items.map(item => <li key={item.id}><span>{item.local_date} · {labels[item.kind]}</span><strong>{recordSummary(item)}</strong></li>)}</ul></> : <p>正在读取本周记录…</p>}</div>
    </section>
  </div></JournalDrawer>;
}
