import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import {
  AgentApiError,
  applyAgentChange,
  loadAgentUsers,
  planAgent,
  undoAgentChange,
  type AgentUserRules,
  type AgentPlanResponse,
} from './agentApi';

type AgentStatus = 'idle' | 'reading' | 'read' | 'preview' | 'applying' | 'saved' | 'undone' | 'error';
type FloatingPosition = { left: number; top: number };
type DragTarget = 'panel' | 'launcher';
type DragState = {
  target: DragTarget;
  pointerId: number;
  startX: number;
  startY: number;
  startLeft: number;
  startTop: number;
  width: number;
  height: number;
  moved: boolean;
};

const PANEL_POSITION_KEY = 'hy2.lanco.agent.position.v1';
const LAUNCHER_POSITION_KEY = 'hy2.lanco.agent.launcher-position.v1';

function readPosition(key: string): FloatingPosition | null {
  try {
    const value = JSON.parse(localStorage.getItem(key) || 'null') as Partial<FloatingPosition> | null;
    return value && Number.isFinite(value.left) && Number.isFinite(value.top)
      ? { left: Number(value.left), top: Number(value.top) }
      : null;
  } catch {
    return null;
  }
}

function RobotAvatar({ small = false }: { small?: boolean }) {
  return <svg className={`lanco-agent-avatar${small ? ' is-small' : ''}`} viewBox="0 0 64 64" role="img" aria-label="Lanco 助手">
    <path className="lanco-agent-antenna" d="M32 11V6" />
    <circle className="lanco-agent-antenna-dot" cx="32" cy="5" r="2.5" />
    <rect className="lanco-agent-head" x="10" y="13" width="44" height="33" rx="13" />
    <circle className="lanco-agent-eye" cx="24" cy="28" r="3" />
    <circle className="lanco-agent-eye" cx="40" cy="28" r="3" />
    <path className="lanco-agent-mouth" d="M27 36c3 3 7 3 10 0" />
    <circle className="lanco-agent-blush" cx="17" cy="35" r="3" />
    <circle className="lanco-agent-blush" cx="47" cy="35" r="3" />
    <path className="lanco-agent-body" d="M21 48h22c4 0 7 3 7 7v3H14v-3c0-4 3-7 7-7Z" />
    <path className="lanco-agent-body-detail" d="M29 54h6" />
  </svg>;
}

function statusLabel(status: AgentStatus): string {
  return ({ idle: '等待你的指令', reading: '正在读取用户规则', read: '已读取规则', preview: '已生成变更预览', applying: '正在保存变更', saved: '已保存', undone: '已撤销', error: '执行失败' })[status];
}

function RuleSummary({ snapshot }: { snapshot: AgentUserRules }) {
  const userRules = snapshot.rules || [];
  const globalRules = snapshot.global_rules || [];
  const mergedRules = snapshot.merged_rules || [];
  return <section className="lanco-agent-rule-summary" aria-label="规则来源统计">
    <div className="lanco-agent-rule-cards">
      <div><strong>用户覆盖规则</strong><b>{userRules.length} 条</b><span>优先匹配</span></div>
      <div><strong>继承全局规则</strong><b>{globalRules.length} 条</b><span>模板 · {snapshot.global_revision.slice(0, 8) || '未知'}…</span></div>
      <div><strong>合并后订阅规则</strong><b>{mergedRules.length} 条</b><span>去重后统计</span></div>
    </div>
    <details className="lanco-agent-rule-details">
      <summary>查看合并顺序</summary>
      <div className="lanco-agent-rule-lists">
        <div><strong>用户专属规则 · 优先匹配</strong>{userRules.length ? userRules.map((rule, index) => <code key={`user-${index}-${rule}`}>{rule}</code>) : <span>暂无用户覆盖规则</span>}</div>
        <div><strong>全局模板规则 · 自动继承</strong>{globalRules.length ? globalRules.map((rule, index) => <code key={`global-${index}-${rule}`}>{rule}</code>) : <span>暂无全局模板规则</span>}</div>
      </div>
    </details>
  </section>;
}

