"use client"

import { useEffect, useMemo, useRef, useState } from "react"
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

import type { DemandState } from "@/lib/types/demand"
import type { SimulationState } from "@/lib/types/simulation"
import type { World } from "@/lib/types/world"

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"

type NodeData = {
  code: string
  id: string
  name: string
  type: World["nodes"][number]["type"]
  region: string
  capacity: number
  avgHoldTicksPerTon: number
  isHighlighted: boolean
  isDimmed: boolean
  utilization: number
  queuedShipments: number
  inboundShipments: number
  outboundShipments: number
  holdingShipments: number
  consumedShipmentsTick: number
  activeLoadTons: number
  isFailed: boolean
  onOpenDetails?: (node: {
    id: string
    code: string
    name: string
    region: string
    queuedShipments: number
    inboundShipments: number
    outboundShipments: number
    holdingShipments: number
    consumedShipmentsTick: number
    activeLoadTons: number
    capacity: number
    avgHoldTicksPerTon: number
  }) => void
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

  const heat = Math.max(0, Math.min(1, data.utilization))
  const heatShadow = data.isFailed
    ? "0 10px 24px rgba(0, 0, 0, 0.28), 0 0 39.9587px rgba(130, 72, 72, 0.56)"
    : `0 10px 24px rgba(0,0,0,0.28), 0 0 ${10 + heat * 24}px rgba(245, 158, 11, ${0.12 + heat * 0.32})`
  const pulse = data.queuedShipments > 8 || data.utilization > 0.85
  const nodeTitle = `${data.name}\nload ${data.activeLoadTons.toFixed(1)}t / cap ${data.capacity.toFixed(0)}\nqueued ${data.queuedShipments} | inbound ${data.inboundShipments} | outbound ${data.outboundShipments}\nholding ${data.holdingShipments} | consumed(tick) ${data.consumedShipmentsTick}\navg hold ${data.avgHoldTicksPerTon.toFixed(2)} ticks/ton`

  return (
    <div
      className={`relative min-w-24 rounded-xl border px-3 py-2 text-center transition-[opacity,box-shadow,filter,border-color] duration-150 ${TYPE_STYLE[data.type]} ${emphasisClass}`}
      style={{
        boxShadow: heatShadow,
        filter: `saturate(${1 + heat * 0.35}) brightness(${1 + heat * 0.2})`,
        borderColor: data.isFailed ? "rgba(248, 113, 113, 0.72)" : undefined,
      }}
      title={nodeTitle}
      onContextMenu={event => {
        event.preventDefault()
        data.onOpenDetails?.({
          id: data.id,
          code: data.code,
          name: data.name,
          region: data.region,
          queuedShipments: data.queuedShipments,
          inboundShipments: data.inboundShipments,
          outboundShipments: data.outboundShipments,
          holdingShipments: data.holdingShipments,
          consumedShipmentsTick: data.consumedShipmentsTick,
          activeLoadTons: data.activeLoadTons,
          capacity: data.capacity,
          avgHoldTicksPerTon: data.avgHoldTicksPerTon,
        })
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
      {pulse ? <div className={`absolute -inset-1 rounded-xl border animate-pulse ${data.isFailed ? "border-rose-400/60" : "border-amber-300/40"}`} /> : null}
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

type DemandLogEntry = {
  id: number
  tick: number
  text: string
}

type SelectedNodeDetails = {
  id: string
  code: string
  name: string
  region: string
  queuedShipments: number
  inboundShipments: number
  outboundShipments: number
  holdingShipments: number
  consumedShipmentsTick: number
  activeLoadTons: number
  capacity: number
  avgHoldTicksPerTon: number
}

type SelectedEdgeDetails = {
  corridorId: string
  sourceCode: string
  targetCode: string
  forward: {
    baseEta: number
    inTransitShipments: number
    inTransitLoad: number
    avgRemainingTicks: number
    minRemainingTicks: number
    maxRemainingTicks: number
  }
  reverse: {
    baseEta: number
    inTransitShipments: number
    inTransitLoad: number
    avgRemainingTicks: number
    minRemainingTicks: number
    maxRemainingTicks: number
  }
}

function toSelectedNodeDetails(world: World | null, simState: SimulationState | null, nodeId: string | null): SelectedNodeDetails | null {
  if (!world || !nodeId) {
    return null
  }
  const node = world.nodes.find(item => item.id === nodeId)
  if (!node) {
    return null
  }
  const stat = simState?.node_stats?.[node.id]
  return {
    id: node.id,
    code: toCode(node.name, node.type),
    name: node.name,
    region: node.region,
    queuedShipments: stat?.queued_shipments ?? 0,
    inboundShipments: stat?.inbound_shipments ?? 0,
    outboundShipments: stat?.outbound_shipments ?? 0,
    holdingShipments: stat?.holding_shipments ?? 0,
    consumedShipmentsTick: stat?.consumed_shipments_tick ?? 0,
    activeLoadTons: stat?.active_load_tons ?? 0,
    capacity: node.capacity,
    avgHoldTicksPerTon: node.avg_hold_ticks_per_ton,
  }
}

function toSelectedEdgeDetails(world: World | null, simState: SimulationState | null, corridorKey: string | null): SelectedEdgeDetails | null {
  if (!world || !corridorKey) {
    return null
  }
  const [left, right] = corridorKey.split("|")
  if (!left || !right) {
    return null
  }
  const forwardEdge = world.edges.find(item => item.source === left && item.target === right)
  const reverseEdge = world.edges.find(item => item.source === right && item.target === left)
  if (!forwardEdge && !reverseEdge) {
    return null
  }
  const leftNode = world.nodes.find(item => item.id === left)
  const rightNode = world.nodes.find(item => item.id === right)
  const forwardStat = forwardEdge ? simState?.edge_stats?.[forwardEdge.id] : undefined
  const reverseStat = reverseEdge ? simState?.edge_stats?.[reverseEdge.id] : undefined

  return {
    corridorId: corridorKey,
    sourceCode: leftNode ? toCode(leftNode.name, leftNode.type) : left,
    targetCode: rightNode ? toCode(rightNode.name, rightNode.type) : right,
    forward: {
      baseEta: forwardEdge?.base_eta ?? 0,
      inTransitShipments: forwardStat?.in_transit_shipments ?? 0,
      inTransitLoad: forwardStat?.in_transit_load ?? 0,
      avgRemainingTicks: forwardStat?.avg_remaining_ticks ?? 0,
      minRemainingTicks: forwardStat?.min_remaining_ticks ?? 0,
      maxRemainingTicks: forwardStat?.max_remaining_ticks ?? 0,
    },
    reverse: {
      baseEta: reverseEdge?.base_eta ?? 0,
      inTransitShipments: reverseStat?.in_transit_shipments ?? 0,
      inTransitLoad: reverseStat?.in_transit_load ?? 0,
      avgRemainingTicks: reverseStat?.avg_remaining_ticks ?? 0,
      minRemainingTicks: reverseStat?.min_remaining_ticks ?? 0,
      maxRemainingTicks: reverseStat?.max_remaining_ticks ?? 0,
    },
  }
}

function corridorKeyFor(source: string, target: string): string {
  return [source, target].sort().join("|")
}

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

function buildWorldGraph(
  world: World,
  highlightedRegion: string | null,
  simState: SimulationState | null,
  onOpenDetails?: NodeData["onOpenDetails"],
): { nodes: Node<NodeData>[]; edges: Edge[] } {
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
      id: node.id,
      code: codeById.get(node.id) ?? node.id,
      name: node.name,
      type: node.type,
      region: node.region,
      capacity: node.capacity,
      avgHoldTicksPerTon: node.avg_hold_ticks_per_ton,
      isHighlighted: highlightedRegion === node.region,
      isDimmed: Boolean(highlightedRegion) && highlightedRegion !== node.region,
      utilization: (() => {
        const stat = simState?.node_stats?.[node.id]
        if (!stat || stat.capacity <= 0) {
          return 0
        }
        return Math.min(1.3, stat.active_load_tons / stat.capacity)
      })(),
      queuedShipments: simState?.node_stats?.[node.id]?.queued_shipments ?? 0,
      inboundShipments: simState?.node_stats?.[node.id]?.inbound_shipments ?? 0,
      outboundShipments: simState?.node_stats?.[node.id]?.outbound_shipments ?? 0,
      holdingShipments: simState?.node_stats?.[node.id]?.holding_shipments ?? 0,
      consumedShipmentsTick: simState?.node_stats?.[node.id]?.consumed_shipments_tick ?? 0,
      activeLoadTons: simState?.node_stats?.[node.id]?.active_load_tons ?? 0,
      isFailed: Boolean(simState?.failed && simState?.failed_node_names?.includes(node.name)),
      onOpenDetails,
    },
  }))

  const edges: Edge[] = world.edges.map(edge => {
    const sourcePos = positionById.get(edge.source) ?? { x: 0, y: 0 }
    const targetPos = positionById.get(edge.target) ?? { x: 0, y: 0 }
    const dx = targetPos.x - sourcePos.x
    const dy = targetPos.y - sourcePos.y

    const edgeStat = simState?.edge_stats?.[edge.id]
    const inTransitCount = edgeStat?.in_transit_shipments ?? 0
    const inTransitLoad = edgeStat?.in_transit_load ?? 0
    const avgRemaining = edgeStat?.avg_remaining_ticks ?? 0
    const isBusy = inTransitCount > 0
    const pairKey = corridorKeyFor(edge.source, edge.target)
    const sourceComesFirst = edge.source < edge.target

    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: pickSourceHandle(dx, dy),
      targetHandle: pickTargetHandle(dx, dy),
      label: String(edge.base_eta),
      type: "default",
      pathOptions: { curvature: sourceComesFirst ? 0.28 : 0.52 },
      animated: isBusy,
      markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12 },
      style: {
        stroke: isBusy ? "rgba(251, 191, 36, 0.92)" : "color-mix(in oklab, var(--muted-foreground) 58%, transparent)",
        strokeWidth: isBusy ? Math.min(4.2, 1.5 + inTransitCount * 0.22) : 1.2,
      },
      labelStyle: { fontSize: 10, fill: isBusy ? "rgb(252 211 77)" : "var(--muted-foreground)", fontWeight: 600 },
      labelBgStyle: {
        fill: "var(--background)",
        fillOpacity: 0.85,
        stroke: "var(--border)",
        strokeWidth: 0.5,
        rx: 3,
        ry: 3,
      },
      title: isBusy
        ? `${inTransitCount} in transit (${inTransitLoad.toFixed(1)}t), avg ${avgRemaining.toFixed(1)} ticks left`
        : undefined,
      data: { corridorKey: pairKey },
    }
  })

  return { nodes, edges }
}

