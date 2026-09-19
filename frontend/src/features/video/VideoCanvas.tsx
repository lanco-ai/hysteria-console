import { useCallback, useRef } from 'react';
import { Background, Controls, MiniMap, ReactFlow, applyEdgeChanges, applyNodeChanges, type Connection, type Edge, type EdgeChange, type Node, type NodeChange, type ReactFlowInstance } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { VideoNode } from './VideoNode';

export type VideoCanvasProps = {
  nodes: Node[];
  edges: Edge[];
  onNodesChange: (nodes: Node[]) => void;
  onEdgesChange: (edges: Edge[]) => void;
  onConnect: (connection: Connection) => void;
  onSelect?: (node: Node | null) => void;
  onDropNode?: (type: string, position: { x: number; y: number }) => void;
  isValidConnection?: (connection: Connection | Edge) => boolean;
  disabledNodeTypes?: string[];
};

const palette = [
  ['prompt', '提示词', '输入文字'],
  ['image_asset', '图片素材', '上传或引用图片'],
  ['text_to_image', '文生图', '提示词生成图片'],
  ['image_to_video', '图生视频', '图片生成视频'],
  ['first_last_frame_video', '首尾帧视频', '两张图生成视频'],
  ['preview', '结果预览', '查看或下载结果'],
] as const;

export function VideoCanvas({ nodes, edges, onNodesChange, onEdgesChange, onConnect, onSelect, onDropNode, isValidConnection, disabledNodeTypes = [] }: VideoCanvasProps) {
  const nodeTypes = { prompt: VideoNode, image_asset: VideoNode, text_to_image: VideoNode, image_to_video: VideoNode, first_last_frame_video: VideoNode, preview: VideoNode };
  const flow = useRef<ReactFlowInstance | null>(null);
  const handleNodes = useCallback((changes: NodeChange[]) => onNodesChange(applyNodeChanges(changes, nodes)), [nodes, onNodesChange]);
  const handleEdges = useCallback((changes: EdgeChange[]) => onEdgesChange(applyEdgeChanges(changes, edges)), [edges, onEdgesChange]);
  const handleConnect = useCallback((connection: Connection) => onConnect(connection), [onConnect]);
  const handleDragOver = useCallback((event: React.DragEvent) => {
    if (!onDropNode) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
  }, [onDropNode]);
  const handleDrop = useCallback((event: React.DragEvent) => {
    if (!onDropNode || !flow.current) return;
    const type = event.dataTransfer.getData('application/video-node');
    if (!type || disabledNodeTypes.includes(type)) return;
    event.preventDefault();
    const position = flow.current.screenToFlowPosition({ x: event.clientX, y: event.clientY });
    onDropNode(type, position);
  }, [disabledNodeTypes, onDropNode]);
  const handlePaletteDrag = (event: React.DragEvent, type: string) => {
    event.dataTransfer.setData('application/video-node', type);
    event.dataTransfer.effectAllowed = 'copy';
  };
  return <div className="video-canvas-shell">
    <aside className="video-node-palette" aria-label="节点库">
      <div className="video-canvas-panel-heading"><strong>节点库</strong><small>拖入画布创建节点</small></div>
      {palette.map(([type, title, description]) => {
        const disabled = disabledNodeTypes.includes(type);
        return <button key={type} type="button" className="video-palette-node" draggable={!disabled} disabled={disabled} aria-disabled={disabled} onDragStart={event => { if (!disabled) handlePaletteDrag(event, type); }} onClick={() => { if (!disabled) onDropNode?.(type, { x: 80 + (nodes.length % 4) * 210, y: 80 + Math.floor(nodes.length / 4) * 150 }); }}><strong>{title}</strong><small>{disabled ? '当前供应商未验证支持' : description}</small></button>;
      })}
    </aside>
    <div className="video-canvas" aria-label="视频工作流画布" onDragOver={handleDragOver} onDrop={handleDrop}><ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onInit={instance => { flow.current = instance; }} onNodesChange={handleNodes} onEdgesChange={handleEdges} onConnect={handleConnect} {...(isValidConnection ? { isValidConnection } : {})} onNodeClick={(_, node) => onSelect?.(node)} onPaneClick={() => onSelect?.(null)} fitView><Background gap={20} size={1} /><Controls /><MiniMap pannable zoomable /></ReactFlow></div>
  </div>;
}
