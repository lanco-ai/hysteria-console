import type { RefObject } from 'react';
import { journalKindLabels } from './journalLabels';
import type { useJournalAutosave, JournalContext } from './useJournalAutosave';

/** The direct writing box for the day's records, weekly reviews included. */
export function JournalInlineEditor({ writer, context, inputRef, empty, onReload, label, statusId, placeholder, newLabel }: {
  writer: ReturnType<typeof useJournalAutosave>; context: JournalContext;
  inputRef: RefObject<HTMLTextAreaElement | null>; empty: boolean; onReload: () => void;
  label: string; statusId: string; placeholder: { empty: string; more: string }; newLabel: string;
}) {
  // A draft keeps the day and category it was started with, even after the page's change.
  const otherDate = writer.bound.date !== context.date;
  const otherKind = writer.bound.kind !== context.kind;
  const differs = otherDate && otherKind ? '日期和分类' : otherDate ? '日期' : '分类';
  return <div className="journal-inline-editor">
    <textarea ref={inputRef} aria-label={label} aria-describedby={statusId} maxLength={12000}
      placeholder={empty ? placeholder.empty : placeholder.more}
      value={writer.body} onChange={event => writer.setBody(event.target.value)}
      onBlur={() => void writer.flush()} onCompositionStart={() => writer.composition(true)} onCompositionEnd={() => writer.composition(false)}
      onKeyDown={event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); void writer.flush(); } }} />
    <div className="journal-inline-state">
      <span id={statusId} role="status">{writer.state === 'saving' ? '正在保存…' : writer.state === 'saved' ? '已自动保存' : writer.state === 'error' ? '未保存，文字已保留' : writer.dirty ? '等待自动保存…' : '输入后自动保存'}</span>
      {(otherDate || otherKind) && writer.body ? <span className="journal-inline-elsewhere">这条记在 {writer.bound.date} · {journalKindLabels[writer.bound.kind]}，不是当前选择的{differs}</span> : null}
      {writer.record && !writer.dirty && writer.state !== 'saving' ? <button className="btn btn-ghost btn-sm" type="button" aria-label={newLabel} title={newLabel} onClick={() => { if (writer.reset()) inputRef.current?.focus(); }}>＋</button> : null}
    </div>
    {writer.error ? <div className="journal-inline-error" role="alert"><span>{writer.error}</span><button className="btn btn-ghost btn-sm" type="button" onClick={() => void writer.flush(true)}>重试保存</button><button className="btn btn-ghost btn-sm" type="button" onClick={() => {
      if (window.confirm('确定放弃当前未保存的文字并重新读取记录？') && writer.reset(null, true)) onReload();
    }}>重新读取</button></div> : null}
  </div>;
}