export function LancoAgent() {
  const [open, setOpen] = useState(false);
  const [users, setUsers] = useState<string[]>([]);
  const [targetUser, setTargetUser] = useState('');
  const [message, setMessage] = useState('');
  const [status, setStatus] = useState<AgentStatus>('idle');
  const [result, setResult] = useState<AgentPlanResponse | null>(null);
  const [error, setError] = useState('');
  const [lastChangeId, setLastChangeId] = useState('');
  const [autoApply, setAutoApply] = useState(false);
  const [panelPosition, setPanelPosition] = useState<FloatingPosition | null>(() => readPosition(PANEL_POSITION_KEY));
  const [launcherPosition, setLauncherPosition] = useState<FloatingPosition | null>(() => readPosition(LAUNCHER_POSITION_KEY));
  const [draggingTarget, setDraggingTarget] = useState<DragTarget | null>(null);
  const panelRef = useRef<HTMLElement | null>(null);
  const launcherRef = useRef<HTMLButtonElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const suppressLauncherClickRef = useRef(false);
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => {
    try {
      if (panelPosition) localStorage.setItem(PANEL_POSITION_KEY, JSON.stringify(panelPosition));
      else localStorage.removeItem(PANEL_POSITION_KEY);
    } catch { /* localStorage may be disabled */ }
  }, [panelPosition]);

  useEffect(() => {
    try {
      if (launcherPosition) localStorage.setItem(LAUNCHER_POSITION_KEY, JSON.stringify(launcherPosition));
      else localStorage.removeItem(LAUNCHER_POSITION_KEY);
    } catch { /* localStorage may be disabled */ }
  }, [launcherPosition]);

  useEffect(() => {
    if (!draggingTarget) return undefined;
    const clamp = (left: number, top: number, width: number, height: number): FloatingPosition => ({
      left: Math.min(Math.max(8, left), Math.max(8, window.innerWidth - width - 8)),
      top: Math.min(Math.max(8, top), Math.max(8, window.innerHeight - height - 8)),
    });
    const onMove = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag || event.pointerId !== drag.pointerId) return;
      const left = drag.startLeft + event.clientX - drag.startX;
      const top = drag.startTop + event.clientY - drag.startY;
      if (Math.abs(event.clientX - drag.startX) > 4 || Math.abs(event.clientY - drag.startY) > 4) drag.moved = true;
      const next = clamp(left, top, drag.width, drag.height);
      if (drag.target === 'panel') setPanelPosition(next);
      else setLauncherPosition(next);
    };
    const onEnd = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag || event.pointerId !== drag.pointerId) return;
      if (drag.target === 'launcher') {
        if (!drag.moved) setOpen(true);
        else suppressLauncherClickRef.current = true;
      }
      dragRef.current = null;
      setDraggingTarget(null);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onEnd);
    window.addEventListener('pointercancel', onEnd);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onEnd);
      window.removeEventListener('pointercancel', onEnd);
    };
  }, [draggingTarget]);

  useEffect(() => {
    const onResize = () => {
      const clamp = (position: FloatingPosition | null, element: HTMLElement | null): FloatingPosition | null => {
        if (!position || !element) return position;
        const rect = element.getBoundingClientRect();
        return {
          left: Math.min(Math.max(8, position.left), Math.max(8, window.innerWidth - rect.width - 8)),
          top: Math.min(Math.max(8, position.top), Math.max(8, window.innerHeight - rect.height - 8)),
        };
      };
      setPanelPosition(current => clamp(current, panelRef.current));
      setLauncherPosition(current => clamp(current, launcherRef.current));
    };
    window.addEventListener('resize', onResize);
    onResize();
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    if (!open || users.length) return;
    const controller = new AbortController();
    void loadAgentUsers(controller.signal).then(values => {
      setUsers(values);
      setTargetUser(current => current || values[0] || '');
    }).catch(errorValue => {
      if (!(errorValue instanceof DOMException && errorValue.name === 'AbortError')) setError('无法读取用户列表');
    });
    return () => controller.abort();
  }, [open, users.length]);

  const clearConversation = () => {
    requestRef.current?.abort();
    requestRef.current = null;
    setMessage('');
    setStatus('idle');
    setResult(null);
    setError('');
    setLastChangeId('');
  };

  const closeAgent = () => {
    if (status === 'read' || status === 'saved' || status === 'undone') clearConversation();
    setOpen(false);
  };

  const applyChange = async (changeId: string) => {
    if (!changeId) return;
    setError(''); setStatus('applying');
    try {
      const response = await applyAgentChange(changeId);
      setStatus('saved');
      setResult(current => current ? {
        ...current,
        ...(response.result.snapshot ? { snapshot: response.result.snapshot } : {}),
        explanation: `${current.explanation} 已保存，用户下次拉取订阅时生效。`,
      } : current);
    } catch (errorValue) {
      setError(errorValue instanceof AgentApiError ? errorValue.message : '保存失败，请刷新后核对');
      setStatus('error');
    }
  };

  const run = async () => {
    if (!message.trim() || !targetUser || status === 'reading' || status === 'applying') return;
    const submittedMessage = message.trim();
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setError(''); setResult(null); setStatus('reading');
    try {
      const response = await planAgent(submittedMessage, targetUser, controller.signal);
      setMessage('');
      setResult(response);
      setLastChangeId(response.plan?.change_id || '');
      const isDirectRuleChange = response.action === 'add_rule' || response.action === 'delete_rule';
      if (response.plan && autoApply && isDirectRuleChange && (response.plan.additions.length || response.plan.removals.length)) {
        setStatus('preview');
        void applyChange(response.plan.change_id);
      } else {
        setStatus(response.plan ? 'preview' : 'read');
      }
    } catch (errorValue) {
      if (errorValue instanceof DOMException && errorValue.name === 'AbortError') return;
      setError(errorValue instanceof AgentApiError ? errorValue.message : 'Agent 请求失败');
      setStatus('error');
    } finally { requestRef.current = null; }
  };

  const apply = async () => {
    await applyChange(lastChangeId);
  };

  const undo = async () => {
    if (!lastChangeId || status !== 'saved') return;
    setError(''); setStatus('applying');
    try {
      const response = await undoAgentChange(lastChangeId);
      setLastChangeId(''); setStatus('undone');
      setResult(current => current ? {
        ...current,
        ...(response.result.snapshot ? { snapshot: response.result.snapshot } : {}),
        explanation: '变更已撤销，规则恢复到修改前版本。',
      } : current);
    }
    catch (errorValue) { setError(errorValue instanceof AgentApiError ? errorValue.message : '撤销失败，请刷新后核对'); setStatus('error'); }
  };

  const beginDrag = (target: DragTarget, event: ReactPointerEvent<HTMLElement>) => {
    if (event.button !== 0 || (target === 'panel' && event.target instanceof Element && event.target.closest('button, a, input, select, textarea'))) return;
    const element = target === 'panel' ? panelRef.current : launcherRef.current;
    if (!element) return;
    const rect = element.getBoundingClientRect();
    const position = target === 'panel' ? panelPosition : launcherPosition;
    dragRef.current = {
      target,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startLeft: position?.left ?? rect.left,
      startTop: position?.top ?? rect.top,
      width: rect.width,
      height: rect.height,
      moved: false,
    };
    try { element.setPointerCapture(event.pointerId); } catch { /* capture is unavailable in some test/webview shells */ }
    setDraggingTarget(target);
  };

  const resetPosition = () => {
    setPanelPosition(null);
    setLauncherPosition(null);
  };

  const panelStyle = panelPosition ? { left: `${panelPosition.left}px`, top: `${panelPosition.top}px`, right: 'auto', bottom: 'auto' } : undefined;
  const launcherStyle = launcherPosition ? { left: `${launcherPosition.left}px`, top: `${launcherPosition.top}px`, right: 'auto', bottom: 'auto' } : undefined;
  const isIdle = status === 'idle' && !result && !error;

  return <>
    {!open ? <button ref={launcherRef} className={`lanco-agent-launcher${draggingTarget === 'launcher' ? ' is-dragging' : ''}`} style={launcherStyle} type="button" aria-label="打开 Lanco Agent" title="Lanco 助手" onPointerDown={event => beginDrag('launcher', event)} onClick={() => { if (suppressLauncherClickRef.current) { suppressLauncherClickRef.current = false; return; } setOpen(true); }}><RobotAvatar small /></button> : null}
    {open ? <aside ref={panelRef} className={`lanco-agent${draggingTarget === 'panel' ? ' is-dragging' : ''}`} style={panelStyle} role="dialog" aria-modal="false" aria-labelledby="lanco-agent-title">
      <header className="lanco-agent-header" onPointerDown={event => beginDrag('panel', event)}><div className="lanco-agent-heading"><RobotAvatar small /><div><strong id="lanco-agent-title">Lanco Agent</strong><span>规则助手 · {statusLabel(status)}</span></div></div><div className="lanco-agent-header-actions"><button type="button" className="lanco-agent-reset" aria-label="重置位置" title="恢复机器人和面板到右下角" onPointerDown={event => event.stopPropagation()} onClick={resetPosition}>⌖</button><button type="button" className="lanco-agent-close" aria-label="收起 Lanco Agent" onPointerDown={event => event.stopPropagation()} onClick={closeAgent}>×</button></div></header>
      <div className="lanco-agent-body">
        <div className="lanco-agent-field"><label htmlFor="lanco-agent-user">目标用户</label><select id="lanco-agent-user" value={targetUser} onChange={event => { clearConversation(); setAutoApply(false); setTargetUser(event.target.value); }} disabled={status === 'reading' || status === 'applying'}><option value="">选择用户</option>{users.map(user => <option key={user} value={user}>{user}</option>)}</select><label className="lanco-agent-auto"><input type="checkbox" checked={autoApply} onChange={event => setAutoApply(event.target.checked)} disabled={status === 'reading' || status === 'applying'} /> 自动执行本用户规则 <span>明确的添加或删除指令会直接保存</span></label></div>
        {isIdle ? <section className="lanco-agent-welcome"><p>你好，我可以帮你整理指定用户的网络规则。</p><span>用户规则会排在全局规则之前，先选择一个操作或直接输入要求。</span><div className="lanco-agent-quick-actions"><button type="button" className="btn btn-ghost btn-sm" onClick={() => setMessage('查看当前用户规则和继承的全局规则')}>查看合并规则</button><button type="button" className="btn btn-ghost btn-sm" onClick={() => setMessage('给当前用户添加规则：')}>添加用户规则</button><button type="button" className="btn btn-ghost btn-sm" onClick={() => setMessage('给当前用户应用规则包：')}>应用规则包</button><button type="button" className="btn btn-ghost btn-sm" onClick={() => setMessage('检查当前用户规则并生成预览')}>检查冲突</button><button type="button" className="btn btn-ghost btn-sm" onClick={() => setMessage('给当前用户启用 Overleaf 加速')}>Overleaf 加速</button></div></section> : null}
        {!isIdle ? <div className="lanco-agent-timeline" aria-live="polite"><span className={status === 'read' || status === 'preview' || status === 'applying' || status === 'saved' || status === 'undone' ? 'is-active' : ''}>读取用户规则</span><span className={status === 'preview' || status === 'applying' || status === 'saved' || status === 'undone' ? 'is-active' : ''}>检查冲突并生成预览</span><span className={status === 'saved' || status === 'undone' ? 'is-active' : ''}>{status === 'undone' ? '已撤销修改' : '保存结果'}</span></div> : null}
        {result ? <section className="lanco-agent-result">
          <p>{result.explanation}</p>
          {result.snapshot ? <RuleSummary snapshot={result.snapshot} /> : null}
          {result.plan ? <>
            <div className="lanco-agent-plan">
              <strong>{result.plan.label}</strong>
              <span>{result.plan.description}</span>
              <span>规则位置：全局规则之前 · 全局模板保持不变</span>
              {result.plan.rule ? <code>{result.plan.rule}</code> : null}
              {result.plan.additions.length ? <><span>新增规则</span><ul>{result.plan.additions.map(rule => <li key={`add-${rule}`}><code>{rule}</code></li>)}</ul></> : null}
              {result.plan.removals.length ? <><span>删除规则</span><ul>{result.plan.removals.map(rule => <li key={`remove-${rule}`}><code>{rule}</code></li>)}</ul></> : null}
              {!result.plan.additions.length && !result.plan.removals.length ? <span>没有实际变化，重复项已跳过。</span> : null}
            </div>
            <div className="lanco-agent-actions"><button className="btn btn-primary btn-sm" type="button" onClick={() => void apply()} disabled={status !== 'preview'}>应用修改</button><button className="btn btn-ghost btn-sm" type="button" onClick={clearConversation}>取消</button></div>
          </> : null}
          {result.plan && status === 'error' && lastChangeId ? <button className="btn btn-ghost btn-sm" type="button" onClick={() => void apply()}>重试保存</button> : null}
          {result.plan && status === 'saved' ? <button className="btn btn-ghost btn-sm" type="button" onClick={() => void undo()}>撤销这次修改</button> : null}
        </section> : null}
        {error ? <div className="err" role="alert">{error}</div> : null}
        <textarea className="lanco-agent-input" value={message} onChange={event => setMessage(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void run(); } }} placeholder="例如：给 lhz 启用 Overleaf 加速" rows={2} disabled={status === 'reading' || status === 'applying'} />
      </div>
      <footer className="lanco-agent-footer"><button className="lanco-agent-end" type="button" onClick={clearConversation}>结束对话</button><span>Gemini 3.8 Flash High</span><button className="btn btn-primary btn-sm" type="button" onClick={() => void run()} disabled={!targetUser || !message.trim() || status === 'reading' || status === 'applying'}>{status === 'reading' ? '处理中…' : '发送'}</button></footer>
    </aside> : null}
  </>;
}
