import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import type { Connection, Edge, Node, NodeChange } from '@xyflow/react';
import { CodexShell } from '../../shared/CodexShell';
import { createRun, loadWorkflowById, loadVideoCapabilities, loadWorkflows, saveWorkflow, uploadAsset } from './videoApi';
import { VideoRunPanel } from './VideoRunPanel';
import type { VideoCapabilities, VideoRun, VideoWorkflow } from './videoTypes';

const VideoCanvas = lazy(() => import('./VideoCanvas').then(module => ({ default: module.VideoCanvas })));
const NODE_INPUTS: Record<string, string[]> = {
  prompt: [], image_asset: [], text_to_image: ['prompt'], image_to_video: ['image'],
  first_last_frame_video: ['first_frame', 'last_frame'], preview: ['media'],
};
const NODE_OUTPUTS: Record<string, string[]> = {
  prompt: ['text'], image_asset: ['image'], text_to_image: ['image', 'first_frame', 'last_frame'],
  image_to_video: ['video'], first_last_frame_video: ['video'], preview: [],
};
const initialNode: Node = { id: 'prompt-start', type: 'prompt', position: { x: 80, y: 180 }, data: { text: '' } };

function graphFromWorkflow(workflow: VideoWorkflow): { nodes: Node[]; edges: Edge[] } {
  if (!Array.isArray(workflow.nodes) || !Array.isArray(workflow.edges)) return { nodes: [initialNode], edges: [] };
  const nodes = workflow.nodes.flatMap((raw, index) => {
    if (typeof raw.id !== 'string' || typeof raw.type !== 'string' || !Object.hasOwn(NODE_INPUTS, raw.type)) return [];
    const position = raw.position && typeof raw.position === 'object' ? raw.position as Record<string, unknown> : {};
    return [{ id: raw.id, type: raw.type, position: {
      x: typeof position.x === 'number' && Number.isFinite(position.x) ? position.x : 80 + index * 300,
      y: typeof position.y === 'number' && Number.isFinite(position.y) ? position.y : 180,
    }, data: raw.data && typeof raw.data === 'object' && !Array.isArray(raw.data) ? raw.data : {} } as Node];
  });
  const ids = new Set(nodes.map(node => node.id));
  const edges = workflow.edges.flatMap((raw, index) => {
    if (typeof raw.source !== 'string' || typeof raw.target !== 'string' || !ids.has(raw.source) || !ids.has(raw.target)) return [];
    const sourceHandle = raw.sourceHandle || raw.source_port;
    const targetHandle = raw.targetHandle || raw.target_port;
    return [{ id: typeof raw.id === 'string' ? raw.id : `restored-edge-${index}`, source: raw.source, target: raw.target,
      sourceHandle: typeof sourceHandle === 'string' ? sourceHandle : null,
      targetHandle: typeof targetHandle === 'string' ? targetHandle : null } as Edge];
  });
  // Attach the legacy URL to its original connection once, and persist that
  // provenance with future drafts so reloading cannot attach it to a new clip.
  const restoredNodes = nodes.map(node => node.type === 'preview' && typeof node.data.video_url === 'string'
    && node.data.legacy_preview_connection === undefined
    ? { ...node, data: { ...node.data, legacy_preview_connection: previewConnection(node.id, edges) } } : node);
  return { nodes: restoredNodes.length ? restoredNodes : [initialNode], edges };
}

function previewConnection(nodeId: string, edges: Edge[]) {
  const edge = edges.find(item => item.target === nodeId && item.targetHandle === 'media');
  return edge ? JSON.stringify([edge.id, edge.source, edge.sourceHandle]) : '';
}

function graphForSave(nodes: Node[], edges: Edge[]) {
  return {
    nodes: nodes.map(({ id, type, position, data }) => ({ id, type, position, data })),
    edges: edges.map(({ id, source, sourceHandle, target, targetHandle }) => ({ id, source, sourceHandle, target, targetHandle })),
  };
}

