import { lazy, Suspense, useCallback, useEffect, useState, type ReactElement } from 'react';
import type { Connection, Edge, Node } from '@xyflow/react';
import { CodexShell } from '../../shared/CodexShell';
import { createRun, loadVideoCapabilities, loadWorkflows, saveWorkflow, uploadAsset } from './videoApi';
import { VideoRunPanel } from './VideoRunPanel';
import { VideoSettingsDrawer } from './VideoSettingsDrawer';
import { VIDEO_TEMPLATES, type VideoTemplate } from './VideoTemplates';
import { createDefaultStoryboard, VideoStoryboard } from './VideoStoryboard';
import type { VideoCapabilities, VideoRun, VideoStoryboard as Storyboard, VideoStoryboardShot, VideoWorkflow } from './videoTypes';

const VideoCanvas = lazy(() => import('./VideoCanvas').then(module => ({ default: module.VideoCanvas })));

const NODE_INPUTS: Record<string, string[]> = {
  prompt: [], image_asset: [], text_to_image: ['prompt'], image_to_video: ['image'],
  first_last_frame_video: ['first_frame', 'last_frame'], preview: ['media'],
};
const NODE_OUTPUTS: Record<string, string[]> = {
  prompt: ['text'], image_asset: ['image'], text_to_image: ['image'],
  image_to_video: ['video'], first_last_frame_video: ['video'], preview: [],
};

function wouldCreateCycle(connection: Connection | Edge, edges: Edge[]): boolean {
  const adjacency = new Map<string, string[]>();
  edges.forEach(edge => adjacency.set(edge.source, [...(adjacency.get(edge.source) || []), edge.target]));
  const pending = [connection.target];
  const seen = new Set<string>();
  while (pending.length) {
    const current = pending.shift();
    if (!current || seen.has(current)) continue;
    if (current === connection.source) return true;
    seen.add(current);
    pending.push(...(adjacency.get(current) || []));
  }
  return false;
}

function validCanvasConnection(connection: Connection | Edge, nodes: Node[], edges: Edge[]): boolean {
  if (!connection.source || !connection.target || connection.source === connection.target) return false;
  const source = nodes.find(node => node.id === connection.source);
  const target = nodes.find(node => node.id === connection.target);
  const sourceHandle = connection.sourceHandle || '';
  const targetHandle = connection.targetHandle || '';
  if (!source || !target || !NODE_OUTPUTS[source.type || '']?.includes(sourceHandle) || !NODE_INPUTS[target.type || '']?.includes(targetHandle)) return false;
  if (edges.some(edge => edge.target === connection.target && (edge.targetHandle || '') === targetHandle)) return false;
  return !wouldCreateCycle(connection, edges);
}

function storyboardGraph(storyboard: Storyboard): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [];
  const edges: Edge[] = [];
  storyboard.shots.forEach((shot, index) => {
    const y = index * 360;
    const prefix = shot.id;
    nodes.push(
      { id: `${prefix}:prompt`, type: 'prompt', position: { x: 40, y }, data: { text: shot.image_prompt, shot_id: prefix, label: shot.title } },
      { id: `${prefix}:image`, type: shot.image_asset_id ? 'image_asset' : 'text_to_image', position: { x: 320, y }, data: shot.image_asset_id ? { asset_ref: `asset://${shot.image_asset_id}`, shot_id: prefix } : { model: shot.image_model, prompt: shot.image_prompt, aspect_ratio: storyboard.aspect_ratio, shot_id: prefix } },
      { id: `${prefix}:video`, type: 'image_to_video', position: { x: 620, y }, data: { model: shot.video_model, prompt: shot.motion_prompt, duration: shot.duration, aspect_ratio: storyboard.aspect_ratio, shot_id: prefix } },
      { id: `${prefix}:preview`, type: 'preview', position: { x: 920, y }, data: { shot_id: prefix } },
    );
    if (!shot.image_asset_id) {
      edges.push({ id: `${prefix}:prompt-image`, source: `${prefix}:prompt`, sourceHandle: 'text', target: `${prefix}:image`, targetHandle: 'prompt' });
    }
    edges.push(
      { id: `${prefix}:image-video`, source: `${prefix}:image`, sourceHandle: 'image', target: `${prefix}:video`, targetHandle: 'image' },
      { id: `${prefix}:video-preview`, source: `${prefix}:video`, sourceHandle: 'video', target: `${prefix}:preview`, targetHandle: 'media' },
    );
  });
  return { nodes, edges };
}

