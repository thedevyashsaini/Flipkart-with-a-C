"use client"

import { useEffect, useMemo, useState } from "react"
import {
  Background,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"

import type { World } from "@/lib/types/world"

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"

type NodeData = {
  code: string
  name: string
  type: World["nodes"][number]["type"]
  region: string
  isHighlighted: boolean
  isDimmed: boolean
}

const TYPE_STYLE: Record<NodeData["type"], string> = {
  hub: "border-amber-500/30 bg-amber-500/10 text-amber-100",
  warehouse: "border-sky-500/30 bg-sky-500/10 text-sky-100",
  airport: "border-emerald-500/30 bg-emerald-500/10 text-emerald-100",
  city: "border-violet-500/30 bg-violet-500/10 text-violet-100",
}

function WorldNode({ data }: NodeProps<Node<NodeData>>) {
  const emphasisClass = data.isDimmed
    ? "opacity-35 saturate-50"
    : data.isHighlighted
      ? "opacity-100 saturate-110 brightness-110 shadow-[0_14px_30px_rgba(0,0,0,0.38)]"
      : "opacity-100"

  return (
    <div
      className={`relative min-w-24 rounded-xl border px-3 py-2 text-center shadow-[0_10px_24px_rgba(0,0,0,0.28)] transition-[opacity,box-shadow,filter] duration-150 ${TYPE_STYLE[data.type]} ${emphasisClass}`}
      title={data.name}
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

const nodeTypes = { worldNode: WorldNode }

const REGION_LABELS: Record<string, string> = {
  "IN-NORTH": "India North",
  "IN-WEST": "India West",
  "IN-SOUTH": "India South",
  "IN-EAST": "India East",
  ME: "Middle East",
  EU: "Europe",
}

const REGION_ORDER = ["IN-WEST", "IN-NORTH", "IN-SOUTH", "IN-EAST", "ME", "EU"]

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

function toCode(name: string, type: NodeData["type"]): string {
  const city = name.split(" ")[0]?.replace(/[^a-zA-Z]/g, "") || "NODE"
  const cityPart = city.toUpperCase().slice(0, 3).padEnd(3, "X")
  const typePart = type.slice(0, 1).toUpperCase()
  return `${cityPart}${typePart}`
}

function prettyRegion(region: string): string {
  if (REGION_LABELS[region]) {
    return REGION_LABELS[region]
  }
  return region.replace(/-/g, " ")
}

function buildWorldGraph(world: World, highlightedRegion: string | null): { nodes: Node<NodeData>[]; edges: Edge[] } {
  const codeById = new Map<string, string>()
  const usedCodes = new Set<string>()

  world.nodes.forEach(node => {
    const base = toCode(node.name, node.type)
    let code = base
    let suffix = 0
    while (usedCodes.has(code)) {
      code = `${base.slice(0, 3)}${String.fromCharCode(65 + (suffix % 26))}`
      suffix += 1
    }
    usedCodes.add(code)
    codeById.set(node.id, code)
  })

  const positionById = new Map<string, { x: number; y: number }>()
  world.nodes.forEach(node => {
    positionById.set(node.id, { x: node.x, y: node.y })
  })

  const nodes: Node<NodeData>[] = world.nodes.map(node => ({
    id: node.id,
    type: "worldNode",
    position: positionById.get(node.id) ?? { x: 0, y: 0 },
    draggable: true,
    data: {
      code: codeById.get(node.id) ?? node.id,
      name: node.name,
      type: node.type,
      region: node.region,
      isHighlighted: highlightedRegion === node.region,
      isDimmed: Boolean(highlightedRegion) && highlightedRegion !== node.region,
    },
  }))

  const edges: Edge[] = world.edges.map(edge => {
    const sourcePos = positionById.get(edge.source) ?? { x: 0, y: 0 }
    const targetPos = positionById.get(edge.target) ?? { x: 0, y: 0 }
    const dx = targetPos.x - sourcePos.x
    const dy = targetPos.y - sourcePos.y

    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: pickSourceHandle(dx, dy),
      targetHandle: pickTargetHandle(dx, dy),
      label: String(edge.base_eta),
      type: "default",
      pathOptions: { curvature: 0.45 },
      markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12 },
      style: {
        stroke: "color-mix(in oklab, var(--muted-foreground) 58%, transparent)",
        strokeWidth: 1.2,
      },
      labelStyle: { fontSize: 10, fill: "var(--muted-foreground)", fontWeight: 600 },
      labelBgStyle: {
        fill: "var(--background)",
        fillOpacity: 0.85,
        stroke: "var(--border)",
        strokeWidth: 0.5,
        rx: 3,
        ry: 3,
      },
    }
  })

  return { nodes, edges }
}

export default function Page() {
  const [world, setWorld] = useState<World | null>(null)
  const [error, setError] = useState("")
  const [highlightedRegion, setHighlightedRegion] = useState<string | null>(null)
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<NodeData>>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])

  useEffect(() => {
    async function loadWorld() {
      try {
        const response = await fetch(`${API_BASE}/world`, { cache: "no-store" })
        if (!response.ok) {
          throw new Error(`Failed to fetch world: ${response.status}`)
        }
        const data = (await response.json()) as World
        setWorld(data)
      } catch (err) {
        setError((err as Error).message)
      }
    }

    loadWorld()
  }, [])

  const regions = useMemo(() => {
    if (!world) {
      return []
    }
    const unique = new Set(world.nodes.map(node => node.region))
    return Array.from(unique).sort((a, b) => {
      const aIndex = REGION_ORDER.indexOf(a)
      const bIndex = REGION_ORDER.indexOf(b)
      const aRank = aIndex === -1 ? Number.MAX_SAFE_INTEGER : aIndex
      const bRank = bIndex === -1 ? Number.MAX_SAFE_INTEGER : bIndex
      if (aRank !== bRank) {
        return aRank - bRank
      }
      return prettyRegion(a).localeCompare(prettyRegion(b))
    })
  }, [world])

  const graph = useMemo(() => {
    if (!world) {
      return { nodes: [], edges: [] }
    }
    return buildWorldGraph(world, highlightedRegion)
  }, [world, highlightedRegion])

  useEffect(() => {
    setNodes(graph.nodes)
    setEdges(graph.edges)
  }, [graph, setNodes, setEdges])

  return (
    <main className="grid min-h-svh place-items-center bg-gradient-to-br from-background via-background to-muted/40 p-6">
      <section className="h-[84vh] w-full max-w-7xl overflow-hidden rounded-none border border-border bg-card">
        {error ? (
          <div className="grid h-full place-items-center text-sm text-destructive">{error}</div>
        ) : !world ? (
          <div className="grid h-full place-items-center text-sm text-muted-foreground">Loading world graph...</div>
        ) : (
          <div className="flex h-full flex-col">
            <div className="border-b border-border/80 bg-card/95 backdrop-blur-sm" onMouseLeave={() => setHighlightedRegion(null)}>
              <div
                className="grid w-full"
                style={{ gridTemplateColumns: `repeat(${Math.max(regions.length, 1)}, minmax(0, 1fr))` }}
              >
                {regions.map(region => {
                  const active = highlightedRegion === region
                  return (
                    <button
                      key={region}
                      type="button"
                      onMouseEnter={() => setHighlightedRegion(region)}
                      onFocus={() => setHighlightedRegion(region)}
                      className={`border-r border-border/70 px-2 py-2 text-center text-xs font-medium transition-colors last:border-r-0 md:text-sm ${
                        active
                          ? "bg-foreground/10 text-foreground"
                          : "bg-transparent text-muted-foreground hover:bg-foreground/5 hover:text-foreground"
                      }`}
                    >
                      {prettyRegion(region)}
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="min-h-0 flex-1">
              <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                nodeTypes={nodeTypes}
                nodesConnectable={false}
                connectOnClick={false}
                fitView
                fitViewOptions={{ padding: 0.18 }}
                minZoom={0.2}
                maxZoom={2}
                attributionPosition="top-right"
              >
                <Background gap={20} size={1} color="color-mix(in oklab, var(--muted-foreground) 32%, transparent)" />
              </ReactFlow>
            </div>
          </div>
        )}
      </section>
    </main>
  )
}
