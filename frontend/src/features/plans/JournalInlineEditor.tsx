import type { RefObject } from 'react';
import { journalKindLabels } from './journalLabels';
import type { useJournalAutosave, JournalContext } from './useJournalAutosave';

export function JournalInlineEditor({ writer, context, inputRef, empty, onReload }: {
  writer: ReturnType<typeof useJournalAutosave>; context: JournalContext;
  inputRef: RefObject<HTMLTextAreaElement | null>; empty: boolean; onReload: () => void;
}) {
  const elsewhere = writer.bound.date !== context.date || writer.bound.kind !== context.kind;
  return <div className="journal-inline-editor">
    <textarea ref={inputRef} aria-label="记录内容" aria-describedby="journal-autosave-state" maxLength={12000}
      placeholder={empty ? '这一天暂无记录。可以先记下一件小事。' : '在这里记下一件事、一个想法，或今天的收获…'}
      value={writer.body} onChange={event => writer.setBody(event.target.value)}
      onBlur={() => void writer.flush()} onCompositionStart={() => writer.composition(true)} onCompositionEnd={() => writer.composition(false)}
      onKeyDown={event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); void writer.flush(); } }} />
    <div className="journal-inline-state">
      <span id="journal-autosave-state" role="status">{writer.state === 'saving' ? '正在保存…' : writer.state === 'saved' ? '已自动保存' : writer.state === 'error' ? '未保存，文字已保留' : writer.dirty ? '等待自动保存…' : '输入后自动保存'}</span>
      {elsewhere && writer.body ? <span>{writer.bound.date} · {journalKindLabels[writer.bound.kind]}</span> : null}
      {writer.record && !writer.dirty && writer.state !== 'saving' ? <button className="btn btn-ghost btn-sm" type="button" aria-label="另记一条" title="另记一条" onClick={() => { if (writer.reset()) inputRef.current?.focus(); }}>＋</button> : null}
    </div>
    {writer.error ? <div className="journal-inline-error" role="alert"><span>{writer.error}</span><button className="btn btn-ghost btn-sm" type="button" onClick={() => void writer.flush(true)}>重试保存</button><button className="btn btn-ghost btn-sm" type="button" onClick={() => {
      if (window.confirm('确定放弃当前未保存的文字并重新读取记录？') && writer.reset(null, true)) onReload();
    }}>重新读取</button></div> : null}
  </div>;
}
