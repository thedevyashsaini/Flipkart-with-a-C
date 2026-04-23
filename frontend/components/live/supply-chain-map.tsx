"use client"

import { useMemo } from "react"
import {
  Background,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"

type LiveNode = {
  id: string
  name: string
  type: "hub" | "warehouse" | "airport" | "city"
  region: string
  capacity: number
  avg_hold_ticks_per_ton: number
  x: number
  y: number
}

type LiveEdge = {
  id: string
  source: string
  target: string
  base_eta: number
  base_cost: number
}

type LiveShipment = {
  shipment_id: string
  source_node_id: string
  destination_node_id: string
  current_node_id: string
  target_node_id: string
  state: string
}

const TYPE_STYLE: Record<LiveNode["type"], string> = {
  hub: "border-amber-500/30 bg-amber-500/10 text-amber-100",
  warehouse: "border-sky-500/30 bg-sky-500/10 text-sky-100",
  airport: "border-emerald-500/30 bg-emerald-500/10 text-emerald-100",
  city: "border-violet-500/30 bg-violet-500/10 text-violet-100",
}

type LiveNodeData = {
  code: string
  type: LiveNode["type"]
}

function LiveWorldNode({ data }: NodeProps<Node<LiveNodeData>>) {
  const heat = 0
  const heatShadow = `0 10px 24px rgba(0,0,0,0.28), 0 0 ${10 + heat * 24}px rgba(245, 158, 11, ${0.12 + heat * 0.32})`

  return (
    <div
      className={`relative min-w-24 rounded-xl border px-3 py-2 text-center transition-[opacity,box-shadow,filter,border-color] duration-150 ${TYPE_STYLE[data.type]} opacity-100`}
      style={{
        boxShadow: heatShadow,
        filter: `saturate(${1 + heat * 0.35}) brightness(${1 + heat * 0.2})`,
      }}
    >
      <Handle id="t-top" type="target" position={Position.Top} isConnectable={false} className="!size-2 !border !border-white/80 !bg-black" />
      <Handle id="s-top" type="source" position={Position.Top} isConnectable={false} className="!size-2 !border !border-white/80 !bg-black" />
      <Handle id="t-right" type="target" position={Position.Right} isConnectable={false} className="!size-2 !border !border-white/80 !bg-black" />
      <Handle id="s-right" type="source" position={Position.Right} isConnectable={false} className="!size-2 !border !border-white/80 !bg-black" />
      <Handle id="t-bottom" type="target" position={Position.Bottom} isConnectable={false} className="!size-2 !border !border-white/80 !bg-black" />
      <Handle id="s-bottom" type="source" position={Position.Bottom} isConnectable={false} className="!size-2 !border !border-white/80 !bg-black" />
      <Handle id="t-left" type="target" position={Position.Left} isConnectable={false} className="!size-2 !border !border-white/80 !bg-black" />
      <Handle id="s-left" type="source" position={Position.Left} isConnectable={false} className="!size-2 !border !border-white/80 !bg-black" />

      <div className="text-[12px] font-semibold tracking-wide">{data.code}</div>
      <div className="text-[10px] opacity-70">{data.type}</div>
    </div>
  )
}

const nodeTypes = { worldNode: LiveWorldNode }

function pickSourceHandle(dx: number, dy: number): string {
  if (Math.abs(dx) >= Math.abs(dy)) {
    return dx >= 0 ? "s-right" : "s-left"
  }
  return dy >= 0 ? "s-bottom" : "s-top"
}

function pickTargetHandle(dx: number, dy: number): string {
  if (Math.abs(dx) >= Math.abs(dy)) {
    return dx >= 0 ? "t-left" : "t-right"
  }
  return dy >= 0 ? "t-top" : "t-bottom"
}

function toCode(name: string, type: LiveNode["type"]): string {
  const city = name.split(" ")[0]?.replace(/[^a-zA-Z]/g, "") || "NODE"
  const cityPart = city.toUpperCase().slice(0, 3).padEnd(3, "X")
  const typePart = type.slice(0, 1).toUpperCase()
  return `${cityPart}${typePart}`
}

export function SupplyChainMap({ nodes, edges, shipments = [] }: { nodes: LiveNode[]; edges: LiveEdge[]; shipments?: LiveShipment[] }) {
  const positionById = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>()
    nodes.forEach(node => {
      map.set(node.id, { x: node.x, y: node.y })
    })
    return map
  }, [nodes])

  const rfNodes = useMemo<Node<LiveNodeData>[]>(() => {
    return nodes.map(node => ({
      id: node.id,
      type: "worldNode",
      position: { x: node.x, y: node.y },
      data: { code: toCode(node.name, node.type), type: node.type },
      draggable: false,
    }))
  }, [nodes])

  const rfEdges = useMemo<Edge[]>(() => {
    const activeLaneCounts = new Map<string, number>()
    shipments.forEach(shipment => {
      if (shipment.state !== "in_transit") {
        return
      }
      const key = `${shipment.current_node_id}->${shipment.target_node_id}`
      activeLaneCounts.set(key, (activeLaneCounts.get(key) ?? 0) + 1)
    })

    return edges.map(edge => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: (() => {
        const sourcePos = positionById.get(edge.source) ?? { x: 0, y: 0 }
        const targetPos = positionById.get(edge.target) ?? { x: 0, y: 0 }
        return pickSourceHandle(targetPos.x - sourcePos.x, targetPos.y - sourcePos.y)
      })(),
      targetHandle: (() => {
        const sourcePos = positionById.get(edge.source) ?? { x: 0, y: 0 }
        const targetPos = positionById.get(edge.target) ?? { x: 0, y: 0 }
        return pickTargetHandle(targetPos.x - sourcePos.x, targetPos.y - sourcePos.y)
      })(),
      label: String(edge.base_eta),
      type: "default",
      pathOptions: { curvature: edge.source < edge.target ? 0.28 : 0.52 },
      markerEnd: { type: MarkerType.ArrowClosed, width: 10, height: 10 },
      style: {
        stroke: (activeLaneCounts.get(`${edge.source}->${edge.target}`) ?? 0) > 0
          ? "rgba(251, 191, 36, 0.92)"
          : "color-mix(in oklab, var(--muted-foreground) 58%, transparent)",
        strokeWidth: (activeLaneCounts.get(`${edge.source}->${edge.target}`) ?? 0) > 0
          ? Math.min(4.2, 1.5 + (activeLaneCounts.get(`${edge.source}->${edge.target}`) ?? 0) * 0.22)
          : 1.2,
      },
      labelStyle: {
        fontSize: 10,
        fill: (activeLaneCounts.get(`${edge.source}->${edge.target}`) ?? 0) > 0 ? "rgb(252 211 77)" : "var(--muted-foreground)",
        fontWeight: 600,
      },
      labelBgStyle: {
        fill: "var(--background)",
        fillOpacity: 0.84,
        stroke: "var(--border)",
        strokeWidth: 0.5,
        rx: 3,
        ry: 3,
      },
      animated: (activeLaneCounts.get(`${edge.source}->${edge.target}`) ?? 0) > 0,
      title: (activeLaneCounts.get(`${edge.source}->${edge.target}`) ?? 0) > 0 ? `${activeLaneCounts.get(`${edge.source}->${edge.target}`)} shipment(s) in transit` : undefined,
    }))
  }, [edges, positionById, shipments])

  return (
    <div className="h-full min-h-0 w-full">
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        nodesConnectable={false}
        nodesDraggable={false}
        elementsSelectable={false}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.2}
        maxZoom={2}
        attributionPosition="top-right"
      >
        <Background gap={20} size={1} color="color-mix(in oklab, var(--muted-foreground) 32%, transparent)" />
      </ReactFlow>
    </div>
  )
}