export default function Page() {
  const [world, setWorld] = useState<World | null>(null)
  const [demand, setDemand] = useState<DemandState | null>(null)
  const [simState, setSimState] = useState<SimulationState | null>(null)
  const [demandLogs, setDemandLogs] = useState<DemandLogEntry[]>([])
  const [demandStreamError, setDemandStreamError] = useState("")
  const [simStreamError, setSimStreamError] = useState("")
  const [error, setError] = useState("")
  const [highlightedRegion, setHighlightedRegion] = useState<string | null>(null)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [selectedEdgeCorridor, setSelectedEdgeCorridor] = useState<string | null>(null)
  const [injectingNodeId, setInjectingNodeId] = useState<string | null>(null)
  const [seedInput, setSeedInput] = useState("42")
  const [tickInput, setTickInput] = useState("0")
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<NodeData>>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const laneCountRef = useRef<Map<string, number>>(new Map())
  const laneLoadRef = useRef<Map<string, number>>(new Map())
  const lastDemandTickRef = useRef(0)
  const logSeqRef = useRef(1)
  const streamReadyRef = useRef(false)
  const seedInitializedRef = useRef(false)

  const selectedNodeDetails = useMemo(
    () => toSelectedNodeDetails(world, simState, selectedNodeId),
    [world, simState, selectedNodeId],
  )
  const selectedEdgeDetails = useMemo(
    () => toSelectedEdgeDetails(world, simState, selectedEdgeCorridor),
    [world, simState, selectedEdgeCorridor],
  )
  const failedNodeCodes = useMemo(() => {
    if (!world || !simState?.failed_node_names?.length) {
      return []
    }
    return simState.failed_node_names.map(name => {
      const node = world.nodes.find(item => item.name === name)
      return node ? toCode(node.name, node.type) : name
    })
  }, [world, simState])

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

  useEffect(() => {
    const eventSource = new EventSource(`${API_BASE}/demand/stream`)

    eventSource.onmessage = event => {
      try {
        const payload = JSON.parse(event.data) as DemandState
        setDemand(payload)

        if (!seedInitializedRef.current) {
          setSeedInput(String(payload.seed))
          seedInitializedRef.current = true
        }

        if (payload.tick < lastDemandTickRef.current) {
          laneCountRef.current = new Map()
          laneLoadRef.current = new Map()
          setDemandLogs([])
          streamReadyRef.current = false
        }
        lastDemandTickRef.current = payload.tick

        const lanes = [...payload.within_region, ...payload.cross_region]
        const nextCountMap = new Map<string, number>()
        const nextLoadMap = new Map<string, number>()
        for (const lane of lanes) {
          const key = `${lane.source_region}->${lane.destination_region}`
          nextCountMap.set(key, lane.shipment_count)
          nextLoadMap.set(key, lane.total_load)
        }

        if (streamReadyRef.current) {
          const nextLogs: DemandLogEntry[] = []
          for (const lane of lanes) {
            const key = `${lane.source_region}->${lane.destination_region}`
            const prevCount = laneCountRef.current.get(key) ?? 0
            const prevLoad = laneLoadRef.current.get(key) ?? 0
            const countDelta = lane.shipment_count - prevCount
            const loadDelta = Math.max(0, lane.total_load - prevLoad)
            if (countDelta <= 0) {
              continue
            }

            const laneText = lane.source_region === lane.destination_region
              ? `${countDelta} new shipments within ${prettyRegion(lane.source_region)}`
              : `${countDelta} new shipments from ${prettyRegion(lane.source_region)} to ${prettyRegion(lane.destination_region)}`
            const fullText = `${laneText} (load +${loadDelta.toFixed(1)} tonnes)`
            nextLogs.push({ id: logSeqRef.current++, tick: payload.tick, text: fullText })
          }

          if (nextLogs.length > 0) {
            setDemandLogs(prev => [...nextLogs, ...prev].slice(0, 50))
          }
        } else {
          streamReadyRef.current = true
        }

        laneCountRef.current = nextCountMap
        laneLoadRef.current = nextLoadMap
        setDemandStreamError("")
      } catch {
        setDemandStreamError("Demand stream payload parse failed")
      }
    }

    eventSource.onerror = () => {
      setDemandStreamError("Demand stream reconnecting...")
    }

    return () => {
      eventSource.close()
    }
  }, [])

  function parseNonNegativeInt(value: string, fallback: number) {
    const trimmed = value.trim()
    if (trimmed.length === 0) {
      return fallback
    }
    const parsed = Number.parseInt(trimmed, 10)
    if (!Number.isFinite(parsed) || parsed < 0) {
      return fallback
    }
    return parsed
  }

  async function controlSimulation(action: "start" | "pause" | "clear") {
    try {
      const requestInit: RequestInit = { method: "POST" }

      if (action === "start") {
        const normalizedSeed = parseNonNegativeInt(seedInput, demand?.seed ?? 42)
        const normalizedTick = parseNonNegativeInt(tickInput, simState?.tick ?? 0)
        setSeedInput(String(normalizedSeed))
        setTickInput(String(normalizedTick))

        requestInit.headers = { "Content-Type": "application/json" }
        requestInit.body = JSON.stringify({ seed: normalizedSeed, tick: normalizedTick })
      }

      const response = await fetch(`${API_BASE}/sim/${action}`, requestInit)
      if (!response.ok) {
        throw new Error(`${action} failed (${response.status})`)
      }
      if (action === "clear") {
        const payload = (await response.json()) as { seed: number; tick: number }
        setSeedInput(String(payload.seed))
        setTickInput(String(payload.tick))
        setDemandLogs([])
        laneCountRef.current = new Map()
        laneLoadRef.current = new Map()
        lastDemandTickRef.current = 0
      } else if (action === "start") {
        setSimStreamError("")
        setDemandStreamError("")
      }
    } catch (err) {
      const msg = (err as Error).message
      setSimStreamError(msg)
      setDemandStreamError(msg)
    }
  }

  async function injectNodeLoad(nodeId: string) {
    try {
      setInjectingNodeId(nodeId)
      const response = await fetch(`${API_BASE}/demand/inject/${nodeId}`, { method: "POST" })
      if (!response.ok) {
        throw new Error(`inject failed (${response.status})`)
      }
    } catch (err) {
      const msg = (err as Error).message
      setDemandStreamError(msg)
    } finally {
      setInjectingNodeId(null)
    }
  }

  useEffect(() => {
    const eventSource = new EventSource(`${API_BASE}/sim/stream`)

    eventSource.onmessage = event => {
      try {
        const payload = JSON.parse(event.data) as SimulationState
        setSimState(payload)
        if (payload.running) {
          setTickInput(String(payload.tick))
        }
        setSimStreamError("")
      } catch {
        setSimStreamError("Simulator stream payload parse failed")
      }
    }

    eventSource.onerror = () => {
      setSimStreamError("Simulator stream reconnecting...")
    }

    return () => {
      eventSource.close()
    }
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
    return buildWorldGraph(world, highlightedRegion, simState, node => {
      setSelectedEdgeCorridor(null)
      setSelectedNodeId(node.id)
    })
  }, [world, highlightedRegion, simState])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setSelectedNodeId(null)
        setSelectedEdgeCorridor(null)
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => {
      window.removeEventListener("keydown", onKeyDown)
    }
  }, [])

  useEffect(() => {
    setNodes(graph.nodes)
    setEdges(graph.edges)
  }, [graph, setNodes, setEdges])

  return (
    <main className="grid min-h-svh place-items-center bg-gradient-to-br from-background via-background to-muted/40 p-6">
      <section className="relative h-[84vh] w-full max-w-7xl overflow-hidden rounded-none border border-border bg-card">
        {error ? (
          <div className="grid h-full place-items-center text-sm text-destructive">{error}</div>
        ) : !world ? (
          <div className="grid h-full place-items-center text-sm text-muted-foreground">Loading world graph...</div>
        ) : (
          <div className="flex h-full">
            <aside className="flex w-60 flex-col border-r border-border/80 bg-card/95">
              <div className="border-b border-border/70 px-3 py-2 text-xs font-medium tracking-[0.08em] text-muted-foreground md:text-sm">DEMAND GENERATOR</div>
              <div className="grid grid-cols-2 gap-2 border-b border-border/70 p-3 text-xs">
                <div className="rounded-none border border-border/70 bg-background/40 p-2">
                  <div className="text-[10px] text-muted-foreground">Tick</div>
                  <div className="text-sm font-semibold">{demand?.tick ?? 0}</div>
                </div>
                <div className="rounded-none border border-border/70 bg-background/40 p-2">
                  <div className="text-[10px] text-muted-foreground">Window</div>
                  <div className="text-sm font-semibold">{demand?.in_window_shipments ?? 0}</div>
                </div>
                <div className="col-span-2 rounded-none border border-border/70 bg-background/40 p-2">
                  <div className="text-[10px] text-muted-foreground">Total Generated</div>
                  <div className="text-sm font-semibold">{demand?.total_generated ?? 0}</div>
                </div>
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto p-3">
                <div className="text-[11px] font-semibold tracking-[0.08em] text-muted-foreground">DEMAND LOGS</div>
                <div className="mt-2 space-y-1.5">
                  {demandLogs.length === 0 ? (
                    <div className="rounded-none border border-border/60 bg-background/25 px-2 py-2 text-xs text-muted-foreground">
                      Waiting for demand events...
                    </div>
                  ) : null}
                  {demandLogs.map(log => (
                    <div key={log.id} className="rounded-none border border-border/60 bg-background/30 px-2 py-1 text-[10px] leading-4 text-foreground/95">
                      <span className="mr-2 text-[10px] text-muted-foreground">T+{log.tick}</span>
                      {log.text}
                    </div>
                  ))}
                </div>

                {demandStreamError ? <div className="mt-3 text-[11px] text-amber-400">{demandStreamError}</div> : null}
              </div>
            </aside>

            <div className="flex min-w-0 flex-1 flex-col">
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
                  onEdgeContextMenu={(event, edge) => {
                    event.preventDefault()
                    setSelectedNodeId(null)
                    setSelectedEdgeCorridor((edge.data as { corridorKey?: string } | undefined)?.corridorKey ?? corridorKeyFor(edge.source, edge.target))
                  }}
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

              <div className="border-t border-border/70 bg-card/95 px-3 py-1 text-[9px] text-muted-foreground">
                Node heat = load/capacity | Pulse = queue spike | Grey edge = idle | Animated edge = shipment in transit | Right-click node/edge for details
              </div>
            </div>

            <aside className="flex w-64 flex-col border-l border-border/80 bg-card/95">
              <div className="border-b border-border/70 px-3 py-2 text-xs font-medium tracking-[0.08em] text-muted-foreground md:text-sm">WITHOUT A C AGENT</div>
                <div className="grid grid-cols-2 gap-1 border-b border-border/70 p-2">
                  <label className="flex flex-col gap-1 text-[10px] text-muted-foreground">
                    <span>Seed</span>
                    <input
                      type="number"
                      min={0}
                      value={seedInput}
                      onChange={event => setSeedInput(event.target.value)}
                      className="h-7 rounded-none border border-border/70 bg-background/35 px-2 text-[11px] text-foreground outline-none focus:border-foreground/50"
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-[10px] text-muted-foreground">
                    <span>Tick</span>
                    <input
                      type="number"
                      min={0}
                      value={tickInput}
                      onChange={event => setTickInput(event.target.value)}
                      className="h-7 rounded-none border border-border/70 bg-background/35 px-2 text-[11px] text-foreground outline-none focus:border-foreground/50"
                    />
                  </label>
                </div>
                <div className="grid grid-cols-3 gap-1 border-b border-border/70 p-2">
                  <button
                    type="button"
                    onClick={() => controlSimulation("start")}
                    className="rounded-none border border-border/70 bg-background/35 px-2 py-1 text-[10px] font-medium text-foreground hover:bg-foreground/10"
                  >
                    Start
                  </button>
                  <button
                    type="button"
                    onClick={() => controlSimulation("pause")}
                    className="rounded-none border border-border/70 bg-background/35 px-2 py-1 text-[10px] font-medium text-foreground hover:bg-foreground/10"
                  >
                    Stop
                  </button>
                  <button
                    type="button"
                    onClick={() => controlSimulation("clear")}
                    className="rounded-none border border-border/70 bg-background/35 px-2 py-1 text-[10px] font-medium text-foreground hover:bg-foreground/10"
                  >
                    Clear
                  </button>
                </div>
              <div className="grid grid-cols-2 gap-2 border-b border-border/70 p-3 text-xs">
                <div className="rounded-none border border-border/70 bg-background/40 p-2">
                  <div className="text-[10px] text-muted-foreground">Status</div>
                  <div className="text-sm font-semibold">{simState?.failed ? "Failed" : simState?.running ? "Running" : "Paused"}</div>
                </div>
                <div className="rounded-none border border-border/70 bg-background/40 p-2">
                  <div className="text-[10px] text-muted-foreground">Full Nodes</div>
                  <div className="text-sm font-semibold">{simState?.full_nodes ?? 0}</div>
                </div>
                <div className="rounded-none border border-border/70 bg-background/40 p-2">
                  <div className="text-[10px] text-muted-foreground">Moved</div>
                  <div className="text-sm font-semibold">{simState?.moved_shipments_tick ?? 0}</div>
                </div>
                <div className="rounded-none border border-border/70 bg-background/40 p-2">
                  <div className="text-[10px] text-muted-foreground">Moved Load</div>
                  <div className="text-sm font-semibold">{(simState?.moved_load_tick ?? 0).toFixed(1)}t</div>
                </div>
                <div className="rounded-none border border-border/70 bg-background/40 p-2">
                  <div className="text-[10px] text-muted-foreground">Queued</div>
                  <div className="text-sm font-semibold">{simState?.queued_shipments ?? 0}</div>
                </div>
                <div className="rounded-none border border-border/70 bg-background/40 p-2">
                  <div className="text-[10px] text-muted-foreground">In Transit</div>
                  <div className="text-sm font-semibold">{simState?.in_transit_shipments ?? 0}</div>
                </div>
                <div className="rounded-none border border-border/70 bg-background/40 p-2">
                  <div className="text-[10px] text-muted-foreground">Holding</div>
                  <div className="text-sm font-semibold">{simState?.holding_shipments ?? 0}</div>
                </div>
                <div className="rounded-none border border-border/70 bg-background/40 p-2">
                  <div className="text-[10px] text-muted-foreground">Consumed</div>
                  <div className="text-sm font-semibold">{simState?.consumed_shipments ?? 0}</div>
                </div>
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto p-3">
                <div className="text-[11px] font-semibold tracking-[0.08em] text-muted-foreground">AGENT LOGS</div>
                {simState?.failed ? (
                  <div className="mt-2 rounded-none border border-rose-500/40 bg-rose-500/12 px-2 py-2 text-[10px] leading-4 text-rose-200">
                    <div className="font-semibold uppercase tracking-[0.08em]">Failed</div>
                    <div className="mt-1">{failedNodeCodes.join(", ") || simState.failure_reason}</div>
                  </div>
                ) : null}
                <div className="mt-2 space-y-1.5">
                  {(simState?.recent_logs ?? []).slice(0, 50).map((log, index) => (
                    <div key={`${log.tick}-${index}`} className="rounded-none border border-border/60 bg-background/30 px-2 py-1 text-[10px] leading-4 text-foreground/95">
                      <span className="mr-2 text-[10px] text-muted-foreground">T+{log.tick}</span>
                      {log.message}
                    </div>
                  ))}
                  {(simState?.recent_logs ?? []).length === 0 ? (
                    <div className="rounded-none border border-border/60 bg-background/25 px-2 py-2 text-xs text-muted-foreground">
                      Waiting for simulator events...
                    </div>
                  ) : null}
                </div>

                {simState?.failed && simState.failure_reason ? <div className="mt-3 text-[11px] text-rose-400">{simState.failure_reason}</div> : null}
                {simStreamError ? <div className="mt-3 text-[11px] text-amber-400">{simStreamError}</div> : null}
              </div>
            </aside>
          </div>
        )}

        {selectedNodeDetails ? (
          <div className="absolute right-72 top-16 z-30 w-72 rounded-none border border-border bg-card/95 p-3 text-xs shadow-[0_16px_34px_rgba(0,0,0,0.42)] backdrop-blur-sm">
            <div className="mb-2 flex items-start justify-between gap-2 border-b border-border/70 pb-2">
              <div>
                <div className="font-semibold text-foreground">{selectedNodeDetails.code} - {selectedNodeDetails.name}</div>
                <div className="text-[10px] text-muted-foreground">{prettyRegion(selectedNodeDetails.region)}</div>
              </div>
              <button
                type="button"
                className="pointer-events-auto rounded-none border border-border/70 px-1.5 py-0.5 text-[10px] text-muted-foreground hover:text-foreground"
                onClick={() => setSelectedNodeId(null)}
              >
                Close
              </button>
            </div>

            <button
              type="button"
              className="pointer-events-auto mb-2 w-full rounded-none border border-border/70 bg-background/35 px-2 py-1 text-[10px] font-medium text-foreground hover:bg-foreground/10 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={injectingNodeId === selectedNodeDetails.id}
              onClick={() => injectNodeLoad(selectedNodeDetails.id)}
            >
              {injectingNodeId === selectedNodeDetails.id ? "Injecting load..." : "Increase Load"}
            </button>

            <div className="space-y-1 text-[11px]">
              <div className="flex justify-between"><span className="text-muted-foreground">Active Load</span><span>{selectedNodeDetails.activeLoadTons.toFixed(1)}t</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Capacity</span><span>{selectedNodeDetails.capacity.toFixed(0)}t</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Load Ratio</span><span>{((selectedNodeDetails.activeLoadTons / Math.max(selectedNodeDetails.capacity, 1)) * 100).toFixed(1)}%</span></div>
              <div className="my-1 border-t border-border/60" />
              <div className="flex justify-between"><span className="text-muted-foreground">Queued</span><span>{selectedNodeDetails.queuedShipments}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Inbound</span><span>{selectedNodeDetails.inboundShipments}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Outbound</span><span>{selectedNodeDetails.outboundShipments}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Holding</span><span>{selectedNodeDetails.holdingShipments}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Consumed (tick)</span><span>{selectedNodeDetails.consumedShipmentsTick}</span></div>
              <div className="my-1 border-t border-border/60" />
              <div className="flex justify-between"><span className="text-muted-foreground">Avg Hold</span><span>{selectedNodeDetails.avgHoldTicksPerTon.toFixed(2)} ticks/ton</span></div>
            </div>
          </div>
        ) : null}

        {selectedEdgeDetails ? (
          <div className="absolute right-72 top-16 z-30 w-72 rounded-none border border-border bg-card/95 p-3 text-xs shadow-[0_16px_34px_rgba(0,0,0,0.42)] backdrop-blur-sm">
            <div className="mb-2 flex items-start justify-between gap-2 border-b border-border/70 pb-2">
              <div>
                <div className="font-semibold text-foreground">{selectedEdgeDetails.sourceCode} &lt;-&gt; {selectedEdgeDetails.targetCode}</div>
                <div className="text-[10px] text-muted-foreground">corridor {selectedEdgeDetails.corridorId}</div>
              </div>
              <button
                type="button"
                className="pointer-events-auto rounded-none border border-border/70 px-1.5 py-0.5 text-[10px] text-muted-foreground hover:text-foreground"
                onClick={() => setSelectedEdgeCorridor(null)}
              >
                Close
              </button>
            </div>

            <div className="space-y-1 text-[11px]">
              <div className="font-semibold text-foreground/90">{selectedEdgeDetails.sourceCode} -&gt; {selectedEdgeDetails.targetCode}</div>
              <div className="flex justify-between"><span className="text-muted-foreground">Base ETA</span><span>{selectedEdgeDetails.forward.baseEta} ticks</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">In Transit</span><span>{selectedEdgeDetails.forward.inTransitShipments}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Transit Load</span><span>{selectedEdgeDetails.forward.inTransitLoad.toFixed(1)}t</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Avg Remaining</span><span>{selectedEdgeDetails.forward.avgRemainingTicks.toFixed(1)} ticks</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Min / Max Remaining</span><span>{selectedEdgeDetails.forward.minRemainingTicks} / {selectedEdgeDetails.forward.maxRemainingTicks}</span></div>
              <div className="my-1 border-t border-border/60" />
              <div className="font-semibold text-foreground/90">{selectedEdgeDetails.targetCode} -&gt; {selectedEdgeDetails.sourceCode}</div>
              <div className="flex justify-between"><span className="text-muted-foreground">Base ETA</span><span>{selectedEdgeDetails.reverse.baseEta} ticks</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">In Transit</span><span>{selectedEdgeDetails.reverse.inTransitShipments}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Transit Load</span><span>{selectedEdgeDetails.reverse.inTransitLoad.toFixed(1)}t</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Avg Remaining</span><span>{selectedEdgeDetails.reverse.avgRemainingTicks.toFixed(1)} ticks</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Min / Max Remaining</span><span>{selectedEdgeDetails.reverse.minRemainingTicks} / {selectedEdgeDetails.reverse.maxRemainingTicks}</span></div>
            </div>
          </div>
        ) : null}
      </section>
    </main>
  )
}
