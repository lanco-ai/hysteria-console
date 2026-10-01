import { useEffect, useRef, type ReactNode } from 'react';

export function WorkspaceDialog({ title, onClose, children, returnFocusTo }: { title: string; onClose: () => void; children: ReactNode; returnFocusTo?: HTMLElement | null }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = ref.current;
    const opener = returnFocusTo ?? document.activeElement;
    element?.showModal();
    return () => {
      element?.close();
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus({ preventScroll: true });
    };
  }, [returnFocusTo]);
  return <dialog ref={ref} className="workspace-dialog" aria-label={title} onCancel={event => { event.preventDefault(); onClose(); }}>
    <header><h2>{title}</h2><button type="button" className="btn btn-ghost" aria-label="关闭窗口" onClick={onClose}>×</button></header>
    <div className="workspace-dialog-body">{children}</div>
  </dialog>;
}
