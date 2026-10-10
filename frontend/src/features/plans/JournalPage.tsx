import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import { JournalInlineEditor } from './JournalInlineEditor';
import { useJournalAutosave } from './useJournalAutosave';
import { journalKindLabels as labels } from './journalLabels';
import { deleteJournal, downloadJournal, listJournal, loadJournalSummary, type JournalDraft, type JournalKind, type JournalRecord, type JournalSummary } from './journalApi';

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

function weekStart(day: string): string {
  const date = new Date(`${day}T12:00:00Z`);
  const weekday = date.getUTCDay();
  date.setUTCDate(date.getUTCDate() - (weekday === 0 ? 6 : weekday - 1));
  return date.toISOString().slice(0, 10);
}

function shiftDay(day: string, days: number): string {
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

const weekdayLabels = ['一', '二', '三', '四', '五', '六', '日'];
const dayFormat = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long', timeZone: 'UTC' });
const formatDay = (day: string) => dayFormat.format(new Date(`${day}T12:00:00Z`));

export function JournalPage({ visible, selectedDate, today, timezone, onSelectDate, onDraftProtectionChange }: {
  /** False while the plan view is shown; the records stay mounted so drafts survive switching views. */
  visible: boolean;
  selectedDate: string; today: string; timezone: string;
  /** The page date: the week bar and the weekly list pick a day, the week buttons move by seven days. */
  onSelectDate: (day: string) => void;
  onDraftProtectionChange: (protectedDraft: boolean) => void;
}): ReactElement {
  const [items, setItems] = useState<JournalRecord[]>([]);
  const [kindFilter, setKindFilter] = useState('');
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [readFailed, setReadFailed] = useState(false);
  const [error, setError] = useState('');
  const [summary, setSummary] = useState<JournalSummary | null>(null);
  const [summaryErrorWeek, setSummaryErrorWeek] = useState<string | null>(null);
  // Bumped by saves and deletes: the week's summary is re-read after a change, not after every day's read.
  const [changes, setChanges] = useState(0);
  const reviewWeek = weekStart(selectedDate);
  const readGeneration = useRef(0);
  const editorRef = useRef<HTMLTextAreaElement>(null);
  const reviewEditorRef = useRef<HTMLTextAreaElement>(null);
  const reviewRef = useRef<HTMLElement>(null);
  const deepLinkHandled = useRef(false);

  const visibleSummary = summary?.week_start === reviewWeek ? summary : null;
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

  // An older link to 回顾 opens this view. When the columns stack the review is below the day's records,
  // so scroll to it once they have loaded and no longer move it.
  useEffect(() => {
    if (deepLinkHandled.current || loading || !visible) return;
    deepLinkHandled.current = true;
    const target = reviewRef.current;
    if (window.location.hash === '#daily-review' && target && target.getBoundingClientRect().top > window.innerHeight * 0.6) target.scrollIntoView();
  }, [loading, visible]);

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
  }, [reviewWeek, changes]);

  // The day's records and the weekly review sit side by side, so each has its own writer and draft.
  const timelineContext = useMemo(() => ({ date: selectedDate, kind: (kindFilter || 'life') as JournalKind, timezone, weekly: false }), [selectedDate, kindFilter, timezone]);
  const reviewContext = useMemo(() => ({ date: reviewWeek, kind: 'weekly_review' as const, timezone, weekly: true }), [reviewWeek, timezone]);
  const recordsChanged = () => { setChanges(value => value + 1); void reload(); };
  const writer = useJournalAutosave(timelineContext, recordsChanged);
  const reviewWriter = useJournalAutosave(reviewContext, recordsChanged);
  const writingBusy = writer.state === 'saving';
  const reviewBusy = reviewWriter.state === 'saving';
  useLayoutEffect(() => { onDraftProtectionChange(writer.dirty || writingBusy || reviewWriter.dirty || reviewBusy); },
    [writer.dirty, writingBusy, reviewWriter.dirty, reviewBusy, onDraftProtectionChange]);

  function edit(item: JournalRecord) {
    if (writingBusy) return;
    if (writer.dirty && !window.confirm('当前文字尚未保存，确定放弃并编辑这条记录？')) return;
    if (writer.reset(item, true)) requestAnimationFrame(() => {
      editorRef.current?.scrollIntoView({ block: 'center', behavior: 'instant' });
      editorRef.current?.focus({ preventScroll: true });
    });
  }

  async function remove(item: JournalRecord) {
    if (!window.confirm(`确定删除「${item.title || labels[item.kind]}」？删除后无法恢复。`)) return;
    setBusy(true); setError('');
    try { await deleteJournal(item.id, item.revision); setChanges(value => value + 1); await reload(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '删除失败'); }
    finally { setBusy(false); }
  }

  // A record open in either writer is shown there, not again in the list.
  const visibleItems = items.filter(item => item.id !== writer.record?.id && item.id !== reviewWriter.record?.id);
  const searching = Boolean(query || kindFilter);
  const weekDays = Array.from({ length: 7 }, (_, index) => shiftDay(reviewWeek, index));
  const dayCounts = visibleSummary ? visibleSummary.items.reduce((counts, item) => counts.set(item.local_date, (counts.get(item.local_date) || 0) + 1), new Map<string, number>()) : null;
  return <div className="journal-board" hidden={!visible}>
    <nav className="journal-week-nav" aria-label="这一周的记录">
      <div className="journal-week-title"><span>{weekDays.includes(today) ? '本周' : '这一周'}</span><strong>{reviewWeek} — {weekEnd(reviewWeek)}</strong></div>
      <div className="journal-week-days">{weekDays.map((day, index) => {
        const count = dayCounts?.get(day) || 0;
        return <button key={day} type="button" className={`journal-day${day === today ? ' is-today' : ''}`} aria-pressed={day === selectedDate}
          aria-label={`${formatDay(day)}${day === today ? '，今天' : ''}${dayCounts ? `，${count} 条记录` : ''}`} onClick={() => onSelectDate(day)}>
          <span>周{weekdayLabels[index]}</span><b>{Number(day.slice(8))}</b><small>{dayCounts ? (count ? `${count} 条` : '·') : ''}</small>
        </button>;
      })}</div>
      <div className="journal-week-step"><button className="btn btn-ghost btn-sm" type="button" onClick={() => onSelectDate(shiftDay(selectedDate, -7))}>上周</button><button className="btn btn-ghost btn-sm" type="button" onClick={() => onSelectDate(shiftDay(selectedDate, 7))}>下周</button></div>
    </nav>
    <section className="daily-timeline-section" aria-labelledby="journal-timeline-title">
      <header className="daily-column-head"><h3 id="journal-timeline-title">这一天的记录</h3><small>{searching ? '搜索全部日期' : `${formatDay(selectedDate)}${loading || readFailed ? '' : ` · ${items.length} 条`}`}</small></header>
      <div className="journal-filters"><label className="journal-search">搜索<input aria-label="搜索记录" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="标题、正文或结构化内容" /></label><label>分类<select aria-label="筛选分类" value={kindFilter} onChange={event => setKindFilter(event.target.value)}><option value="">全部</option>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
      <div className="journal-timeline-primary">
        {searching ? <p className="journal-filter-note">搜索范围为全部日期；清空搜索与分类后按日期查看。</p> : null}
        <JournalInlineEditor writer={writer} context={timelineContext} inputRef={editorRef} empty={!loading && !readFailed && !items.length} onReload={() => void reload()}
          label="记录内容" statusId="journal-autosave-state" newLabel="另记一条"
          placeholder={{ empty: '这一天暂无记录。可以先记下一件小事。', more: '在这里记下一件事、一个想法，或今天的收获…' }} />
        {loading ? <p>正在读取记录…</p> : readFailed ? <div className="journal-read-failed"><p>当前筛选的记录暂时无法显示。</p><button className="btn btn-secondary" type="button" onClick={() => void reload()}>重试读取记录</button></div> : visibleItems.length ? <ol className="journal-timeline">{visibleItems.map(item => <li key={item.id}><article><header><div><time dateTime={item.occurred_at}>{new Intl.DateTimeFormat('zh-CN', { timeZone: item.timezone, dateStyle: 'medium', timeStyle: 'short' }).format(new Date(item.occurred_at))}</time><span className="journal-kind">{labels[item.kind]}</span></div><div><button className="btn btn-ghost btn-sm" type="button" onClick={() => edit(item)} aria-label={`编辑 ${item.title || labels[item.kind]}`} disabled={busy || writingBusy}>编辑</button><button className="btn btn-ghost btn-sm" type="button" onClick={() => void remove(item)} aria-label={`删除 ${item.title || labels[item.kind]}`} disabled={busy || writingBusy}>删除</button></div></header>{item.chat_source ? <a href={`/admin/chat?conversation=${encodeURIComponent(item.chat_source.conversation_id)}`}>来自 AI 对话：{item.chat_source.title}</a> : null}{item.title ? <h3>{item.title}</h3> : null}{item.body ? <p>{item.body}</p> : null}{recordDetails(item).length ? <dl className="journal-details">{recordDetails(item).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl> : null}{item.ended_at ? <small>结束：{new Intl.DateTimeFormat('zh-CN', { timeZone: item.timezone, timeStyle: 'short' }).format(new Date(item.ended_at))}</small> : null}</article></li>)}</ol> : searching ? <p className="journal-filter-note">没有其他符合筛选条件的记录。</p> : null}
      </div>
    </section>
    <section className="daily-review-section" aria-labelledby="journal-review-title" ref={reviewRef}>
      <div className="journal-review">
        <header className="daily-column-head"><h3 id="journal-review-title">本周回顾</h3><small>做成了什么、学到了什么、下周调整什么</small></header>
        <JournalInlineEditor writer={reviewWriter} context={reviewContext} inputRef={reviewEditorRef} empty={Boolean(visibleSummary && !visibleSummary.counts.weekly_review)} onReload={() => void reload()}
          label="本周回顾内容" statusId="journal-review-autosave-state" newLabel="另写一条回顾"
          placeholder={{ empty: '这一周还没有回顾。做成了什么、学到了什么、下周想调整什么？', more: '再补充一点这一周的回顾…' }} />
        <h4>本周记录</h4>
        {summaryErrorWeek === reviewWeek ? <p className="journal-message journal-error" role="alert">本周记录读取失败</p> : visibleSummary ? <><p>共 {visibleSummary.total} 条；只统计记录数量，不把篇数当作学习进步。</p><ul className="journal-counts">{Object.entries(visibleSummary.counts).map(([kind, count]) => <li key={kind}>{labels[kind as JournalKind]}：{count}</li>)}</ul><ul className="journal-review-list">{visibleSummary.items.map(item => <li key={item.id}><button type="button" onClick={() => onSelectDate(item.local_date)} aria-current={item.local_date === selectedDate ? 'date' : undefined}><span>{formatDay(item.local_date)} · {labels[item.kind]}</span><strong>{recordSummary(item)}</strong></button></li>)}</ul></> : <p>正在读取本周记录…</p>}
      </div>
    </section>
    {error ? <p className="journal-message journal-error journal-board-wide" role="alert">{error}</p> : null}
    <footer className="journal-footer journal-board-wide"><p className="journal-privacy-note">记录存于本机私人空间；正文不会发送给 AI 服务。</p><button className="btn btn-ghost btn-sm journal-export" type="button" onClick={() => void downloadJournal().catch(cause => setError(cause instanceof Error ? cause.message : '导出失败'))}>导出 JSON</button></footer>
  </div>;
}

function weekEnd(day: string): string {
  return shiftDay(day, 6);
}
