import { useEffect, useLayoutEffect, useRef, useState, type ReactElement } from 'react';
import { cancelRun, loadRun } from './videoApi';
import type { VideoRun } from './videoTypes';

export function VideoRunPanel({ runId, onRunChange }: { runId: string | null; onRunChange?: (run: VideoRun) => void }): ReactElement {
  const [run, setRun] = useState<VideoRun | null>(null);
  const currentRunId = useRef(runId);
  useLayoutEffect(() => { currentRunId.current = runId; }, [runId]);
  useEffect(() => {
    if (!runId) { setRun(null); return; }
    let active = true;
    const poll = async () => { try { const result = await loadRun(runId); if (active) { setRun(result); onRunChange?.(result); } } catch { /* status remains visible */ } };
    void poll();
    const timer = window.setInterval(() => { if (run?.state === 'succeeded' || run?.state === 'failed' || run?.state === 'cancel_unsupported') return; void poll(); }, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [runId, run?.state, onRunChange]);
  const stop = async () => {
    if (!runId) return;
    const requestedRunId = runId;
    try {
      const cancelled = await cancelRun(requestedRunId);
      if (currentRunId.current === requestedRunId) setRun(cancelled);
    } catch { /* preserve current state */ }
  };
  return <section className="video-run-panel" aria-live="polite"><div><strong>当前任务</strong><span>{run?.state || '未运行'}</span></div>{run ? <button type="button" className="btn btn-danger" onClick={stop} disabled={run.state !== 'running'}>停止</button> : null}{run?.error ? <small>{run.error}</small> : null}</section>;
}
