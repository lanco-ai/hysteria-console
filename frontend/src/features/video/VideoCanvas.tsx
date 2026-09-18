import { useCallback } from 'react';
import { Background, Controls, MiniMap, ReactFlow, applyEdgeChanges, applyNodeChanges, type Connection, type Edge, type EdgeChange, type Node, type NodeChange } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { VideoNode } from './VideoNode';

export type VideoCanvasProps = {
  nodes: Node[];
  edges: Edge[];
  onNodesChange: (nodes: Node[]) => void;
  onEdgesChange: (edges: Edge[]) => void;
  onConnect: (connection: Connection) => void;
  onSelect?: (node: Node | null) => void;
};

export function VideoCanvas({ nodes, edges, onNodesChange, onEdgesChange, onConnect, onSelect }: VideoCanvasProps) {
  const nodeTypes = { prompt: VideoNode, image_asset: VideoNode, text_to_image: VideoNode, image_to_video: VideoNode, first_last_frame_video: VideoNode, preview: VideoNode };
  const handleNodes = useCallback((changes: NodeChange[]) => onNodesChange(applyNodeChanges(changes, nodes)), [nodes, onNodesChange]);
  const handleEdges = useCallback((changes: EdgeChange[]) => onEdgesChange(applyEdgeChanges(changes, edges)), [edges, onEdgesChange]);
  const handleConnect = useCallback((connection: Connection) => onConnect(connection), [onConnect]);
  return <div className="video-canvas" aria-label="视频工作流画布"><ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={handleNodes} onEdgesChange={handleEdges} onConnect={handleConnect} onNodeClick={(_, node) => onSelect?.(node)} onPaneClick={() => onSelect?.(null)} fitView><Background gap={20} size={1} /><Controls /><MiniMap pannable zoomable /></ReactFlow></div>;
}
