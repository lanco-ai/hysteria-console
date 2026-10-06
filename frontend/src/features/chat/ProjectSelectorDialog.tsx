import { useEffect, useRef, useState } from 'react';
import { WorkspaceDialog } from './WorkspaceDialog';
import type { LearningProject } from './workspaceApi';

export function ProjectSelectorDialog({ projects, currentId, disabled, onSelect, onCreate, onClose, returnFocusTo }: {
  projects: LearningProject[];
  currentId: string;
  disabled: boolean;
  onSelect: (id: string) => Promise<boolean>;
  onCreate: () => void;
  onClose: () => void;
  returnFocusTo: HTMLElement | null;
}) {
  const [query, setQuery] = useState('');
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState('');
  const pendingRef = useRef(false);
  const searchRef = useRef<HTMLInputElement>(null);
  // WorkspaceDialog opens its native dialog before this parent effect runs.
  useEffect(() => { searchRef.current?.focus(); }, []);
  const needle = query.trim().toLocaleLowerCase();
  const visible = projects.filter(project => `${project.name}\n${project.goal}`.toLocaleLowerCase().includes(needle));
  const locked = disabled || pending;
  async function select(id: string) {
    if (disabled || pendingRef.current) return;
    if (id === currentId) { onClose(); return; }
    pendingRef.current = true; setPending(true); setNotice('');
    try {
      if (await onSelect(id)) onClose();
      else setNotice('当前项目与草稿已保留。可以重新选择，或关闭窗口检查提示。');
    } finally { pendingRef.current = false; setPending(false); }
  }
  return <WorkspaceDialog title="选择学习项目" returnFocusTo={returnFocusTo} onClose={() => { if (!pendingRef.current) onClose(); }}>
    <label className="workspace-project-search"><span className="sr-only">搜索学习项目</span><input ref={searchRef} className="input" type="search" placeholder="搜索项目名称或学习目标" value={query} disabled={locked} onChange={event => setQuery(event.target.value)} /></label>
    <div className="workspace-project-grid">
      <button type="button" className="workspace-project-card" aria-label="自由对话" aria-pressed={!currentId} disabled={locked} onClick={() => { void select(''); }}>
        <span className="workspace-project-card-heading"><strong>自由对话</strong>{!currentId && <span className="workspace-project-selected">当前</span>}</span>
        <span className="workspace-project-card-goal">从一个问题开始，不关联学习项目。</span>
      </button>
      {visible.map(project => <button key={project.id} type="button" className="workspace-project-card" aria-label={`选择项目 ${project.name}`} aria-pressed={currentId === project.id} disabled={locked} onClick={() => { void select(project.id); }}>
        <span className="workspace-project-card-heading"><strong>{project.name}</strong>{currentId === project.id && <span className="workspace-project-selected">当前</span>}</span>
        <span className="workspace-project-card-goal">{project.goal || '还未填写学习目标'}</span>
      </button>)}
    </div>
    {!visible.length && <p className="workspace-muted">{needle ? '没有匹配的项目' : '创建一个项目，把目标和资料放在一起。'}</p>}
    {notice && <p className="workspace-muted" role="status">{notice}</p>}
    <div className="workspace-project-selector-footer"><span className="workspace-muted">{pending ? '正在保存当前草稿…' : `${projects.length} 个学习项目`}</span><button type="button" className="btn btn-primary btn-sm" disabled={locked} onClick={onCreate}>新建学习项目</button></div>
  </WorkspaceDialog>;
}
