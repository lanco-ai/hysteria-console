import { useEffect, useRef, useState } from 'react';
import {
  AgentApiError,
  applyAgentChange,
  loadAgentUsers,
  planAgent,
  undoAgentChange,
  type AgentPlanResponse,
} from './agentApi';

type AgentStatus = 'idle' | 'reading' | 'read' | 'preview' | 'applying' | 'saved' | 'undone' | 'error';

function statusLabel(status: AgentStatus): string {
  return ({ idle: '等待你的指令', reading: '正在读取用户规则', read: '已读取规则', preview: '已生成变更预览', applying: '正在保存变更', saved: '已保存', undone: '已撤销', error: '执行失败' })[status];
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
  const requestRef = useRef<AbortController | null>(null);

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

  const run = async () => {
    if (!message.trim() || !targetUser || status === 'reading' || status === 'applying') return;
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setError(''); setResult(null); setStatus('reading');
    try {
      const response = await planAgent(message.trim(), targetUser, controller.signal);
      setResult(response);
      setLastChangeId(response.plan?.change_id || '');
      setStatus(response.action === 'apply_pack' ? 'preview' : 'read');
    } catch (errorValue) {
      if (errorValue instanceof DOMException && errorValue.name === 'AbortError') return;
      setError(errorValue instanceof AgentApiError ? errorValue.message : 'Agent 请求失败');
      setStatus('error');
    } finally { requestRef.current = null; }
  };

  const apply = async () => {
    if (!lastChangeId) return;
    setError(''); setStatus('applying');
    try {
      await applyAgentChange(lastChangeId);
      setStatus('saved');
      setResult(current => current ? { ...current, explanation: `${current.explanation} 已保存，用户下次拉取订阅时生效。` } : current);
    } catch (errorValue) {
      setError(errorValue instanceof AgentApiError ? errorValue.message : '保存失败，请刷新后核对');
      setStatus('error');
    }
  };

  const undo = async () => {
    if (!lastChangeId || status !== 'saved') return;
    setError(''); setStatus('applying');
    try { await undoAgentChange(lastChangeId); setLastChangeId(''); setStatus('undone'); setResult(current => current ? { ...current, explanation: '变更已撤销，规则恢复到修改前版本。' } : current); }
    catch (errorValue) { setError(errorValue instanceof AgentApiError ? errorValue.message : '撤销失败，请刷新后核对'); setStatus('error'); }
  };

  return <>
    {!open ? <button className="lanco-agent-launcher" type="button" aria-label="打开 Lanco Agent" onClick={() => setOpen(true)}><span aria-hidden="true">✦</span><span>助手</span></button> : null}
    {open ? <aside className="lanco-agent" role="dialog" aria-modal="false" aria-labelledby="lanco-agent-title">
      <header className="lanco-agent-header"><div><strong id="lanco-agent-title">Lanco Agent</strong><span>规则助手 · {statusLabel(status)}</span></div><button type="button" className="lanco-agent-close" aria-label="收起 Lanco Agent" onClick={() => setOpen(false)}>×</button></header>
      <div className="lanco-agent-body">
        <div className="lanco-agent-field"><label htmlFor="lanco-agent-user">目标用户</label><select id="lanco-agent-user" value={targetUser} onChange={event => setTargetUser(event.target.value)} disabled={status === 'reading' || status === 'applying'}><option value="">选择用户</option>{users.map(user => <option key={user} value={user}>{user}</option>)}</select></div>
        <div className="lanco-agent-timeline" aria-live="polite"><span className={status !== 'idle' ? 'is-active' : ''}>读取用户规则</span><span className={status === 'read' || status === 'preview' || status === 'applying' || status === 'saved' || status === 'undone' ? 'is-active' : ''}>检查冲突并生成预览</span><span className={status === 'saved' || status === 'undone' ? 'is-active' : ''}>{status === 'undone' ? '已撤销修改' : '保存结果'}</span></div>
        {result ? <section className="lanco-agent-result"><p>{result.explanation}</p>{result.snapshot ? <p className="small">当前有 {result.snapshot.rules.length} 条用户规则，版本 {result.snapshot.revision.slice(0, 8)}…</p> : null}{result.plan ? <><div className="lanco-agent-plan"><strong>{result.plan.label}</strong><span>{result.plan.description}</span>{result.plan.rule ? <code>{result.plan.rule}</code> : null}{result.plan.additions.length ? <><span>新增规则</span><ul>{result.plan.additions.map(rule => <li key={`add-${rule}`}><code>{rule}</code></li>)}</ul></> : null}{result.plan.removals.length ? <><span>删除规则</span><ul>{result.plan.removals.map(rule => <li key={`remove-${rule}`}><code>{rule}</code></li>)}</ul></> : null}{!result.plan.additions.length && !result.plan.removals.length ? <span>没有实际变化，重复项已跳过。</span> : null}</div><div className="lanco-agent-actions"><button className="btn btn-primary btn-sm" type="button" onClick={() => void apply()} disabled={status !== 'preview'}>应用修改</button><button className="btn btn-ghost btn-sm" type="button" onClick={() => { setResult(null); setLastChangeId(''); setStatus('idle'); }}>取消</button></div></> : null}{result.plan && status === 'saved' ? <button className="btn btn-ghost btn-sm" type="button" onClick={() => void undo()}>撤销这次修改</button> : null}</section> : null}
        {error ? <div className="err" role="alert">{error}</div> : null}
        <textarea className="lanco-agent-input" value={message} onChange={event => setMessage(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void run(); } }} placeholder="例如：给 lhz 启用 Overleaf 加速" rows={2} disabled={status === 'reading' || status === 'applying'} />
      </div>
      <footer className="lanco-agent-footer"><span>Gemini 3.8 Flash High</span><button className="btn btn-primary btn-sm" type="button" onClick={() => void run()} disabled={!targetUser || !message.trim() || status === 'reading' || status === 'applying'}>{status === 'reading' ? '处理中…' : '发送'}</button></footer>
    </aside> : null}
  </>;
}