function createsCycle(connection: Connection | Edge, edges: Edge[]) {
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

function validConnection(connection: Connection | Edge, nodes: Node[], edges: Edge[]) {
  if (!connection.source || !connection.target || connection.source === connection.target) return false;
  const source = nodes.find(node => node.id === connection.source);
  const target = nodes.find(node => node.id === connection.target);
  const sourceHandle = connection.sourceHandle || '';
  const targetHandle = connection.targetHandle || '';
  if (!source || !target || !NODE_OUTPUTS[source.type || '']?.includes(sourceHandle) || !NODE_INPUTS[target.type || '']?.includes(targetHandle)) return false;
  if (edges.some(edge => edge.target === connection.target && edge.targetHandle === targetHandle)) return false;
  return !createsCycle(connection, edges);
}

function assetUrlFromRef(ref: string) {
  return ref.startsWith('asset://') ? `/api/video/assets/${encodeURIComponent(ref.slice(8))}/content` : '';
}

export function VideoPage({ publicHost }: { publicHost: string }): ReactElement {
  void publicHost;
  const [nodes, setNodes] = useState<Node[]>([initialNode]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [title, setTitle] = useState('未命名作品');
  const [legacyStoryboard, setLegacyStoryboard] = useState<VideoWorkflow['storyboard'] | undefined>();
  const [workflows, setWorkflows] = useState<VideoWorkflow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [capabilities, setCapabilities] = useState<VideoCapabilities | null>(null);
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<'saved' | 'dirty' | 'saving' | 'error'>('saved');
  const [busy, setBusy] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [runStates, setRunStates] = useState<Record<string, string>>({});
  const [invalidAssetRefs, setInvalidAssetRefs] = useState<string[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [message, setMessage] = useState('在画布上右键添加节点，先生成候选图并选择首尾帧。');
  const processedRuns = useRef(new Set<string>());
  const activeTargets = useRef(new Map<string, string | null>());
  const activeRuns = useRef(new Map<string, number>());
  const lastSubmissionAt = useRef(new Map<string, number>());
  const editRevision = useRef(0);
  const savedRevision = useRef(0);
  const workflowEpoch = useRef(0);
  const workflowIdRef = useRef<string | null>(null);
  const saveInFlight = useRef<Promise<string | null> | null>(null);
  const latestSave = useRef<() => Promise<string | null>>(async () => null);
  const selectedNode = nodes.find(node => node.id === selectedNodeId) || null;
  const markDirty = useCallback(() => { editRevision.current += 1; setSaveState('dirty'); }, []);

  const mergeCandidateResults = useCallback((workflow: VideoWorkflow, acknowledgeSelection = false, acceptSelection = false) => {
    if (workflow.id !== workflowIdRef.current) return;
    const remote = graphFromWorkflow(workflow).nodes;
    for (const node of remote) {
      const key = `${workflowEpoch.current}:${node.id}`;
      const prefix = node.type === 'text_to_image' ? 'candidate' : 'video';
      if (activeTargets.current.get(key) === node.data[`${prefix}_run_id`]
          && ['succeeded', 'failed', 'cancelled', 'cancel_unsupported'].includes(String(node.data[`${prefix}_run_state`]))) {
        activeTargets.current.delete(key);
      }
    }
    setNodes(current => current.map(node => {
      const source = remote.find(item => item.id === node.id && item.type === node.type);
      if (!source) return node;
      if (node.type === 'image_to_video' || node.type === 'first_last_frame_video') {
        if (node.data.video_node_token && node.data.video_node_token !== source.data.video_node_token) return node;
        if (Number(source.data.video_run_version || 0) < Number(node.data.video_run_version || 0)) return node;
        if (source.data.video_run_id === node.data.video_run_id
            && ['succeeded', 'failed', 'cancelled', 'cancel_unsupported'].includes(String(node.data.video_run_state))
            && ['queued', 'running'].includes(String(source.data.video_run_state))) return node;
        const data = { ...node.data };
        for (const key of ['video_node_token', 'video_run_id', 'video_run_state', 'video_run_version', 'video_url']) {
          if (source.data[key] !== undefined) data[key] = source.data[key];
        }
        return { ...node, data };
      }
      if (node.type !== 'text_to_image') return node;
      const data = { ...node.data };
      if (Number(source.data.candidate_run_version || 0) >= Number(data.candidate_run_version || 0)) {
        for (const key of ['candidate_run_id', 'candidate_run_state', 'candidate_run_version']) {
          if (source.data[key] !== undefined) data[key] = source.data[key];
        }
      }
      if (Number(source.data.candidate_batch_version || 0) > Number(data.candidate_batch_version || 0)) {
        for (const key of ['candidate_batch_id', 'candidate_batch_version', 'candidate_selection_version', 'candidate_asset_refs', 'selected_first_asset_ref', 'selected_last_asset_ref']) data[key] = source.data[key];
      }
      if (acknowledgeSelection && source.data.candidate_batch_id === data.candidate_batch_id
          && Number(source.data.candidate_selection_version || 0) >= Number(data.candidate_selection_version || 0)) {
        data.candidate_selection_version = source.data.candidate_selection_version;
        if (acceptSelection) {
          data.selected_first_asset_ref = source.data.selected_first_asset_ref;
          data.selected_last_asset_ref = source.data.selected_last_asset_ref;
        }
      }
      return { ...node, data };
    }));
  }, []);
  useEffect(() => {
    if (!workflowId) return;
    const epoch = workflowEpoch.current;
    let active = true;
    const refresh = async () => {
      try {
        const workflow = await loadWorkflowById(workflowId);
        if (active && epoch === workflowEpoch.current) mergeCandidateResults(workflow);
      } catch { /* A later scoped read can recover; keep the editable draft. */ }
    };
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [workflowId, mergeCandidateResults]);

  const loadWorkflow = useCallback((workflow: VideoWorkflow) => {
    const graph = graphFromWorkflow(workflow);
    workflowEpoch.current += 1;
    activeTargets.current.clear();
    activeRuns.current.clear();
    lastSubmissionAt.current.clear();
    workflowIdRef.current = workflow.id;
    editRevision.current = 0;
    savedRevision.current = 0;
    setWorkflowId(workflow.id);
    setTitle(workflow.title || '未命名作品');
    setLegacyStoryboard(workflow.storyboard);
    setNodes(graph.nodes);
    setEdges(graph.edges);
    setSelectedNodeId(null);
    setSaveState('saved');
    const pending = graph.nodes.flatMap(node => {
      const prefix = node.type === 'text_to_image' ? 'candidate' : 'video';
      const id = node.data[`${prefix}_run_id`];
      return typeof id === 'string' ? [{ nodeId: node.id, id, state: String(node.data[`${prefix}_run_state`]) }] : [];
    });
    for (const run of pending) {
      activeRuns.current.set(run.id, workflowEpoch.current);
      if (['queued', 'running'].includes(run.state)) activeTargets.current.set(`${workflowEpoch.current}:${run.nodeId}`, run.id);
    }
    setRunId(pending.length ? pending[pending.length - 1]!.id : null);
    setRunStates(Object.fromEntries(pending.map(run => [run.nodeId, run.state])));
    setInvalidAssetRefs([]);
    setBusy(false);
  }, []);
  useEffect(() => {
    void Promise.allSettled([loadWorkflows(), loadVideoCapabilities()]).then(([workflowResult, capabilityResult]) => {
      if (workflowResult.status === 'fulfilled') {
        setWorkflows(workflowResult.value);
        if (workflowResult.value[0]) loadWorkflow(workflowResult.value[0]);
      }
      if (capabilityResult.status === 'fulfilled') setCapabilities(capabilityResult.value);
      setLoaded(true);
    });
  }, [loadWorkflow]);

  const save = useCallback(async (): Promise<string | null> => {
    if (saveInFlight.current) {
      await saveInFlight.current;
      return editRevision.current > savedRevision.current ? latestSave.current() : workflowIdRef.current;
    }
    const epoch = workflowEpoch.current;
    const revision = editRevision.current;
    const workflowId = workflowIdRef.current;
    const task = (async (): Promise<string | null> => {
      setSaveState('saving');
    try {
      const saved = await saveWorkflow({ ...(workflowId ? { id: workflowId } : {}), title,
        ...(legacyStoryboard ? { storyboard: legacyStoryboard } : {}), ...graphForSave(nodes, edges) });
      setWorkflows(current => [saved, ...current.filter(item => item.id !== saved.id)]);
      if (epoch !== workflowEpoch.current) return null;
      workflowIdRef.current = saved.id;
      mergeCandidateResults(saved, true, editRevision.current === revision);
      savedRevision.current = revision;
      setWorkflowId(saved.id);
      setSaveState(editRevision.current === revision ? 'saved' : 'dirty');
      return saved.id;
    } catch (error) {
      if (epoch !== workflowEpoch.current) return null;
      setSaveState('error');
      setMessage(error instanceof Error ? error.message : '保存失败');
      return null;
    }
    })();
    saveInFlight.current = task;
    try { return await task; }
    finally { if (saveInFlight.current === task) saveInFlight.current = null; }
  }, [edges, legacyStoryboard, nodes, title, mergeCandidateResults]);
  latestSave.current = save;
  useEffect(() => {
    if (saveState !== 'dirty') return;
    const timer = window.setTimeout(() => { void save(); }, 1100);
    return () => window.clearTimeout(timer);
  }, [save, saveState]);
  const ensureSaved = useCallback(() => workflowIdRef.current && saveState === 'saved' && editRevision.current === savedRevision.current
    ? Promise.resolve(workflowIdRef.current) : save(), [saveState, save]);
  const updateNode = useCallback((nodeId: string, patch: Record<string, unknown>) => {
    setNodes(current => current.map(node => node.id === nodeId ? { ...node, data: { ...node.data, ...patch } } : node));
    markDirty();
  }, [markDirty]);

  const incomingFrame = (nodeId: string, port: string) => {
    const edge = edges.find(item => item.target === nodeId && item.targetHandle === port);
    const source = nodes.find(item => item.id === edge?.source);
    if (!source) return '';
    if (source.type === 'text_to_image') return String(source.data[port === 'first_frame' ? 'selected_first_asset_ref' : 'selected_last_asset_ref'] || '');
    return String(source.data.asset_ref || '');
  };
  const videoRunReason = (node: Node) => {
    if (node.type === 'first_last_frame_video') {
      const model = String(node.data.model || '');
      if (!capabilities?.first_last_frame_models?.includes(model)) return capabilities?.first_last_frame.reason || '当前模型未验证支持独立首尾帧';
      const first = incomingFrame(node.id, 'first_frame');
      const last = incomingFrame(node.id, 'last_frame');
      if (!first || !last) return '请先连接并选择首帧和尾帧';
      if (invalidAssetRefs.includes(first)) return '首帧素材不可用';
      if (invalidAssetRefs.includes(last)) return '尾帧素材不可用';
      if (first === last) return '首帧和尾帧请选择不同图片';
    }
    return '';
  };
  const imageRunReason = (node: Node) => Number(node.data.n || 1) > 1 && !capabilities?.image_batch_models?.includes(String(node.data.model || ''))
    ? '当前模型未验证支持批量生图' : '';
  const runNode = useCallback(async (node: Node) => {
    const epoch = workflowEpoch.current;
    const targetKey = `${epoch}:${node.id}`;
    if (activeTargets.current.has(targetKey) || Date.now() - (lastSubmissionAt.current.get(targetKey) || 0) < 1500) return;
    const reason = node.type === 'text_to_image' ? imageRunReason(node) : videoRunReason(node);
    if (reason) { setMessage(reason); return; }
    activeTargets.current.set(targetKey, null);
    lastSubmissionAt.current.set(targetKey, Date.now());
    try {
      const id = await ensureSaved();
      if (epoch !== workflowEpoch.current || !id) { activeTargets.current.delete(targetKey); return; }
      setBusy(true);
      setMessage(node.type === 'text_to_image' ? '正在生成候选图…' : '正在生成视频…');
      const result = await createRun(id, node.id);
      if (epoch !== workflowEpoch.current) { activeTargets.current.delete(targetKey); return; }
      activeTargets.current.set(targetKey, result.id);
      activeRuns.current.set(result.id, epoch);
      setRunId(result.id);
      setRunStates(current => ({ ...current, [node.id]: result.state }));
    } catch (error) {
      activeTargets.current.delete(targetKey);
      if (epoch === workflowEpoch.current) setMessage(error instanceof Error ? error.message : '提交失败');
    } finally {
      if (epoch === workflowEpoch.current) setBusy(false);
    }
  }, [capabilities, edges, ensureSaved, invalidAssetRefs, nodes]);

  const handleRunUpdate = useCallback((run: VideoRun) => {
    const epoch = activeRuns.current.get(run.id);
    if (epoch === undefined || epoch !== workflowEpoch.current) return;
    setRunStates(current => ({ ...current, ...Object.fromEntries(Object.entries(run.node_status || {}).map(([id, status]) => [id, status.state])) }));
    if (['succeeded', 'failed', 'cancelled', 'cancel_unsupported'].includes(run.state) && run.target_node_id
        && activeTargets.current.get(`${epoch}:${run.target_node_id}`) === run.id) activeTargets.current.delete(`${epoch}:${run.target_node_id}`);
    if (run.state !== 'succeeded' || processedRuns.current.has(run.id)) return;
    processedRuns.current.add(run.id);
    const target = run.target_node_id || '';
    const targetNode = nodes.find(node => node.id === target);
    if (['text_to_image', 'first_last_frame_video', 'image_to_video'].includes(targetNode?.type || '')) {
      const workflowId = workflowIdRef.current;
      if (workflowId) void loadWorkflowById(workflowId).then(workflow => {
        if (epoch === workflowEpoch.current) mergeCandidateResults(workflow);
      }).catch(() => { /* The scoped workflow poll retries recovery. */ });
      setMessage(targetNode?.type === 'text_to_image' ? '候选图已生成，请选择首帧和尾帧' : '视频已生成');
    }
  }, [nodes, mergeCandidateResults]);

  const canvasNodes = useMemo(() => nodes.map(node => {
    const refs = Array.isArray(node.data.candidate_asset_refs) ? node.data.candidate_asset_refs as string[] : [];
    const reason = node.type === 'text_to_image' ? imageRunReason(node) : node.type === 'first_last_frame_video' ? videoRunReason(node) : '';
    const previewSource = node.type === 'preview'
      ? nodes.find(source => source.id === edges.find(edge => edge.target === node.id && edge.targetHandle === 'media')?.source)
      : undefined;
    // Resolve from the durable source before rendering, even when run polling
    // is unavailable. Current edges decide where results appear after edits.
    const previewData = previewSource && ['image_to_video', 'first_last_frame_video'].includes(previewSource.type || '')
      && typeof previewSource.data.video_url === 'string'
      ? { video_url: previewSource.data.video_url }
      : node.type === 'preview'
        ? { video_url: node.data.legacy_preview_connection === previewConnection(node.id, edges) ? node.data.video_url : undefined }
        : {};
    return { ...node, data: { ...node.data, ...previewData,
      candidate_urls: refs.map(assetUrlFromRef),
      invalid_asset_refs: invalidAssetRefs,
      onAssetError: (ref: string) => setInvalidAssetRefs(current => current.includes(ref) ? current : [...current, ref]),
      run_state: node.data.candidate_run_state || node.data.video_run_state || runStates[node.id], run_disabled: busy || Boolean(reason), run_reason: reason,
      onChange: (patch: Record<string, unknown>) => updateNode(node.id, patch),
      onRun: () => { void runNode(node); },
    } };
  }), [nodes, edges, capabilities, busy, runStates, invalidAssetRefs, runNode, updateNode]);

  const addNode = (type: string, position: { x: number; y: number }, origin: Node | null) => {
    const id = `${type}-${crypto.randomUUID()}`;
    const imageModel = capabilities?.image_models[0] || 'grok-imagine-image';
    const videoModel = capabilities?.first_last_frame_models?.[0] || capabilities?.video_models[0] || 'grok-imagine-video';
    const data = type === 'prompt' ? { text: '' } : type === 'text_to_image'
      ? { model: imageModel, n: capabilities?.image_batch_models?.includes(imageModel) ? 4 : 1, aspect_ratio: '9:16' }
      : type === 'first_last_frame_video' || type === 'image_to_video' ? { model: videoModel, prompt: '', duration: 5, aspect_ratio: '9:16' } : {};
    const autoConnect = type === 'text_to_image' && origin?.type === 'prompt';
    const nextPosition = autoConnect ? { x: origin.position.x + 330, y: origin.position.y } : { ...position };
    while (nodes.some(node => Math.abs(node.position.x - nextPosition.x) < 280 && Math.abs(node.position.y - nextPosition.y) < 250)) {
      nextPosition.y += 300;
    }
    setNodes(current => [...current, { id, type, position: nextPosition, data }]);
    if (autoConnect) setEdges(current => [...current, { id: `${origin.id}-${id}`, source: origin.id, sourceHandle: 'text', target: id, targetHandle: 'prompt' }]);
    setSelectedNodeId(id);
    setInspectorOpen(true);
    markDirty();
  };
  const deleteNode = (node: Node) => {
    setNodes(current => current.filter(item => item.id !== node.id));
    setEdges(current => current.filter(edge => edge.source !== node.id && edge.target !== node.id));
    if (selectedNodeId === node.id) setSelectedNodeId(null);
    markDirty();
  };
  const duplicateNode = (node: Node) => {
    const id = `${node.type}-${crypto.randomUUID()}`;
    const data = { ...node.data };
    for (const key of ['candidate_run_id', 'candidate_run_state', 'candidate_run_version', 'candidate_batch_id', 'candidate_batch_version', 'candidate_selection_version', 'candidate_node_token', 'video_node_token', 'video_run_id', 'video_run_state', 'video_run_version', 'video_url']) delete data[key];
    setNodes(current => [...current, { ...node, id, position: { x: node.position.x + 42, y: node.position.y + 42 }, data }]);
    setSelectedNodeId(id);
    markDirty();
  };
  const connect = (connection: Connection) => {
    if (!validConnection(connection, nodes, edges)) return;
    setEdges(current => [...current, { ...connection, id: `edge-${crypto.randomUUID()}` } as Edge]);
    markDirty();
  };
  const updateCanvasNodes = (next: Node[], changes: NodeChange[]) => {
    setNodes(current => next.map(node => {
      const original = current.find(item => item.id === node.id);
      return { ...node, data: original?.data || {} };
    }));
    const removed = changes.filter(change => change.type === 'remove').map(change => change.id);
    if (removed.length) setEdges(current => current.filter(edge => !removed.includes(edge.source) && !removed.includes(edge.target)));
    if (changes.some(change => change.type === 'remove' || change.type === 'add' || (change.type === 'position' && change.dragging === false))) markDirty();
  };
  const uploadImage = async (nodeId: string, file: File) => {
    const epoch = workflowEpoch.current;
    setBusy(true);
    try {
      const asset = await uploadAsset(file, file.name || 'frame.png');
      if (epoch !== workflowEpoch.current) return;
      updateNode(nodeId, { label: asset.filename, asset_ref: `asset://${asset.id}`, asset_url: `/api/video/assets/${asset.id}/content` });
      setMessage('图片素材已上传');
    } catch (error) { if (epoch === workflowEpoch.current) setMessage(error instanceof Error ? error.message : '图片上传失败'); }
    finally { if (epoch === workflowEpoch.current) setBusy(false); }
  };
  const startNew = useCallback(() => {
    workflowEpoch.current += 1;
    activeTargets.current.clear();
    activeRuns.current.clear();
    lastSubmissionAt.current.clear();
    workflowIdRef.current = null;
    editRevision.current = 0;
    savedRevision.current = 0;
    setWorkflowId(null); setTitle('未命名作品'); setLegacyStoryboard(undefined);
    setNodes([{ ...initialNode, id: `prompt-${crypto.randomUUID()}` }]); setEdges([]);
    setSelectedNodeId(null); setRunId(null); setRunStates({}); setInvalidAssetRefs([]); setSaveState('saved'); setBusy(false); setMessage('新作品草稿');
  }, []);
  const switchWorkflow = useCallback(async (workflow: VideoWorkflow | null) => {
    const epoch = workflowEpoch.current;
    if (saveInFlight.current || editRevision.current !== savedRevision.current || saveState === 'error') {
      setMessage('正在保存当前作品…');
      while (workflowEpoch.current === epoch) {
        const revision = editRevision.current;
        const savedId = await latestSave.current();
        if (!savedId) return;
        if (workflowEpoch.current !== epoch) return;
        if (revision === editRevision.current && editRevision.current === savedRevision.current) break;
      }
    }
    if (workflowEpoch.current !== epoch) return;
    if (workflow) loadWorkflow(workflow);
    else startNew();
  }, [loadWorkflow, saveState, startNew]);
  const inspectorData = selectedNode?.data || {};
  const modelOptions = selectedNode?.type === 'text_to_image' ? capabilities?.image_models || [] : capabilities?.video_models || [];
  const selectedModel = String(inspectorData.model || '');
  const options = [...new Set([selectedModel, ...modelOptions].filter(Boolean))];
  return <CodexShell active="video" pageTitle="AI 视频" authStatus="authenticated" subtitle="节点视频工作台">
    <section className="video-workspace">
      <header className="video-toolbar video-primary-toolbar">
        <div className="video-primary-toolbar-meta">
          <div className="video-primary-heading"><h2>AI 视频画布</h2><p role="status">{message}</p></div>
          <label className="video-workflow-select">作品<select className="select input-sm" value={workflowId || ''} onChange={event => {
            const workflow = workflows.find(item => item.id === event.target.value);
            void switchWorkflow(workflow || null);
          }}><option value="">新作品</option>{workflows.map(workflow => <option key={workflow.id} value={workflow.id}>{workflow.title || workflow.id.slice(0, 8)}</option>)}</select></label>
        </div>
        <div className="video-primary-toolbar-controls">
          <label className="video-canvas-title"><span className="sr-only">作品名称</span><input className="input input-sm" aria-label="作品名称" value={title} onChange={event => { setTitle(event.target.value); markDirty(); }} /></label>
          <span className="video-save-state" data-state={saveState}>{saveState === 'saved' ? '已保存' : saveState === 'saving' ? '保存中…' : saveState === 'error' ? '保存失败' : '有未保存修改'}</span>
          <button type="button" className="btn btn-secondary btn-md" onClick={() => { void save(); }} disabled={busy}>保存</button>
        </div>
      </header>
      <div className="video-canvas-editor video-canvas-editor-primary" aria-label="工作流节点画布">
        {loaded ? <Suspense fallback={<div className="video-canvas-loading">正在加载画布…</div>}><VideoCanvas nodes={canvasNodes} edges={edges} selectedNode={selectedNode}
          onNodesChange={updateCanvasNodes} onEdgesChange={next => { setEdges(next); markDirty(); }} onConnect={connect}
          isValidConnection={connection => validConnection(connection, nodes, edges)}
          onSelect={node => { setSelectedNodeId(node?.id || null); setInspectorOpen(Boolean(node)); }} onAddNode={addNode}
          onDuplicateNode={duplicateNode} onDeleteNode={deleteNode}
          firstLastReason={capabilities?.first_last_frame.supported ? undefined : capabilities?.first_last_frame.reason || '当前供应商未验证支持'} /></Suspense> : <div className="video-canvas-loading">正在加载已保存工作流…</div>}
        <aside className={`video-node-inspector ${inspectorOpen ? 'is-open' : ''}`} aria-label="节点属性">
          <div className="video-canvas-panel-heading"><strong>节点属性</strong><button type="button" className="video-inspector-close" onClick={() => setInspectorOpen(false)}>关闭属性</button></div>
          {selectedNode ? <>
            <label>显示名称<input value={String(inspectorData.label || '')} onChange={event => updateNode(selectedNode.id, { label: event.target.value })} placeholder="节点名称" /></label>
            {selectedNode.type === 'prompt' ? <p className="video-inspector-note">在提示词节点内直接编辑画面描述。</p> : null}
            {selectedNode.type === 'image_asset' ? <><label>上传图片素材<input aria-label="上传图片素材" type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={event => { const file = event.currentTarget.files?.[0]; if (file) void uploadImage(selectedNode.id, file); event.currentTarget.value = ''; }} /></label>{typeof inspectorData.asset_url === 'string' && inspectorData.asset_url ? <img className="video-asset-preview" src={inspectorData.asset_url} alt={String(inspectorData.label || '图片素材')} /> : null}</> : null}
            {['text_to_image', 'image_to_video', 'first_last_frame_video'].includes(selectedNode.type || '') ? <>
              <label>模型{options.length ? <select value={selectedModel} onChange={event => updateNode(selectedNode.id, { model: event.target.value })}>{options.map(model => <option key={model} value={model}>{model}</option>)}</select> : <input value={selectedModel} onChange={event => updateNode(selectedNode.id, { model: event.target.value })} />}</label>
              {selectedNode.type !== 'text_to_image' ? <label>运动提示词<textarea value={String(inspectorData.prompt || '')} onChange={event => updateNode(selectedNode.id, { prompt: event.target.value })} rows={5} /></label> : null}
              <label>画幅<select value={String(inspectorData.aspect_ratio || '9:16')} onChange={event => updateNode(selectedNode.id, { aspect_ratio: event.target.value })}><option value="9:16">9:16</option><option value="16:9">16:9</option><option value="1:1">1:1</option></select></label>
              {selectedNode.type === 'text_to_image' ? <label>候选数量<select value={Number(inspectorData.n || 1)} disabled={!capabilities?.image_batch_models?.includes(selectedModel)} onChange={event => updateNode(selectedNode.id, { n: Number(event.target.value) })}>{Array.from({ length: 10 }, (_, index) => <option key={index + 1} value={index + 1}>{index + 1}</option>)}</select></label>
                : <label>时长（秒）<input type="number" min={1} max={30} value={Number(inspectorData.duration || 5)} onChange={event => updateNode(selectedNode.id, { duration: Math.max(1, Math.min(30, Number(event.target.value) || 1)) })} /></label>}
              {selectedNode.type === 'text_to_image' && !capabilities?.image_batch_models?.includes(selectedModel) ? <p className="video-inspector-warning">当前模型未验证支持批量生图，仅可生成单张图片。</p> : null}
              {selectedNode.type === 'first_last_frame_video' && videoRunReason(selectedNode) ? <p className="video-inspector-warning">{videoRunReason(selectedNode)}</p> : null}
            </> : null}
            {selectedNode.type === 'preview' ? <p className="video-inspector-note">视频完成后可在预览节点播放和下载。</p> : null}
          </> : <p className="video-inspector-note">选择节点查看属性。桌面右键画布或使用添加节点按钮继续。</p>}
        </aside>
      </div>
      <VideoRunPanel runId={runId} onRunChange={handleRunUpdate} />
    </section>
  </CodexShell>;
}
