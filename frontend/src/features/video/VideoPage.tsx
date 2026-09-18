import { lazy, Suspense, useCallback, useEffect, useMemo, useState, type ReactElement } from 'react';
import type { Connection, Edge, Node } from '@xyflow/react';
import { CodexShell } from '../../shared/CodexShell';
import { VIDEO_TEMPLATES } from './VideoTemplates';
import { createRun, loadVideoCapabilities, loadWorkflows, saveWorkflow } from './videoApi';
import type { VideoCapabilities, VideoWorkflow } from './videoTypes';

const VideoCanvas = lazy(() => import('./VideoCanvas').then(module => ({ default: module.VideoCanvas })));
const INITIAL_TEMPLATE = VIDEO_TEMPLATES[0]!;

function templateNodes(template: typeof VIDEO_TEMPLATES[number]): Node[] {
  return template.nodes.map(node => ({ ...node, type: node.type, data: { ...node.data } })) as Node[];
}
function templateEdges(template: typeof VIDEO_TEMPLATES[number]): Edge[] { return template.edges.map(edge => ({ ...edge })) as Edge[]; }

export function VideoPage({ publicHost }: { publicHost: string }): ReactElement {
  void publicHost;
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [workflows, setWorkflows] = useState<VideoWorkflow[]>([]);
  const [capabilities, setCapabilities] = useState<VideoCapabilities | null>(null);
  const [selectedTemplate, setSelectedTemplate] = useState(INITIAL_TEMPLATE.id);
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [message, setMessage] = useState('选择模板开始');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void Promise.allSettled([loadWorkflows(), loadVideoCapabilities()]).then(([workflowResult, capabilityResult]) => {
      if (workflowResult.status === 'fulfilled') setWorkflows(workflowResult.value);
      if (capabilityResult.status === 'fulfilled') setCapabilities(capabilityResult.value);
    });
  }, []);
  const template = useMemo(() => VIDEO_TEMPLATES.find(item => item.id === selectedTemplate) || INITIAL_TEMPLATE, [selectedTemplate]);
  const selectTemplate = useCallback((id: string) => {
    const next = VIDEO_TEMPLATES.find(item => item.id === id) || INITIAL_TEMPLATE;
    setSelectedTemplate(next.id); setNodes(templateNodes(next)); setEdges(templateEdges(next)); setWorkflowId(null); setMessage(next.title);
  }, []);
  useEffect(() => { if (nodes.length === 0) selectTemplate(INITIAL_TEMPLATE.id); }, [nodes.length, selectTemplate]);

  const save = async () => {
    setBusy(true);
    try {
      const payload = { title: template.title, nodes: nodes.map(({ id, type, position, data }) => ({ id, type: type || 'prompt', position, data })), edges: edges.map(({ id, source, sourceHandle, target, targetHandle }) => ({ id, source, sourceHandle, target, targetHandle })) };
      const saved = await saveWorkflow(workflowId ? { ...payload, id: workflowId } : payload);
      setWorkflowId(saved.id); setWorkflows(current => [saved, ...current.filter(item => item.id !== saved.id)]); setMessage('工作流已保存');
    } catch (error) { setMessage(error instanceof Error ? error.message : '保存失败'); } finally { setBusy(false); }
  };
  const run = async () => {
    if (!workflowId) { await save(); return; }
    setBusy(true);
    try { const result = await createRun(workflowId); setMessage(`任务 ${result.state}`); } catch (error) { setMessage(error instanceof Error ? error.message : '提交失败'); } finally { setBusy(false); }
  };
  const connect = (connection: Connection) => { if (connection.source && connection.target && connection.source !== connection.target) setEdges(current => [...current, { ...connection, id: `${connection.source}-${connection.target}-${Date.now()}` } as Edge]); };

  return <CodexShell active="video" pageTitle="AI 视频" authStatus="authenticated" subtitle="工作流">
    <section className="video-workspace">
      <header className="video-toolbar"><div><h2>AI 视频工作流</h2><p>{message}</p></div><div className="video-toolbar-actions"><button type="button" className="button secondary" onClick={save} disabled={busy}>保存</button><button type="button" className="button primary" onClick={run} disabled={busy}>运行工作流</button></div></header>
      <div className="video-workspace-grid">
        <aside className="video-panel video-library"><h3>工作流模板</h3>{VIDEO_TEMPLATES.map(item => <button type="button" key={item.id} className={item.id === selectedTemplate ? 'video-template active' : 'video-template'} onClick={() => selectTemplate(item.id)}>{item.title}</button>)}<h3>已保存</h3>{workflows.slice(0, 5).map(item => <button type="button" key={item.id} className="video-template" onClick={() => { setWorkflowId(item.id); setNodes(item.nodes as Node[]); setEdges(item.edges as Edge[]); }}>{item.title || item.id.slice(0, 8)}</button>)}</aside>
        <main className="video-canvas-wrap"><Suspense fallback={<div className="video-canvas-loading">正在加载画布…</div>}><VideoCanvas nodes={nodes} edges={edges} onNodesChange={setNodes} onEdgesChange={setEdges} onConnect={connect}/></Suspense></main>
        <aside className="video-panel video-inspector"><h3>节点设置</h3><p>选择画布节点编辑参数。</p><div className="video-capability-summary"><strong>供应商能力</strong><span>{capabilities ? `${capabilities.image_models.length} 个图片模型 · ${capabilities.video_models.length} 个视频模型` : '尚未连接'}</span>{capabilities && !capabilities.first_last_frame.supported ? <small>首尾帧尚未由当前供应商验证</small> : null}</div></aside>
      </div>
      <footer className="video-run-status"><span>当前任务</span><strong>未运行</strong><span>编辑画布不会自动产生费用</span></footer>
    </section>
  </CodexShell>;
}
