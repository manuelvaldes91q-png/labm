"use client";

import { useCallback, useRef, useState, type DragEvent } from "react";
import {
  ReactFlow,
  addEdge,
  useNodesState,
  useEdgesState,
  Controls,
  Background,
  BackgroundVariant,
  type Connection,
  type Node,
  type Edge,
  type OnConnect,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { DeviceModel } from "@/lib/mikrotik-models";
import { connectNodes, createNode, deleteNode, deleteConnection } from "@/lib/api";
import MikroTikNode, { type MikroTikNodeData } from "@/components/MikroTikNode";
import Terminal from "@/components/Terminal";

const nodeTypes = { mikrotik: MikroTikNode };

type AppNode = Node<MikroTikNodeData>;
type AppEdge = Edge;

let nodeCounter = 0;

function nextId(): string {
  nodeCounter += 1;
  return `node_${nodeCounter}`;
}

function buildContainerName(model: DeviceModel): string {
  return `${model.id}-${Date.now().toString(36)}`;
}

function FlowCanvas() {
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<AppNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<AppEdge>([]);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [statusMsg, setStatusMsg] = useState<string>("");
  const { screenToFlowPosition } = useReactFlow();

  const showStatus = useCallback((msg: string, durationMs = 4000) => {
    setStatusMsg(msg);
    setTimeout(() => setStatusMsg(""), durationMs);
  }, []);

  const onConnect: OnConnect = useCallback(
    async (connection: Connection) => {
      if (!connection.source || !connection.target) return;

      const sourceNode = nodes.find((n) => n.id === connection.source);
      const targetNode = nodes.find((n) => n.id === connection.target);
      if (!sourceNode || !targetNode) return;

      const sourceData = sourceNode.data;
      const targetData = targetNode.data;

      if (!sourceData.containerName || !targetData.containerName) {
        showStatus("Both nodes must be provisioned before connecting.");
        return;
      }

      const existingSourceEdges = edges.filter(
        (e) => e.source === connection.source,
      );
      const existingTargetEdges = edges.filter(
        (e) => e.target === connection.target,
      );
      const nextSourceIndex = existingSourceEdges.length + 1;
      const nextTargetIndex =
        existingTargetEdges.length + existingSourceEdges.length + 1;

      const edgeId = `e-${connection.source}-${connection.target}`;

      setEdges((eds) =>
        addEdge(
          {
            ...connection,
            id: edgeId,
            type: "default",
            animated: true,
            style: { stroke: "#3b82f6", strokeWidth: 2 },
            label: `eth`,
            labelStyle: { fontSize: 10, fill: "#94a3b8" },
          },
          eds,
        ),
      );

      showStatus(`Linking ${sourceData.name} \u2194 ${targetData.name}...`);

      try {
        await connectNodes({
          node_a: sourceData.containerName,
          node_b: targetData.containerName,
          index_a: nextSourceIndex,
          index_b: nextTargetIndex,
        });
        showStatus(
          `Connected: ${sourceData.name}:ether${nextSourceIndex} \u2194 ${targetData.name}:ether${nextTargetIndex}`,
        );
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Connection failed";
        showStatus(`Error: ${msg}`);
        setEdges((eds) => eds.filter((e) => e.id !== edgeId));
      }
    },
    [nodes, edges, setEdges, showStatus],
  );

  const onDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    async (e: DragEvent) => {
      e.preventDefault();
      const raw = e.dataTransfer.getData("application/reactflow");
      if (!raw) return;

      const model = JSON.parse(raw) as DeviceModel;
      const position = screenToFlowPosition({
        x: e.clientX,
        y: e.clientY,
      });

      const containerName = buildContainerName(model);
      const nodeId = nextId();

      const newNode: AppNode = {
        id: nodeId,
        type: "mikrotik",
        position,
        data: {
          ...model,
          containerName,
          containerStatus: "provisioning",
        },
      };

      setNodes((nds) => [...nds, newNode]);

      try {
        const resp = await createNode({
          name: containerName,
          node_type: model.node_type,
        });
        setNodes((nds) =>
          nds.map((n) =>
            n.id === nodeId
              ? {
                  ...n,
                  data: {
                    ...n.data,
                    containerStatus: "running",
                    wanIp: resp.wan_ip ?? undefined,
                    winboxPort: resp.winbox_port ?? undefined,
                  },
                }
              : n,
          ),
        );
        const extras = [];
        if (resp.wan_ip) extras.push(`WAN ${resp.wan_ip}`);
        if (resp.winbox_port) extras.push(`Winbox :${resp.winbox_port}`);
        showStatus(
          `${model.name} provisioned${extras.length ? ` (${extras.join(", ")})` : ""}`,
        );
      } catch (err) {
        setNodes((nds) =>
          nds.map((n) =>
            n.id === nodeId
              ? {
                  ...n,
                  data: {
                    ...n.data,
                    containerStatus: "failed",
                  },
                }
              : n,
          ),
        );
        const msg = err instanceof Error ? err.message : "Provisioning failed";
        showStatus(`Error provisioning ${model.name}: ${msg}`);
      }
    },
    [screenToFlowPosition, setNodes, showStatus],
  );

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: AppNode) => {
      setSelectedNode(node.data.containerName ?? node.id);
    },
    [],
  );

  const onNodesDelete = useCallback(
    async (deleted: AppNode[]) => {
      for (const node of deleted) {
        if (node.data.containerName) {
          try {
            await deleteNode(node.data.containerName);
            showStatus(`Deleted ${node.data.containerName}`);
          } catch {
            /* container may already be removed */
          }
        }
        setEdges((eds) =>
          eds.filter(
            (e) => e.source !== node.id && e.target !== node.id,
          ),
        );
      }
    },
    [setEdges, showStatus],
  );

  const onEdgesDelete = useCallback(
    async (deleted: Edge[]) => {
      for (const edge of deleted) {
        const sourceNode = nodes.find((n) => n.id === edge.source);
        if (sourceNode?.data.containerName) {
          const vethName = `veth-${sourceNode.data.containerName}-ether`;
          try {
            await deleteConnection(vethName);
          } catch {
            /* best-effort cleanup */
          }
        }
      }
    },
    [nodes],
  );

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {statusMsg && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-50 rounded-md bg-neutral-800 border border-neutral-700 px-4 py-2 text-xs text-neutral-200 shadow-lg font-mono">
          {statusMsg}
        </div>
      )}

      <div ref={reactFlowWrapper} className="flex-1 min-h-0">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onDragOver={onDragOver}
          onDrop={onDrop}
          onNodeClick={onNodeClick}
          onNodesDelete={onNodesDelete}
          onEdgesDelete={onEdgesDelete}
          nodeTypes={nodeTypes}
          fitView
          proOptions={{ hideAttribution: true }}
          defaultEdgeOptions={{
            type: "default",
            animated: true,
            style: { stroke: "#3b82f6", strokeWidth: 2 },
          }}
        >
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#262626" />
          <Controls
            className="!bg-neutral-800 !border-neutral-700 !rounded-lg [&>button]:!bg-neutral-800 [&>button]:!border-neutral-700 [&>button]:!text-neutral-400 [&>button:hover]:!bg-neutral-700"
          />
        </ReactFlow>
      </div>

      <div className="h-[250px] shrink-0 border-t border-neutral-800">
        <Terminal nodeName={selectedNode ?? undefined} className="h-full" />
      </div>
    </div>
  );
}

export default function TopologyCanvas() {
  return (
    <ReactFlowProvider>
      <FlowCanvas />
    </ReactFlowProvider>
  );
}
