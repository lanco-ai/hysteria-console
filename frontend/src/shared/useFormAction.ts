import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

type Callbacks<Result> = {
  onStart?: () => void;
  onResult: (result: Result) => void;
  onError: () => void;
};

type ActiveAction = {
  controller: AbortController;
  generation: number;
  timer: number;
};

const ACTION_TIMEOUT_MS = 15_000;

export function useFormAction(onPageShow?: () => void) {
  const [busy, setBusy] = useState(false);
  const pendingRef = useRef(false);
  const generationRef = useRef(0);
  const activeRef = useRef<ActiveAction | null>(null);
  const onPageShowRef = useRef(onPageShow);

  useLayoutEffect(() => {
    onPageShowRef.current = onPageShow;
  }, [onPageShow]);

  const invalidate = useCallback(() => {
    generationRef.current += 1;
    const active = activeRef.current;
    if (active) {
      window.clearTimeout(active.timer);
      active.controller.abort();
    }
    activeRef.current = null;
    pendingRef.current = false;
  }, []);

  useEffect(() => {
    const onPageHide = () => invalidate();
    const onPageShowEvent = () => {
      invalidate();
      setBusy(false);
      onPageShowRef.current?.();
    };
    window.addEventListener('pagehide', onPageHide);
    window.addEventListener('pageshow', onPageShowEvent);
    return () => {
      window.removeEventListener('pagehide', onPageHide);
      window.removeEventListener('pageshow', onPageShowEvent);
      invalidate();
    };
  }, [invalidate]);

  const run = useCallback(async <Result,>(
    operation: (signal: AbortSignal) => Promise<Result>,
    callbacks: Callbacks<Result>,
  ): Promise<void> => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    const controller = new AbortController();
    let timeoutExpired = false;
    let rejectTimeout: (reason: Error) => void = () => undefined;
    const timedOut = new Promise<never>((_, reject) => {
      rejectTimeout = reject;
    });
    const timer = window.setTimeout(() => {
      timeoutExpired = true;
      rejectTimeout(new Error('Form action timed out'));
      controller.abort();
    }, ACTION_TIMEOUT_MS);
    activeRef.current = { controller, generation, timer };
    setBusy(true);
    try {
      callbacks.onStart?.();
      const result = await Promise.race([operation(controller.signal), timedOut]);
      if (timeoutExpired) throw new Error('Form action timed out');
      if (generationRef.current === generation) callbacks.onResult(result);
    } catch {
      if (generationRef.current === generation) callbacks.onError();
    } finally {
      window.clearTimeout(timer);
      if (generationRef.current === generation) {
        activeRef.current = null;
        pendingRef.current = false;
        setBusy(false);
      }
    }
  }, []);

  return { busy, run };
}
