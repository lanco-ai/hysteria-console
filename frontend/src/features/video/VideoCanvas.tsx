import { useCallback, useEffect, useRef, useState } from 'react';
import { Background, Controls, MiniMap, ReactFlow, applyEdgeChanges, applyNodeChanges, type Connection, type Edge, type EdgeChange, type Node, type NodeChange, type ReactFlowInstance } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { VideoNode } from './VideoNode';

const nodeTypes = { prompt: VideoNode, image_asset: VideoNode, text_to_image: VideoNode, image_to_video: VideoNode, first_last_frame_video: VideoNode, preview: VideoNode };
const menuItems = [
  ['prompt', '添加提示词'], ['text_to_image', '文生图'], ['image_asset', '图片素材'],
  ['first_last_frame_video', '首尾帧视频'], ['preview', '结果预览'],
] as const;

type Point = { x: number; y: number };
type Menu = { x: number; y: number; position: Point; node: Node | null; kind: 'pane' | 'node' };
export type VideoCanvasProps = {
  nodes: Node[];
  edges: Edge[];
  selectedNode: Node | null;
  onNodesChange: (nodes: Node[], changes: NodeChange[]) => void;
  onEdgesChange: (edges: Edge[]) => void;
  onConnect: (connection: Connection) => void;
  onSelect: (node: Node | null) => void;
  onAddNode: (type: string, position: Point, selectedNode: Node | null) => void;
  onDuplicateNode: (node: Node) => void;
  onDeleteNode: (node: Node) => void;
  isValidConnection: (connection: Connection | Edge) => boolean;
  firstLastReason?: string | undefined;
};

export function VideoCanvas({ nodes, edges, selectedNode, onNodesChange, onEdgesChange, onConnect, onSelect, onAddNode, onDuplicateNode, onDeleteNode, isValidConnection, firstLastReason }: VideoCanvasProps) {
  const flow = useRef<ReactFlowInstance | null>(null);
  const shell = useRef<HTMLDivElement | null>(null);
  const menuElement = useRef<HTMLDivElement | null>(null);
  const [menu, setMenu] = useState<Menu | null>(null);
  useEffect(() => {
    if (!menu) return;
    menuElement.current?.querySelector('button')?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setMenu(null); }
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      const buttons = Array.from(menuElement.current?.querySelectorAll('button') || []);
      const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
      const next = (current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
      if (buttons[next]) { event.preventDefault(); buttons[next].focus(); }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [menu]);
  const handleNodes = useCallback((changes: NodeChange[]) => onNodesChange(applyNodeChanges(changes, nodes), changes), [nodes, onNodesChange]);
  const handleEdges = useCallback((changes: EdgeChange[]) => onEdgesChange(applyEdgeChanges(changes, edges)), [edges, onEdgesChange]);
  const openMenu = (clientX: number, clientY: number, node: Node | null, kind: Menu['kind']) => {
    const bounds = shell.current?.getBoundingClientRect();
    if (!bounds || !flow.current) return;
    setMenu({
      x: Math.max(8, Math.min(clientX - bounds.left, bounds.width - 214)),
      y: Math.max(8, Math.min(clientY - bounds.top, bounds.height - (kind === 'node' ? 114 : 260))),
      position: flow.current.screenToFlowPosition({ x: clientX, y: clientY }), node, kind,
    });
  };
  return <div className="video-canvas-shell" ref={shell} onKeyDown={event => { if (event.key === 'Escape') setMenu(null); }}>
    <button type="button" className="video-add-node-touch btn btn-secondary" onClick={event => {
      const bounds = shell.current?.getBoundingClientRect();
      if (!bounds) return;
      openMenu(bounds.left + 20, bounds.top + 60, selectedNode, 'pane');
      event.currentTarget.focus();
    }}>＋ 添加节点</button>
    <div className="video-canvas" aria-label="视频工作流画布">
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onInit={instance => { flow.current = instance; }}
        onNodesChange={handleNodes} onEdgesChange={handleEdges} onConnect={onConnect}
        isValidConnection={isValidConnection}
        onNodeClick={(_, node) => { setMenu(null); onSelect(node); }}
        onPaneClick={() => { setMenu(null); onSelect(null); }}
        onPaneContextMenu={event => { event.preventDefault(); openMenu(event.clientX, event.clientY, selectedNode, 'pane'); }}
        onNodeContextMenu={(event, node) => { event.preventDefault(); openMenu(event.clientX, event.clientY, node, 'node'); }}
        fitView><Background gap={20} size={1} /><Controls /><MiniMap pannable zoomable /></ReactFlow>
    </div>
    {menu ? <div className="video-context-backdrop" onClick={() => setMenu(null)}>
      <div role="menu" ref={menuElement} aria-label={menu.kind === 'node' ? '节点操作' : '添加节点'} className="video-context-menu" style={{ left: menu.x, top: menu.y }} onClick={event => event.stopPropagation()}>
        {menu.kind === 'node' ? <>
          <button type="button" role="menuitem" onClick={() => { onDuplicateNode(menu.node!); setMenu(null); }}>复制节点</button>
          <button type="button" role="menuitem" onClick={() => { onDeleteNode(menu.node!); setMenu(null); }}>删除节点</button>
        </> : menuItems.map(([type, label]) => <button key={type} type="button" role="menuitem" onClick={() => { onAddNode(type, menu.position, menu.node || selectedNode); setMenu(null); }}>{label}{type === 'first_last_frame_video' && firstLastReason ? <small>{firstLastReason}</small> : null}</button>)}
      </div>
    </div> : null}
  </div>;
}
