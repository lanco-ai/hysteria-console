import type { Ref } from 'react';
import { WorkspaceApiError, type LearningProject, type Paper } from './workspaceApi';

export const MAX_SELECTED_PAPERS = 8;

function paperStatus(paper: Paper) {
  if (paper.status === 'queued') return '排队中';
  if (paper.status === 'processing') return '正在解析 / 识别文字…';
  if (paper.status === 'error') return new WorkspaceApiError(422, paper.error || 'invalid_document').message;
  if (paper.media_type === 'application/pdf') return `${paper.page_count} 页`;
  return paper.has_image ? '图片 · 可用于视觉提问' : '已提取文字';
}

/** The conversation's surroundings: project, knowledge base and documents. */
export function ContextPanel({ panelRef, project, papers, selected, locked, uploading, onUpload, onTogglePaper, onRetryPaper, onDeletePaper, onOpenKnowledge, onOpenMemory, onEditProject, onClose }: {
  panelRef: Ref<HTMLElement>;
  project: LearningProject | null;
  papers: Paper[];
  selected: string[];
  locked: boolean;
  uploading: boolean;
  onUpload: (file: File) => void;
  onTogglePaper: (id: string, on: boolean) => void;
  onRetryPaper: (id: string) => void;
  onDeletePaper: (id: string) => void;
  onOpenKnowledge: () => void;
  onOpenMemory: () => void;
  onEditProject: () => void;
  onClose: () => void;
}) {
  return <aside ref={panelRef} className="chat-context" aria-label="资料与上下文" tabIndex={-1}>
    <header className="chat-context-head">
      <h2>资料与上下文</h2>
      <button className="chat-icon-button" type="button" aria-label="收起资料面板" title="收起资料面板" onClick={onClose}>
        <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5.5 5.5 14.5 14.5M14.5 5.5l-9 9"/></svg>
      </button>
    </header>

    <section className="chat-context-section" aria-labelledby="chat-context-project">
      <h3 id="chat-context-project">学习项目</h3>
      {project ? <div className="chat-context-project">
        <strong>{project.name}</strong>
        <p>{project.goal || '还未填写学习目标。'}</p>
        <div className="workspace-actions">
          <button className="btn btn-sm" type="button" disabled={locked} onClick={onEditProject}>项目目标</button>
          <button className="btn btn-sm" type="button" disabled={locked} onClick={onOpenMemory}>项目记忆{project.memories?.length ? ` · ${project.memories.length}` : ''}</button>
        </div>
      </div> : <p className="workspace-muted">自由对话，不关联学习项目。在左侧选择或新建项目，可以保存目标、指令、记忆和论文资料。</p>}
      <div className="workspace-actions">
        <button className="btn btn-sm" type="button" disabled={locked} onClick={onOpenKnowledge}>知识库</button>
      </div>
    </section>

    <section className="chat-context-section" id="chat-papers" aria-labelledby="chat-context-papers">
      <h3 id="chat-context-papers">论文资料 <span className="chip-count">{papers.length}</span>{selected.length ? <span className="chat-context-selected">已选 {selected.length} / {MAX_SELECTED_PAPERS}</span> : null}</h3>
      {project ? <>
        <label className={`btn btn-sm chat-upload${locked ? ' is-disabled' : ''}`}>
          {uploading ? '正在上传…' : '上传资料'}
          <input className="sr-only" type="file" accept=".pdf,.docx,.txt,.md,.png,.jpg,.jpeg,.webp" disabled={locked} aria-label="上传论文" onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) onUpload(file); }}/>
        </label>
        <p className="workspace-muted">PDF / DOCX / TXT / MD / 图片 · 文档 ≤ 10 MB · 图片 ≤ 4 MB / 800 万像素 · PDF 最多 200 页（扫描页最多 30 页）。</p>
        <p className="workspace-muted">勾选后，相关片段会随问题发送给当前模型；图片还会发送图片本身，请选择支持视觉的模型。</p>
        {papers.length ? <ul className="chat-papers">{papers.map(paper => {
          const checked = selected.includes(paper.id);
          return <li className={`workspace-paper${checked ? ' is-selected' : ''}`} key={paper.id}>
            <label>
              <input type="checkbox" checked={checked} disabled={locked || (!!paper.status && paper.status !== 'ready') || (!checked && selected.length >= MAX_SELECTED_PAPERS)} onChange={event => onTogglePaper(paper.id, event.target.checked)}/>
              <span className="chat-paper-text"><span className="chat-paper-title">{paper.title}</span><small>{paperStatus(paper)}</small></span>
            </label>
            <div className="chat-paper-actions">
              <a href={`/api/chat/documents/${paper.id}/file`} target="_blank" rel="noreferrer">原文</a>
              {paper.status === 'error' ? <button className="btn btn-ghost btn-sm" type="button" disabled={locked} onClick={() => onRetryPaper(paper.id)}>重试</button> : null}
              <button className="btn btn-ghost btn-sm" type="button" disabled={locked} onClick={() => onDeletePaper(paper.id)}>删除</button>
            </div>
          </li>;
        })}</ul> : <p className="workspace-muted">上传后可在当前项目中反复引用。</p>}
      </> : <p className="workspace-muted">选择或新建学习项目后，可以上传论文和图片并在提问时引用。</p>}
    </section>
  </aside>;
}
