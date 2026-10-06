import { useState } from 'react';
import { WorkspaceDialog } from './WorkspaceDialog';
import { workspaceRequest, type LearningProject } from './workspaceApi';

export function ProjectDialog({ project, onClose, onSaved, returnFocusTo }: { project: LearningProject | null; onClose: () => void; onSaved: (project: LearningProject | null) => void; returnFocusTo?: HTMLElement | null }) {
  const [name, setName] = useState(project?.name || '');
  const [goal, setGoal] = useState(project?.goal || '');
  const [instructions, setInstructions] = useState(project?.instructions || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const presets: Record<string, [string, string]> = {
    'AI 与存储': ['能解释关键原理，并通过代码或实验验证。', '先询问我的理解，再指出漏洞；区分论文结论与工程经验，给出可验证的小实验。'],
    '论文阅读': ['读懂研究问题、假设、方法与局限，形成自己的判断。', '引用提供的原文片段，注明页码。没有证据就说明。用问题引导我复述，不要替我编造阅读心得。'],
    '雅思备考': ['持续练习听说读写，追踪反复出现的错误。', '先保留我的原始表达，再解释修改原因。给出可复练的小任务，不把练习评价当作官方分数。'],
  };
  return <WorkspaceDialog title={project ? '编辑学习项目' : '新建学习项目'} onClose={onClose} {...(returnFocusTo === undefined ? {} : { returnFocusTo })}>
    <form className="workspace-form" onSubmit={event => { event.preventDefault(); setBusy(true); setError(''); void workspaceRequest<LearningProject>(`/projects${project ? `/${project.id}` : ''}`, project ? 'PUT' : 'POST', { name: name.trim(), goal, instructions, ...(project ? { revision: project.revision } : {}) }).then(onSaved).catch(e => setError(String(e.message))).finally(() => setBusy(false)); }}>
      {!project && <div className="workspace-actions">{Object.keys(presets).map(label => <button className="btn btn-secondary btn-sm" key={label} type="button" onClick={() => { const preset = presets[label]; if (preset) { setName(label); setGoal(preset[0]); setInstructions(preset[1]); } }}>{label}</button>)}</div>}
      <label>项目名称<input className="input" value={name} maxLength={80} required onChange={e => setName(e.target.value)} /></label>
      <label>想达到什么目标<textarea className="input" value={goal} maxLength={2000} rows={3} onChange={e => setGoal(e.target.value)} /></label>
      <label>这个项目中的 AI 如何帮助你<textarea className="input" value={instructions} maxLength={4000} rows={5} onChange={e => setInstructions(e.target.value)} /></label>
      <p className="workspace-muted">目标和指令只用于该项目的对话。论文需逐次勾选后才会提供给模型。</p>
      {error && <p className="err" role="alert">{error}</p>}
      <div className="workspace-actions"><button className="btn btn-primary" disabled={busy || !name.trim()}>保存项目</button>{project && <button type="button" className="btn btn-danger" disabled={busy} onClick={() => { if (!window.confirm('删除这个空项目？')) return; setBusy(true); void workspaceRequest(`/projects/${project.id}`, 'DELETE', { revision: project.revision }).then(() => onSaved(null)).catch(e => setError(String(e.message))).finally(() => setBusy(false)); }}>删除空项目</button>}</div>
    </form>
  </WorkspaceDialog>;
}
