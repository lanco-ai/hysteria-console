import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type ReactElement } from 'react';
import { loadSchedule, saveScheduleDay, saveScheduleRoutine, type ScheduleCategory, type ScheduleDay, type ScheduleRoutine, type ScheduleView } from './scheduleApi';
import {
  allocation, categories, categoryLabel, containsMinute, dayEntries, formatDuration, formatHours, newBlockId, overlapsWith, placeSegments,
  presets, repeatLabel, repeatModeFor, repeatWeekdays, routineEntry, spanMinutes, suggestedRoutine, toClock, toMinutes, weekdayLabels, weekdayOf,
  withoutStaleMarks, type BlockPreset, type RepeatMode, type ScheduleEntry, type TaskMarker,
} from './scheduleModel';

const PX_PER_MINUTE = 1;
const focusCategories: ScheduleCategory[] = ['work', 'study', 'think'];

type BlockForm = {
  editingKey: string;
  title: string;
  category: ScheduleCategory;
  start: string;
  end: string;
  repeat: RepeatMode;
  weekdays: number[];
  notes: string;
};

const blankForm = (start = '09:00', end = '10:00'): BlockForm => ({ editingKey: '', title: '', category: 'work', start, end, repeat: 'once', weekdays: [], notes: '' });

function formFromEntry(entry: ScheduleEntry): BlockForm {
  return {
    editingKey: entry.key, title: entry.title, category: entry.category || 'work', start: entry.start, end: entry.end,
    repeat: entry.kind === 'routine' ? repeatModeFor(entry.weekdays) : 'once', weekdays: entry.weekdays, notes: entry.notes,
  };
}

function minuteNow(): number {
  const now = new Date();
  return now.getHours() * 60 + now.getMinutes();
}

function hoursInput(minutes: number | undefined): string {
  return minutes ? String(Math.round(minutes / 15) / 4) : '';
}

