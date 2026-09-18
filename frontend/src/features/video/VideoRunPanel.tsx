import { useEffect, useState, type ReactElement } from 'react';
import { cancelRun, loadRun } from './videoApi';
import type { VideoRun } from './videoTypes';

export function VideoRunPanel({ runId }: { runId: string | null }): ReactElement {
  const [run, setRun] = useState<VideoRun | null>(null);
  useEffect(() => {
    if (!runId) { setRun(null); return; }
    let active = true;
    const poll = async () => { try { const result = await loadRun(runId); if (active) setRun(result); } catch { /* status remains visible */ } };
    void poll();
    const timer = window.setInterval(() => { if (run?.state === 'succeeded' || run?.state === 'failed' || run?.state === 'cancel_unsupported') return; void poll(); }, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [runId, run?.state]);
  const stop = async () => { if (!runId) return; try { setRun(await cancelRun(runId)); } catch { /* preserve current state */ } };
  return <section className="video-run-panel" aria-live="polite"><div><strong>当前任务</strong><span>{run?.state || '未运行'}</span></div>{run ? <button type="button" className="button danger" onClick={stop} disabled={run.state !== 'running'}>停止</button> : null}{run?.error ? <small>{run.error}</small> : null}</section>;
}