function normalizeStoryboard(value: unknown): Storyboard {
  const defaults = createDefaultStoryboard();
  if (!value || typeof value !== 'object') return defaults;
  const candidate = value as Partial<Storyboard>;
  const shots = Array.isArray(candidate.shots) ? candidate.shots : defaults.shots;
  return {
    ...defaults,
    ...candidate,
    shots: shots.map((shot, index) => ({ ...defaults.shots[0]!, ...(shot as object), id: String((shot as Partial<Storyboard['shots'][number]>).id || `shot-${index + 1}`) } as VideoStoryboardShot)),
  };
}

function storyboardFromWorkflow(workflow: VideoWorkflow): Storyboard {
  if (workflow.storyboard) return normalizeStoryboard(workflow.storyboard);
  const fallback = createDefaultStoryboard();
  const prompt = workflow.nodes.find(node => node.type === 'prompt');
  const image = workflow.nodes.find(node => node.type === 'text_to_image');
  const promptData = prompt?.data as Record<string, unknown> | undefined;
  const imageData = image?.data as Record<string, unknown> | undefined;
  return {
    ...fallback,
    title: workflow.title || fallback.title,
    shots: [{ ...fallback.shots[0]!, title: workflow.title || fallback.shots[0]!.title, image_prompt: String(promptData?.text || imageData?.prompt || '') }],
  };
}

function workflowGraph(nodes: Node[], edges: Edge[]): { nodes: Array<Record<string, unknown>>; edges: Array<Record<string, unknown>> } {
  return {
    nodes: nodes.map(({ id, type, position, data }) => ({ id, type: type || 'prompt', position, data })),
    edges: edges.map(({ id, source, sourceHandle, target, targetHandle }) => ({ id, source, sourceHandle, target, targetHandle })),
  };
}