export function SchedulePanel({ mode, selectedDate, today, tasks, onProtectionChange }: {
  mode: 'full' | 'strip';
  selectedDate: string;
  today: string;
  tasks: TaskMarker[];
  onProtectionChange: (protectedDraft: boolean) => void;
}): ReactElement {
  const [view, setView] = useState<ScheduleView | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [nowMinute, setNowMinute] = useState(minuteNow);
  const [form, setForm] = useState<BlockForm>(() => blankForm());
  const [formBaseline, setFormBaseline] = useState<BlockForm>(() => blankForm());
  const [noteDraft, setNoteDraft] = useState<string | null>(null);
  const [targetDraft, setTargetDraft] = useState<Partial<Record<ScheduleCategory, string>> | null>(null);
  const dateRef = useRef(selectedDate);
  dateRef.current = selectedDate;
  const busyRef = useRef(false);
  const timelineRef = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLInputElement>(null);
  const loadGeneration = useRef(0);

  const reload = useCallback(async (signal?: AbortSignal) => {
    const generation = ++loadGeneration.current;
    const requested = dateRef.current;
    setLoading(true);
    try {
      const next = await loadSchedule(requested, signal);
      if (signal?.aborted || generation !== loadGeneration.current) return;
      setView(next); setLoadFailed(false); setError('');
    } catch (cause) {
      if (signal?.aborted || generation !== loadGeneration.current) return;
      setLoadFailed(true);
      setError(cause instanceof Error ? cause.message : '行程读取失败');
    } finally {
      if (!signal?.aborted && generation === loadGeneration.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setFeedback(''); setNoteDraft(null);
    setForm(current => current.editingKey ? blankForm() : current);
    setFormBaseline(current => current.editingKey ? blankForm() : current);
    void reload(controller.signal);
    return () => controller.abort();
  }, [selectedDate, reload]);

  useEffect(() => {
    const timer = window.setInterval(() => setNowMinute(minuteNow()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const current = view && view.date === selectedDate ? view : null;
  const routine: ScheduleRoutine = current?.routine || { blocks: [], targets: {} };
  const day: ScheduleDay = current?.day || { blocks: [], routine_status: {}, note: '' };
  const entries = useMemo(() => current ? dayEntries(selectedDate, current.routine, current.day, tasks) : [], [current, selectedDate, tasks]);
  const ownEntries = entries.filter(entry => entry.kind !== 'task');
  const activeEntries = ownEntries.filter(entry => entry.status !== 'skipped');
  const doneCount = activeEntries.filter(entry => entry.status === 'done').length;
  const totals = useMemo(() => allocation(entries), [entries]);
  const plannedMinutes = Math.min(1440, Object.values(totals).reduce((sum, value) => sum + value, 0));
  const focusMinutes = focusCategories.reduce((sum, id) => sum + totals[id], 0);
  const isToday = selectedDate === today;
  const timed = entries.filter(entry => entry.status !== 'skipped');
  const nowEntry = isToday ? [...timed].filter(entry => containsMinute(entry, nowMinute))
    .sort((a, b) => spanMinutes(a.start, a.end) - spanMinutes(b.start, b.end))[0] : undefined;
  const nextEntry = isToday ? timed.find(entry => toMinutes(entry.start) > nowMinute && entry !== nowEntry) : undefined;
  const formDirty = JSON.stringify(form) !== JSON.stringify(formBaseline);
  const noteDirty = noteDraft !== null && noteDraft !== day.note;
  const targetsDirty = targetDraft !== null;

  useLayoutEffect(() => { onProtectionChange(formDirty || noteDirty || targetsDirty || busy); }, [formDirty, noteDirty, targetsDirty, busy, onProtectionChange]);

  // Bring the useful part of the day into view: now on today, otherwise the first block.
  const firstStart = entries.length ? Math.min(...entries.map(entry => toMinutes(entry.start))) : 7 * 60;
  useEffect(() => {
    if (mode !== 'full' || !current || !timelineRef.current) return;
    const anchor = isToday ? nowMinute - 90 : firstStart - 30;
    timelineRef.current.scrollTop = Math.max(0, anchor) * PX_PER_MINUTE;
    // Only on a new day or when the view opens, not on every tick or edit.
  }, [mode, current?.date]);

  // The change shows at once and is rolled back if the server refuses it.
  const commit = async (optimistic: (base: ScheduleView) => ScheduleView, work: (base: ScheduleView) => Promise<ScheduleView>, success: string) => {
    if (!current || busyRef.current) return false;
    busyRef.current = true;
    const base = current;
    setBusy(true); setError(''); setFeedback('');
    setView(optimistic(base));
    try {
      const next = await work(base);
      if (next.date === dateRef.current) setView(next); else void reload();
      setFeedback(success);
      return true;
    } catch (cause) {
      setView(latest => latest && latest.date === base.date ? base : latest);
      setError(cause instanceof Error ? cause.message : '行程保存失败');
      return false;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };
  const saveDay = (next: ScheduleDay, success: string) => commit(
    base => ({ ...base, day: next }),
    base => saveScheduleDay(base.date, withoutStaleMarks(next, base.routine), base.day_revision), success,
  );
  const saveRoutine = (next: ScheduleRoutine, success: string) => commit(
    base => ({ ...base, routine: next }),
    base => saveScheduleRoutine(base.date, next, base.routine_revision), success,
  );

  const resetForm = (next = blankForm()) => { setForm(next); setFormBaseline(next); };
  const editEntry = (entry: ScheduleEntry) => {
    if (entry.kind === 'task') return;
    if (formDirty && form.editingKey !== entry.key && !window.confirm('当前表单尚未保存，放弃并编辑这一项？')) return;
    resetForm(formFromEntry(entry));
    requestAnimationFrame(() => titleRef.current?.focus({ preventScroll: false }));
  };
  const applyPreset = (preset: BlockPreset) => {
    setForm(currentForm => ({ ...currentForm, title: preset.title, category: preset.category, start: preset.start, end: preset.end }));
    titleRef.current?.focus();
  };
  const startAt = (minute: number) => {
    if (formDirty && !window.confirm('当前表单尚未保存，放弃并从这个时间新建？')) return;
    const start = Math.floor(minute / 15) * 15;
    const next = { ...blankForm(toClock(start), toClock(Math.min(start + 60, 1439))), category: form.category };
    resetForm(next);
    titleRef.current?.focus();
  };

  // A routine block edited from the template list may not occur on the selected weekday.
  const editingRoutine = form.editingKey.startsWith('routine:') ? routine.blocks.find(block => `routine:${block.id}` === form.editingKey) : undefined;
  const editingEntry = form.editingKey ? entries.find(entry => entry.key === form.editingKey) || (editingRoutine ? routineEntry(editingRoutine) : undefined) : undefined;
  const formWeekdays = form.repeat === 'once' ? [] : form.repeat === 'custom' ? form.weekdays : repeatWeekdays[form.repeat];
  const formValid = Boolean(form.title.trim()) && form.start !== form.end && (form.repeat === 'once' || formWeekdays.length > 0);
  const appliesToday = form.repeat === 'once' || formWeekdays.includes(weekdayOf(selectedDate));
  const conflicts = form.start && form.end && form.start !== form.end && appliesToday ? overlapsWith(form, entries, form.editingKey) : [];

  const submitForm = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!formValid || busy || !current) return;
    const block = { title: form.title.trim(), category: form.category, start: form.start, end: form.end };
    const editingKind = editingEntry?.kind;
    let saved: boolean;
    if (form.repeat === 'once') {
      const id = editingKind === 'day' ? editingEntry!.id : newBlockId();
      const existing = day.blocks.find(item => item.id === id);
      const nextBlock = { ...block, id, notes: form.notes.trim(), status: existing?.status || 'planned' as const };
      saved = await saveDay({ ...day, blocks: existing ? day.blocks.map(item => item.id === id ? nextBlock : item) : [...day.blocks, nextBlock] },
        existing ? `已更新「${block.title}」` : `已加入这一天：${block.start} ${block.title}`);
    } else {
      const id = editingKind === 'routine' ? editingEntry!.id : newBlockId();
      const exists = routine.blocks.some(item => item.id === id);
      const nextBlock = { ...block, id, weekdays: formWeekdays };
      saved = await saveRoutine({ ...routine, blocks: exists ? routine.blocks.map(item => item.id === id ? nextBlock : item) : [...routine.blocks, nextBlock] },
        exists ? `已更新作息模板「${block.title}」` : `已加入作息模板（${repeatLabel(formWeekdays)}）：${block.start} ${block.title}`);
    }
    if (saved) resetForm();
  };

  const removeEntry = async (entry: ScheduleEntry) => {
    if (entry.kind === 'routine') {
      if (!window.confirm(`从作息模板删除「${entry.title}」？所有${repeatLabel(entry.weekdays)}都不再显示它。只想今天不做，可以选择“跳过”。`)) return;
      if (await saveRoutine({ ...routine, blocks: routine.blocks.filter(item => item.id !== entry.id) }, `已从作息模板删除「${entry.title}」`) && form.editingKey === entry.key) resetForm();
    } else if (entry.kind === 'day') {
      if (!window.confirm(`删除「${entry.title}」？`)) return;
      if (await saveDay({ ...day, blocks: day.blocks.filter(item => item.id !== entry.id) }, `已删除「${entry.title}」`) && form.editingKey === entry.key) resetForm();
    }
  };

  const setStatus = (entry: ScheduleEntry, status: 'planned' | 'done' | 'skipped') => {
    if (entry.kind === 'routine') {
      const marks = { ...day.routine_status };
      if (status === 'planned') delete marks[entry.id]; else marks[entry.id] = status;
      void saveDay({ ...day, routine_status: marks }, status === 'done' ? `完成：${entry.title}` : status === 'skipped' ? `今天跳过：${entry.title}` : `已恢复：${entry.title}`);
    } else if (entry.kind === 'day') {
      void saveDay({ ...day, blocks: day.blocks.map(item => item.id === entry.id ? { ...item, status } : item) },
        status === 'done' ? `完成：${entry.title}` : status === 'skipped' ? `已跳过：${entry.title}` : `已恢复：${entry.title}`);
    }
  };

  const applySuggestedRoutine = () => {
    if (routine.blocks.length && !window.confirm('用推荐作息替换现有作息模板？每天的单独安排不受影响。')) return;
    void saveRoutine(suggestedRoutine(), '已套用推荐作息；可以逐条修改成你自己的节奏');
  };

  const saveNote = () => {
    if (noteDraft === null || !noteDirty) return;
    void saveDay({ ...day, note: noteDraft.trim() }, '已保存今日复盘').then(saved => { if (saved) setNoteDraft(null); });
  };

  const targetValues = targetDraft || Object.fromEntries(categories.map(item => [item.id, hoursInput(routine.targets[item.id])]));
  const targetMinutes = Object.fromEntries(categories.map(item => [item.id, Math.round((Number(targetValues[item.id]) || 0) * 60)])) as Record<ScheduleCategory, number>;
  const targetTotal = Object.values(targetMinutes).reduce((sum, value) => sum + value, 0);
  const saveTargets = () => {
    if (targetTotal > 1440) return;
    void saveRoutine({ ...routine, targets: Object.fromEntries(Object.entries(targetMinutes).filter(([, minutes]) => minutes > 0)) }, '已保存理想的一天')
      .then(saved => { if (saved) setTargetDraft(null); });
  };

  const entryTime = (entry: ScheduleEntry) => `${entry.start}–${entry.end}`;
  const statusNotice = error ? <div className="schedule-message is-error" role="alert"><span>{error}</span>{current || loadFailed ? <button className="btn btn-ghost btn-sm" type="button" onClick={() => void reload()} disabled={loading || busy}>重新读取</button> : null}</div>
    : feedback ? <div className="schedule-message" role="status">{feedback}</div> : null;

  if (mode === 'strip') {
    const summary = !current ? (loadFailed ? '行程暂时无法读取' : '正在读取行程…')
      : !ownEntries.length ? '还没有安排这一天：作息、三餐、工作、学习、思考与联系'
        : nowEntry ? `现在 · ${nowEntry.title} ${entryTime(nowEntry)}${nextEntry ? `　下一项 · ${nextEntry.start} ${nextEntry.title}` : ''}`
          : nextEntry ? `下一项 · ${nextEntry.start} ${nextEntry.title}`
            : `已安排 ${activeEntries.length} 项 · 完成 ${doneCount} 项 · 专注 ${formatHours(focusMinutes)}`;
    return <a className={`schedule-strip${nowEntry?.category ? ` schedule-cat-${nowEntry.category}` : ''}`} href="#daily-schedule">
      <span className="schedule-strip-label">{isToday ? '今日行程' : '这一天的行程'}</span>
      <span className="schedule-strip-summary">{summary}</span>
      <span className="schedule-strip-action">进入行程 →</span>
    </a>;
  }

  const placed = placeSegments(entries);
  const hours = Array.from({ length: 25 }, (_, hour) => hour);
  const visibleCategories = categories.filter(item => totals[item.id] || routine.targets[item.id]);
  const routineByStart = [...routine.blocks].sort((a, b) => toMinutes(a.start) - toMinutes(b.start));
  const formDuration = form.start && form.end && form.start !== form.end ? formatDuration(spanMinutes(form.start, form.end)) : '';

  return <section className="schedule" id="daily-schedule-panel" aria-label="这一天的行程">
    <div className="schedule-overview">
      <article className={`schedule-card schedule-now${nowEntry?.category ? ` schedule-cat-${nowEntry.category}` : ''}`}>
        <p className="schedule-card-label">{isToday ? `现在 · ${toClock(nowMinute)}` : `星期${weekdayLabels[weekdayOf(selectedDate)]} · 这一天`}</p>
        {isToday && nowEntry ? <>
          <h3>{nowEntry.title}</h3>
          <p>{entryTime(nowEntry)} · {nowEntry.category ? categoryLabel[nowEntry.category] : '计划任务'}</p>
          {(() => {
            const total = spanMinutes(nowEntry.start, nowEntry.end);
            const elapsed = (nowMinute - toMinutes(nowEntry.start) + 1440) % 1440;
            return <><span className="schedule-meter" aria-hidden="true"><span style={{ width: `${Math.min(100, Math.round(elapsed / total * 100))}%` }} /></span><small>还剩 {formatDuration(Math.max(0, total - elapsed))}</small></>;
          })()}
        </> : isToday ? <><h3>{ownEntries.length ? '空白时段' : '还没有安排'}</h3><p>{ownEntries.length ? '留白也是安排：休息、散步，或处理一件小事。' : '先放进作息和三餐，再安排工作与学习。'}</p></>
          : <><h3>已安排 {activeEntries.length} 项</h3><p>完成 {doneCount} 项 · 专注 {formatHours(focusMinutes)}</p></>}
        {nextEntry ? <p className="schedule-next">下一项 <strong>{nextEntry.start} {nextEntry.title}</strong></p> : null}
      </article>
      <article className="schedule-card schedule-allocation">
        <p className="schedule-card-label">时间分配 · 已安排 {formatHours(plannedMinutes)} / 24h</p>
        <div className="schedule-allocation-bar" role="img" aria-label={visibleCategories.map(item => `${item.label} ${formatHours(totals[item.id])}`).join('，') || '尚未安排'}>
          {categories.map(item => totals[item.id] ? <span key={item.id} className={`schedule-cat-${item.id}`} style={{ flexGrow: totals[item.id] }} /> : null)}
          <span className="schedule-allocation-free" style={{ flexGrow: Math.max(0, 1440 - plannedMinutes) }} />
        </div>
        {visibleCategories.length ? <ul className="schedule-legend">{visibleCategories.map(item => {
          const target = routine.targets[item.id] || 0;
          const state = target ? (totals[item.id] >= target ? ' is-met' : ' is-short') : '';
          return <li key={item.id} className={`schedule-cat-${item.id}${state}`}><span className="schedule-dot" aria-hidden="true" />{item.label}<strong>{formatHours(totals[item.id])}</strong>{target ? <small>/ 目标 {formatHours(target)}</small> : null}</li>;
        })}</ul> : <p className="schedule-muted">安排之后，这里会显示每类时间的占比，并与理想的一天对比。</p>}
      </article>
      <article className="schedule-card schedule-progress">
        <p className="schedule-card-label">完成度</p>
        <h3>{doneCount} <small>/ {activeEntries.length}</small></h3>
        <span className="schedule-meter" aria-hidden="true"><span style={{ width: `${activeEntries.length ? Math.round(doneCount / activeEntries.length * 100) : 0}%` }} /></span>
        <p>专注 {formatHours(focusMinutes)} · 运动 {formatHours(totals.exercise)} · 联系 {formatHours(totals.connect)}</p>
      </article>
    </div>

    {statusNotice}

    <div className="schedule-main">
      <section className="schedule-timeline-card" aria-label="时间轴">
        <header><h3>时间轴</h3><small>点击空白处从该时间新建；点击色块修改</small></header>
        {!current ? <p className="schedule-muted schedule-timeline-state">{loadFailed ? '行程暂时无法读取。' : '正在读取行程…'}</p> : <div className="schedule-timeline" ref={timelineRef}>
          <div className="schedule-timeline-canvas" style={{ height: 1440 * PX_PER_MINUTE }}
            onClick={event => {
              if (event.target !== event.currentTarget) return;
              startAt((event.clientY - event.currentTarget.getBoundingClientRect().top) / PX_PER_MINUTE);
            }}>
            {hours.map(hour => <div key={hour} className="schedule-hour" style={{ top: hour * 60 * PX_PER_MINUTE }} aria-hidden="true"><span>{String(hour).padStart(2, '0')}:00</span></div>)}
            {placed.map(piece => {
              const { entry } = piece;
              const style = { top: piece.from * PX_PER_MINUTE, height: Math.max(14, (piece.to - piece.from) * PX_PER_MINUTE - 2), left: `calc(52px + (100% - 58px) * ${piece.lane / piece.lanes})`, width: `calc((100% - 58px) / ${piece.lanes} - 4px)` };
              const className = ['schedule-block', entry.category ? `schedule-cat-${entry.category}` : 'schedule-block-task', entry.status === 'done' ? 'is-done' : '', form.editingKey === entry.key ? 'is-selected' : '', piece.to - piece.from < 30 ? 'is-short' : ''].filter(Boolean).join(' ');
              const label = `${piece.continued ? '（续）' : ''}${entry.title}`;
              const body = <><strong>{label}</strong><span>{entryTime(entry)}{entry.kind === 'routine' ? ' · 模板' : entry.kind === 'task' ? ' · 任务' : ''}</span></>;
              return entry.kind === 'task'
                ? <div key={`${entry.key}:${piece.from}`} className={className} style={style} title={`计划任务：${entry.title} ${entryTime(entry)}`}>{body}</div>
                : <button key={`${entry.key}:${piece.from}`} type="button" className={className} style={style} aria-label={`修改 ${entry.title} ${entryTime(entry)}`} onClick={() => editEntry(entry)}>{body}</button>;
            })}
            {isToday ? <div className="schedule-now-line" style={{ top: nowMinute * PX_PER_MINUTE }} aria-hidden="true"><span>{toClock(nowMinute)}</span></div> : null}
          </div>
          {!ownEntries.length ? <div className="schedule-empty">
            <h4>规划你的一天</h4>
            <p>先用一份推荐作息打底：起床、三餐、工作、学习、运动、联系家人、复盘和睡觉，之后逐条改成自己的节奏。</p>
            <button className="btn btn-primary" type="button" onClick={applySuggestedRoutine} disabled={busy}>套用推荐作息</button>
            <button className="btn btn-ghost" type="button" onClick={() => titleRef.current?.focus()}>自己从零安排</button>
          </div> : null}
        </div>}
      </section>

      <div className="schedule-side">
        <form className="schedule-card schedule-form" onSubmit={event => void submitForm(event)}>
          <header><h3>{form.editingKey ? (editingEntry?.kind === 'routine' ? '修改作息模板' : '修改安排') : '添加安排'}</h3>{form.editingKey ? <button className="btn btn-ghost btn-sm" type="button" onClick={() => resetForm()}>取消</button> : null}</header>
          {!form.editingKey ? <div className="schedule-presets" aria-label="常用安排">{presets.map(preset => <button key={preset.title} type="button" className={`schedule-chip schedule-cat-${preset.category}`} onClick={() => applyPreset(preset)}>{preset.title}</button>)}</div> : null}
          <label className="schedule-field"><span>做什么</span><input ref={titleRef} value={form.title} onChange={event => setForm({ ...form, title: event.target.value })} maxLength={80} placeholder="例如：午餐、写方案、给爸妈打电话" required /></label>
          <fieldset className="schedule-categories"><legend>类别</legend>{categories.map(item => <label key={item.id} className={`schedule-chip schedule-cat-${item.id}${form.category === item.id ? ' is-active' : ''}`} title={item.hint}>
            <input type="radio" name="schedule-category" value={item.id} checked={form.category === item.id} onChange={() => setForm({ ...form, category: item.id })} />{item.label}
          </label>)}</fieldset>
          <div className="schedule-time-row">
            <label className="schedule-field"><span>开始</span><input type="time" aria-label="开始时间" value={form.start} step={300} onChange={event => setForm({ ...form, start: event.target.value })} required /></label>
            <label className="schedule-field"><span>结束</span><input type="time" aria-label="结束时间" value={form.end} step={300} onChange={event => setForm({ ...form, end: event.target.value })} required /></label>
            <label className="schedule-field"><span>重复</span><select aria-label="重复" value={form.repeat} onChange={event => setForm({ ...form, repeat: event.target.value as RepeatMode, weekdays: event.target.value === 'custom' ? (form.weekdays.length ? form.weekdays : [weekdayOf(selectedDate)]) : form.weekdays })} disabled={Boolean(editingEntry && editingEntry.kind === 'day')}>
              {!editingEntry || editingEntry.kind === 'day' ? <option value="once">仅这一天</option> : null}
              <option value="daily">每天（作息）</option><option value="weekdays">工作日</option><option value="weekend">周末</option><option value="custom">自定义</option>
            </select></label>
          </div>
          {form.repeat === 'custom' ? <div className="schedule-weekdays" role="group" aria-label="重复的星期">{weekdayLabels.map((label, index) => <label key={label} className={form.weekdays.includes(index) ? 'is-active' : ''}>
            <input type="checkbox" checked={form.weekdays.includes(index)} onChange={event => setForm({ ...form, weekdays: event.target.checked ? [...form.weekdays, index].sort() : form.weekdays.filter(day => day !== index) })} />{label}
          </label>)}</div> : null}
          {form.repeat === 'once' ? <label className="schedule-field"><span>备注</span><textarea value={form.notes} onChange={event => setForm({ ...form, notes: event.target.value })} maxLength={500} rows={2} placeholder="可选：地点、要带的东西、要联系谁" /></label> : null}
          <p className="schedule-form-hint">
            {formDuration ? <span>共 {formDuration}{toMinutes(form.end) <= toMinutes(form.start) ? '（跨过午夜）' : ''}</span> : null}
            {form.repeat !== 'once' ? <span>会出现在{repeatLabel(formWeekdays.length ? formWeekdays : [weekdayOf(selectedDate)])}的行程里。</span> : null}
            {editingEntry?.kind === 'routine' ? <span>只想改今天？先在列表中“跳过”它，再添加一条“仅这一天”的安排。</span> : null}
          </p>
          {conflicts.length ? <p className="schedule-form-warning" role="status">与{conflicts.slice(0, 3).map(item => `「${item.title}」`).join('')}时间重叠，仍可保存。</p> : null}
          <div className="schedule-form-actions">
            {editingEntry && editingEntry.kind !== 'task' ? <button className="btn btn-ghost schedule-danger" type="button" onClick={() => void removeEntry(editingEntry)} disabled={busy}>删除</button> : null}
            <button className="btn btn-primary" type="submit" disabled={!formValid || busy || !current}>{busy ? '保存中…' : form.editingKey ? '保存修改' : '添加'}</button>
          </div>
        </form>

        <section className="schedule-card schedule-agenda" aria-label="这一天的安排">
          <header><h3>这一天的安排</h3><small>{entries.length ? `${activeEntries.length} 项 · 完成 ${doneCount}` : ''}</small></header>
          {entries.length ? <ol>{entries.map(entry => <li key={entry.key} className={[entry.category ? `schedule-cat-${entry.category}` : 'schedule-agenda-task', entry.status === 'done' ? 'is-done' : '', entry.status === 'skipped' ? 'is-skipped' : '', isToday && nowEntry?.key === entry.key ? 'is-now' : ''].filter(Boolean).join(' ')}>
            <label className="schedule-agenda-check"><input type="checkbox" checked={entry.status === 'done'} disabled={entry.kind === 'task' || entry.status === 'skipped' || busy} onChange={event => setStatus(entry, event.target.checked ? 'done' : 'planned')} /><span className="sr-only">完成 {entry.title}</span></label>
            <time>{entry.start}<small>{entry.end}</small></time>
            <div className="schedule-agenda-body">
              <strong>{entry.title}</strong>
              <span>{entry.category ? categoryLabel[entry.category] : '计划任务'}{entry.kind === 'routine' ? ` · 作息模板（${repeatLabel(entry.weekdays)}）` : ''}{entry.kind === 'task' ? ' · 在“今日计划”中管理' : ''}{entry.status === 'skipped' ? ' · 已跳过' : ''}</span>
              {entry.notes ? <p>{entry.notes}</p> : null}
            </div>
            {entry.kind !== 'task' ? <div className="schedule-agenda-actions">
              {entry.status === 'skipped' ? <button className="btn btn-ghost btn-sm" type="button" onClick={() => setStatus(entry, 'planned')} disabled={busy}>恢复</button>
                : <button className="btn btn-ghost btn-sm" type="button" onClick={() => setStatus(entry, 'skipped')} disabled={busy} aria-label={`跳过 ${entry.title}`}>跳过</button>}
              <button className="btn btn-ghost btn-sm" type="button" onClick={() => editEntry(entry)} aria-label={`编辑 ${entry.title}`}>编辑</button>
            </div> : null}
          </li>)}</ol> : <p className="schedule-muted">{current ? '还没有安排。可以从上方的常用安排开始。' : ''}</p>}
        </section>
      </div>
    </div>

    <div className="schedule-bottom">
      <section className="schedule-card schedule-note" aria-label="今日复盘">
        <header><h3>今日复盘</h3><small>精力、心情、收获，或明天想改变的一件事</small></header>
        <textarea aria-label="今日复盘" value={noteDraft ?? day.note} onChange={event => setNoteDraft(event.target.value)} onBlur={saveNote} maxLength={2000} rows={4} placeholder="今天做成了什么？哪段时间最有精力？哪里被打断了？" disabled={!current} />
        <div className="schedule-note-actions">{noteDirty ? <small>未保存 · 离开输入框时自动保存</small> : null}<button className="btn btn-secondary btn-sm" type="button" onClick={saveNote} disabled={!noteDirty || busy}>保存复盘</button></div>
      </section>
      <details className="schedule-card schedule-targets">
        <summary><h3>理想的一天</h3><small>为每类时间设定每日目标，时间分配会与它对比</small></summary>
        <div className="schedule-target-grid">{categories.map(item => <label key={item.id} className={`schedule-cat-${item.id}`}><span><span className="schedule-dot" aria-hidden="true" />{item.label}</span>
          <input type="number" aria-label={`${item.label}目标小时`} min={0} max={24} step={0.25} value={targetValues[item.id] ?? ''} placeholder="0" onChange={event => setTargetDraft({ ...targetValues, [item.id]: event.target.value })} /><small>小时</small>
        </label>)}</div>
        <div className="schedule-targets-actions"><small className={targetTotal > 1440 ? 'is-error' : ''}>合计 {formatHours(targetTotal)}{targetTotal > 1440 ? ' · 超过 24 小时' : ''}</small>
          {targetsDirty ? <button className="btn btn-ghost btn-sm" type="button" onClick={() => setTargetDraft(null)}>还原</button> : null}
          <button className="btn btn-secondary btn-sm" type="button" onClick={saveTargets} disabled={!targetsDirty || busy || targetTotal > 1440}>保存目标</button></div>
      </details>
      <details className="schedule-card schedule-routine">
        <summary><h3>作息模板</h3><small>{routine.blocks.length ? `${routine.blocks.length} 条重复安排，按星期自动出现在每天` : '每天重复的作息、三餐和固定时段'}</small></summary>
        {routineByStart.length ? <ul>{routineByStart.map(block => <li key={block.id} className={`schedule-cat-${block.category}`}>
          <span className="schedule-dot" aria-hidden="true" /><time>{block.start}–{block.end}</time><strong>{block.title}</strong><small>{repeatLabel(block.weekdays)}</small>
          <button className="btn btn-ghost btn-sm" type="button" aria-label={`编辑模板 ${block.title}`} onClick={() => editEntry(routineEntry(block))}>编辑</button>
        </li>)}</ul> : <p className="schedule-muted">还没有作息模板。</p>}
        <button className="btn btn-ghost btn-sm" type="button" onClick={applySuggestedRoutine} disabled={busy || !current}>{routine.blocks.length ? '用推荐作息替换' : '套用推荐作息'}</button>
      </details>
    </div>
  </section>;
}
