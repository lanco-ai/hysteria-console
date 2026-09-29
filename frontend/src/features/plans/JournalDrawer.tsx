import { useEffect, useRef, type ReactNode } from 'react';

/** A native modal keeps the background inert and traps keyboard focus. */
export function JournalDrawer({ open, title, onClose, viewKey, children }: {
  open: boolean; title: string; onClose: () => void; viewKey: string; children: ReactNode;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!open || !dialog) return;
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialog.showModal();
    closeRef.current?.focus({ preventScroll: true });
    return () => {
      dialog.close();
      document.body.style.overflow = previousOverflow;
      if (trigger?.isConnected && trigger !== document.body) trigger.focus({ preventScroll: true });
      else document.querySelector<HTMLElement>('.daily-section-nav a[aria-current]')?.focus({ preventScroll: true });
    };
  }, [open]);

  useEffect(() => { bodyRef.current?.scrollTo({ top: 0, behavior: 'instant' }); }, [viewKey]);

  return <dialog id="daily-journal-drawer" ref={dialogRef} className="journal-drawer" aria-labelledby="journal-drawer-title"
    onKeyDown={event => {
      if (event.key !== 'Tab') return;
      const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button, a[href], input, select, textarea, summary, [tabindex="0"]'))
        .filter(element => element.checkVisibility() && !element.matches(':disabled'));
      const first = controls[0];
      const last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }}
    onCancel={event => { event.preventDefault(); onClose(); }}>
    <header className="journal-drawer-header"><div><span>我的记录</span><h2 id="journal-drawer-title">{title}</h2></div>
      <button ref={closeRef} className="btn btn-secondary btn-sm" type="button" aria-label="关闭记录面板" onClick={onClose}>关闭 <span aria-hidden="true">×</span></button>
    </header>
    <div className="journal-drawer-body" ref={bodyRef}>{children}</div>
  </dialog>;
}