export function VideoPage({ publicHost }: { publicHost: string }): ReactElement {
  void publicHost;
  const initialStoryboard = createDefaultStoryboard();
  const [storyboard, setStoryboard] = useState<Storyboard>(initialStoryboard);
  const [nodes, setNodes] = useState<Node[]>(() => storyboardGraph(initialStoryboard).nodes);
  const [edges, setEdges] = useState<Edge[]>(() => storyboardGraph(initialStoryboard).edges);
  const [workflows, setWorkflows] = useState<VideoWorkflow[]>([]);
  const [capabilities, setCapabilities] = useState<VideoCapabilities | null>(null);
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<'saved' | 'dirty' | 'saving' | 'error'>('saved');
  const [busy, setBusy] = useState(false);
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [message, setMessage] = useState('先整理故事和分镜，再生成素材');

  const selectedNode = nodes.find(node => node.id === selectedNodeId) || null;

  const refreshCapabilities = useCallback(async () => {
    try { setCapabilities(await loadVideoCapabilities()); }
    catch { setCapabilities(null); }
  }, []);

  useEffect(() => {
    void Promise.allSettled([loadWorkflows(), loadVideoCapabilities()]).then(([workflowResult, capabilityResult]) => {
      if (workflowResult.status === 'fulfilled') {
        setWorkflows(workflowResult.value);
        const first = workflowResult.value[0];
        if (first) {
          const next = storyboardFromWorkflow(first);
          const graph = storyboardGraph(next);
          setWorkflowId(first.id); setStoryboard(next); setNodes(graph.nodes); setEdges(graph.edges);
        }
      }
      if (capabilityResult.status === 'fulfilled') setCapabilities(capabilityResult.value);
    });
  }, []);

  const updateStoryboard = useCallback((next: Storyboard) => {
    setStoryboard(next);
    const graph = storyboardGraph(next);
    setNodes(graph.nodes); setEdges(graph.edges); setSaveState('dirty');
    setSelectedNodeId(null);
  }, []);

  const addCanvasNode = useCallback((type: string, position: { x: number; y: number }) => {
    const id = `node-${type}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
    const imageModel = capabilities?.image_models[0] || 'grok-imagine-image';
    const videoModel = capabilities?.video_models[0] || 'grok-imagine-video';
    const data: Record<string, unknown> = type === 'prompt'
      ? { text: '', label: '提示词' }
      : type === 'image_asset'
        ? { label: '图片素材' }
        : type === 'text_to_image'
          ? { model: imageModel, prompt: '', aspect_ratio: storyboard.aspect_ratio }
          : type === 'image_to_video'
            ? { model: videoModel, prompt: '', duration: 5, aspect_ratio: storyboard.aspect_ratio }
            : type === 'first_last_frame_video'
              ? { model: videoModel, prompt: '', duration: 5, aspect_ratio: storyboard.aspect_ratio }
              : {};
    setNodes(current => [...current, { id, type, position, data }]);
    setSelectedNodeId(id);
    setSaveState('dirty');
  }, [capabilities, storyboard.aspect_ratio]);

  const updateSelectedNode = useCallback((patch: Record<string, unknown>) => {
    if (!selectedNodeId) return;
    setNodes(current => current.map(node => node.id === selectedNodeId ? { ...node, data: { ...node.data, ...patch } } : node));
    setSaveState('dirty');
  }, [selectedNodeId]);

  const applyTemplate = useCallback((template: VideoTemplate) => {
    const imageModel = capabilities?.image_models[0] || 'grok-imagine-image';
    const videoModel = capabilities?.video_models[0] || 'grok-imagine-video';
    const templateNodes = template.nodes.map(node => ({
      ...node,
      data: {
        ...node.data,
        ...(node.type === 'text_to_image' ? { model: node.data.model || imageModel, aspect_ratio: storyboard.aspect_ratio } : {}),
        ...(node.type === 'image_to_video' || node.type === 'first_last_frame_video' ? { model: node.data.model || videoModel, duration: 5, aspect_ratio: storyboard.aspect_ratio } : {}),
      },
    })) as Node[];
    setNodes(templateNodes);
    setEdges(template.edges as Edge[]);
    setSelectedNodeId(null);
    setSaveState('dirty');
    setMessage(`已载入模板：${template.title}`);
  }, [capabilities, storyboard.aspect_ratio]);

  const save = useCallback(async (): Promise<string | null> => {
    setBusy(true); setSaveState('saving');
    try {
      const graph = workflowGraph(nodes, edges);
      const payload = { title: storyboard.title, storyboard, ...graph };
      const saved = await saveWorkflow(workflowId ? { ...payload, id: workflowId } : payload);
      setWorkflowId(saved.id); setWorkflows(current => [saved, ...current.filter(item => item.id !== saved.id)]); setSaveState('saved'); setMessage('作品已保存'); return saved.id;
    } catch (error) {
      setSaveState('error'); setMessage(error instanceof Error ? error.message : '保存失败'); return null;
    } finally { setBusy(false); }
  }, [edges, nodes, storyboard, workflowId]);

  useEffect(() => {
    if (saveState !== 'dirty') return;
    const timer = window.setTimeout(() => { void save(); }, 1200);
    return () => window.clearTimeout(timer);
  }, [save, saveState]);

  const ensureSaved = useCallback(async (): Promise<string | null> => {
    if (!workflowId || saveState !== 'saved') return save();
    return workflowId;
  }, [save, saveState, workflowId]);

  const runAll = useCallback(async () => {
    if (storyboard.shots.some(shot => !shot.image_prompt.trim() && !shot.image_asset_id)) { setMessage('请先为每个分镜填写图片提示词或上传图片'); return; }
    const id = await ensureSaved();
    if (!id) return;
    setBusy(true);
    try { const result = await createRun(id); setRunId(result.id); setMessage(`全部分镜已提交（${result.state}）`); }
    catch (error) { setMessage(error instanceof Error ? error.message : '提交失败'); }
    finally { setBusy(false); }
  }, [ensureSaved, storyboard.shots]);

  const runShot = useCallback(async (shot: Storyboard['shots'][number]) => {
    const id = await ensureSaved();
    if (!id) return;
    setBusy(true); setMessage(`${shot.title} 已提交`);
    try { const result = await createRun(id, shot.id); setRunId(result.id); }
    catch (error) { setMessage(error instanceof Error ? error.message : '提交失败'); }
    finally { setBusy(false); }
  }, [ensureSaved]);

  const runCanvas = useCallback(async () => {
    const id = await ensureSaved();
    if (!id) return;
    setBusy(true); setMessage('画布工作流已提交');
    try { const result = await createRun(id); setRunId(result.id); }
    catch (error) { setMessage(error instanceof Error ? error.message : '画布提交失败'); }
    finally { setBusy(false); }
  }, [ensureSaved]);

  const uploadImage = useCallback(async (shot: Storyboard['shots'][number], file: File) => {
    setBusy(true); setMessage(`${shot.title} 图片上传中…`);
    try {
      const asset = await uploadAsset(file, file.name || `${shot.id}.png`);
      updateStoryboard({ ...storyboard, shots: storyboard.shots.map(item => item.id === shot.id ? { ...item, image_asset_id: asset.id, image_url: `/api/video/assets/${encodeURIComponent(asset.id)}/content`, image_state: 'succeeded' } : item) });
      setMessage(`${shot.title} 图片已加入`);
    } catch (error) { setMessage(error instanceof Error ? error.message : '图片上传失败'); }
    finally { setBusy(false); }
  }, [storyboard, updateStoryboard]);

  const handleRunUpdate = useCallback((run: VideoRun) => {
    const assets = run.assets || {};
    const nodeStatus = run.node_status || {};
    const stateFor = (nodeId: string | undefined, fallback: VideoStoryboardShot['image_state']): VideoStoryboardShot['image_state'] => {
      const state = nodeId ? nodeStatus[nodeId]?.state : undefined;
      if (state === 'succeeded') return 'succeeded';
      if (state === 'failed') return 'failed';
      if (state === 'running') return 'running';
      if (state === 'queued') return 'queued';
      return fallback;
    };
    const targets = run.shot_id
      ? [{ shotId: run.shot_id, nodes: run.workflow?.nodes || [] }]
      : storyboard.shots.map(shot => ({ shotId: shot.id, nodes: run.workflow?.nodes || [] }));
    let changed = false;
    const shots = storyboard.shots.map(shot => {
      const target = targets.find(item => item.shotId === shot.id);
      if (!target) return shot;
      const nodes = target.nodes.filter(node => {
        const data = node.data;
        return Boolean(data && typeof data === 'object' && (data as Record<string, unknown>).shot_id === shot.id);
      });
      if (!nodes.length && !run.shot_id) return shot;
      const imageNode = nodes.find(node => node.type === 'text_to_image' || node.type === 'image_asset');
      const videoNode = nodes.find(node => node.type === 'image_to_video' || node.type === 'first_last_frame_video');
      const imageId = imageNode?.id as string | undefined;
      const videoId = videoNode?.id as string | undefined;
      const imageUrl = imageId ? assets[imageId] : assets[`${shot.id}:image`];
      const videoUrl = videoId ? assets[videoId] : assets[`${shot.id}:video`];
      const next = {
        ...shot,
        image_state: stateFor(imageId, run.state === 'failed' ? 'failed' : shot.image_state),
        video_state: stateFor(videoId, run.state === 'failed' ? 'failed' : shot.video_state),
        ...(imageUrl ? { image_url: imageUrl } : {}),
        ...(videoUrl ? { video_url: videoUrl } : {}),
      };
      if (next.image_state !== shot.image_state || next.video_state !== shot.video_state || next.image_url !== shot.image_url || next.video_url !== shot.video_url) changed = true;
      return next;
    });
    if (changed) updateStoryboard({ ...storyboard, shots });
  }, [storyboard, updateStoryboard]);

  const selectWorkflow = (workflow: VideoWorkflow) => {
    const next = storyboardFromWorkflow(workflow);
    const graph = storyboardGraph(next);
    setWorkflowId(workflow.id); setStoryboard(next); setNodes(graph.nodes); setEdges(graph.edges); setSelectedNodeId(null); setSaveState('saved'); setMessage('已载入作品');
  };
  const connect = (connection: Connection) => {
    if (!validCanvasConnection(connection, nodes, edges)) return;
    setEdges(current => [...current, { ...connection, id: `${connection.source}-${connection.target}-${Date.now()}` } as Edge]);
    setSaveState('dirty');
  };

  const updateCanvasNodes = (next: Node[]) => {
    const ids = new Set(next.map(node => node.id));
    setNodes(next);
    setEdges(current => current.filter(edge => ids.has(edge.source) && ids.has(edge.target)));
    if (selectedNodeId && !ids.has(selectedNodeId)) setSelectedNodeId(null);
    setSaveState('dirty');
  };

  const inspectorData = selectedNode?.data && typeof selectedNode.data === 'object' ? selectedNode.data as Record<string, unknown> : {};
  const inspectorModelOptions = selectedNode?.type === 'text_to_image'
    ? [...new Set([String(inspectorData.model || ''), ...(capabilities?.image_models || [])].filter(Boolean))]
    : [...new Set([String(inspectorData.model || ''), ...(capabilities?.video_models || [])].filter(Boolean))];

  return <CodexShell active="video" pageTitle="AI 视频" authStatus="authenticated" subtitle="漫剧分镜工作台">
    <section className="video-workspace video-storyboard-workspace">
      <header className="video-toolbar"><div><h2>AI 漫剧分镜</h2><p>{message}</p></div><div className="video-toolbar-actions"><label className="video-workflow-select">作品<select value={workflowId || ''} onChange={event => { const item = workflows.find(workflow => workflow.id === event.target.value); if (item) selectWorkflow(item); }}><option value="">新作品</option>{workflows.map(item => <option key={item.id} value={item.id}>{item.title || item.id.slice(0, 8)}</option>)}</select></label><button type="button" className="button ghost" onClick={() => setSettingsOpen(true)}>API 设置</button></div></header>
      <VideoStoryboard storyboard={storyboard} capabilities={capabilities} saveState={saveState} busy={busy} onChange={updateStoryboard} onSave={() => { void save(); }} onRunShot={shot => { void runShot(shot); }} onUploadImage={(shot, file) => { void uploadImage(shot, file); }} onRunAll={() => { void runAll(); }} onOpenCanvas={() => setCanvasOpen(true)} />
      <VideoRunPanel runId={runId} onRunChange={handleRunUpdate} />
      <footer className="video-run-status"><span>编辑分镜不会自动产生费用；提交后可在任务面板查看状态。</span></footer>
    </section>
    {canvasOpen ? <div className="video-canvas-layer" role="dialog" aria-modal="true"><div className="video-canvas-dialog"><header><div><strong>工作流画布</strong><small>节点可拖动、连线，运行前请确认输入完整</small></div><div className="video-canvas-header-actions"><label className="video-template-select">模板<select defaultValue="" onChange={event => { const template = VIDEO_TEMPLATES.find(item => item.id === event.target.value); if (template) applyTemplate(template); }}><option value="">选择模板…</option>{VIDEO_TEMPLATES.map(template => <option key={template.id} value={template.id}>{template.title}</option>)}</select></label><button type="button" className="button secondary" onClick={() => { void runCanvas(); }} disabled={busy}>运行画布</button><button type="button" className="button ghost" onClick={() => setCanvasOpen(false)}>关闭</button></div></header><div className="video-canvas-editor"><Suspense fallback={<div className="video-canvas-loading">正在加载画布…</div>}><VideoCanvas nodes={nodes} edges={edges} onNodesChange={updateCanvasNodes} onEdgesChange={next => { setEdges(next); setSaveState('dirty'); }} onConnect={connect} isValidConnection={connection => validCanvasConnection(connection, nodes, edges)} onSelect={node => setSelectedNodeId(node?.id || null)} onDropNode={addCanvasNode} /></Suspense><aside className="video-node-inspector" aria-label="节点属性"><div className="video-canvas-panel-heading"><strong>节点属性</strong><small>{selectedNode ? selectedNode.type : '未选择节点'}</small></div>{selectedNode ? <><label>显示名称<input value={String(inspectorData.label || '')} onChange={event => updateSelectedNode({ label: event.target.value })} placeholder="节点名称" /></label>{selectedNode.type === 'prompt' ? <label>文本<textarea value={String(inspectorData.text || '')} onChange={event => updateSelectedNode({ text: event.target.value })} rows={7} placeholder="输入提示词…" /></label> : null}{selectedNode.type === 'image_asset' ? <p className="video-inspector-note">请在分镜卡片中上传图片，再将素材节点连接到视频节点。</p> : null}{selectedNode.type !== 'prompt' && selectedNode.type !== 'image_asset' && selectedNode.type !== 'preview' ? <><label>模型{inspectorModelOptions.length ? <select value={String(inspectorData.model || '')} onChange={event => updateSelectedNode({ model: event.target.value })}>{inspectorModelOptions.map(model => <option key={model} value={model}>{model}</option>)}</select> : <input value={String(inspectorData.model || '')} onChange={event => updateSelectedNode({ model: event.target.value })} placeholder="模型 ID" />}</label><label>提示词<textarea value={String(inspectorData.prompt || '')} onChange={event => updateSelectedNode({ prompt: event.target.value })} rows={5} placeholder="描述画面或运动…" /></label><label>画幅<select value={String(inspectorData.aspect_ratio || storyboard.aspect_ratio)} onChange={event => updateSelectedNode({ aspect_ratio: event.target.value })}><option value="9:16">9:16</option><option value="16:9">16:9</option><option value="1:1">1:1</option></select></label><label>时长（秒）<input type="number" min={1} max={30} value={Number(inspectorData.duration || 5)} onChange={event => updateSelectedNode({ duration: Math.max(1, Math.min(30, Number(event.target.value) || 1)) })} /></label>{selectedNode.type === 'first_last_frame_video' && !capabilities?.first_last_frame.supported ? <p className="video-inspector-warning">当前供应商未验证独立首尾帧，运行会被安全拒绝。</p> : null}</> : null}{selectedNode.type === 'preview' ? <p className="video-inspector-note">该节点用于查看上游图片或视频结果。</p> : null}</> : <p className="video-inspector-note">从左侧节点库拖入节点，或点击画布中的节点查看属性。</p>}</aside></div></div></div> : null}
    <VideoSettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} onSaved={() => { void refreshCapabilities(); }} />
  </CodexShell>;
}
