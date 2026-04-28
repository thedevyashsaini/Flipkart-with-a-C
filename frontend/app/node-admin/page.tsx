"use client"

import { useEffect, useMemo, useState, useCallback } from "react"

import { LiveTopNav } from "@/app/live-layout"
import { SupplyChainMap } from "@/components/live/supply-chain-map"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"

type NodeInput = {
  id: string
  name: string
  type: "hub" | "warehouse" | "airport" | "city"
  region: string
  capacity: number
  avg_hold_ticks_per_ton: number
  x: number
  y: number
}

type EdgeInput = {
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

type NodeTimelineItem = {
  id: string
  kind: "suggestion" | "event"
  created_at: string
  message: string
  decision_id: string | null
  event_type: string | null
}

function toCode(name: string, type: NodeInput["type"]): string {
  const city = name.split(" ")[0]?.replace(/[^a-zA-Z]/g, "") || "NODE"
  const cityPart = city.toUpperCase().slice(0, 3).padEnd(3, "X")
  const typePart = type.slice(0, 1).toUpperCase()
  return `${cityPart}${typePart}`
}

function cacheGet<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") {
    return fallback
  }
  try {
    const raw = window.localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

function cacheSet(key: string, value: unknown) {
  if (typeof window === "undefined") {
    return
  }
  window.localStorage.setItem(key, JSON.stringify(value))
}

function cacheRemove(key: string) {
  if (typeof window === "undefined") {
    return
  }
  window.localStorage.removeItem(key)
}

function formatTimestamp(value: string): string {
  if (!value) {
    return ""
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date)
}

export default function NodeAdminPage() {
  const [chainId, setChainId] = useState("")
  const [nodeId, setNodeId] = useState("")
  const [nodeToken, setNodeToken] = useState("")
  const [adminKey, setAdminKey] = useState("")
  const [timeline, setTimeline] = useState<NodeTimelineItem[]>([])
  const [nodes, setNodes] = useState<NodeInput[]>([])
  const [edges, setEdges] = useState<EdgeInput[]>([])
  const [shipmentOptions, setShipmentOptions] = useState<string[]>([])
  const [shipments, setShipments] = useState<LiveShipment[]>([])
  const [eventType, setEventType] = useState<"shipment_created" | "shipment_arrived" | "shipment_dispatched" | "shipment_held" | "shipment_rerouted" | "shipment_delivered">("shipment_created")
  const [shipmentId, setShipmentId] = useState("")
  const [destinationNodeId, setDestinationNodeId] = useState("")
  const [load, setLoad] = useState("2.0")
  const [priority, setPriority] = useState<"low" | "medium" | "high" | "critical">("medium")
  const [deadlineTick, setDeadlineTick] = useState("0")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const [activeDecisionId, setActiveDecisionId] = useState<string | null>(null)
  const [decisionExplanations, setDecisionExplanations] = useState<Record<string, string>>({})
  const [loadingDecisionId, setLoadingDecisionId] = useState<string | null>(null)
  const [didRestore, setDidRestore] = useState(false)
  const [incomingDrawerOpen, setIncomingDrawerOpen] = useState(false)
  const [contextMenuNode, setContextMenuNode] = useState<{ id: string; name: string; type: string; status?: string } | null>(null)
  const [flushingNode, setFlushingNode] = useState<string | null>(null)

  useEffect(() => {
    setChainId(cacheGet("node.chain_id", ""))
    setNodeId(cacheGet("node.node_id", ""))
    setNodeToken(cacheGet("node.token", ""))
    setAdminKey(cacheGet("node.admin_key", ""))
    setDidRestore(true)
  }, [])

  useEffect(() => {
    cacheSet("node.chain_id", chainId)
    cacheSet("node.node_id", nodeId)
    cacheSet("node.token", nodeToken)
    cacheSet("node.admin_key", adminKey)
  }, [chainId, nodeId, nodeToken, adminKey])

  const canOperate = useMemo(() => Boolean(chainId && nodeId && nodeToken), [chainId, nodeId, nodeToken])
  const isCreateAction = eventType === "shipment_created"
  const needsDestinationNode = eventType === "shipment_created" || eventType === "shipment_dispatched" || eventType === "shipment_rerouted"
  const needsShipmentMetadata = eventType === "shipment_created"
  const nodeCodeById = useMemo(() => new Map(nodes.map(node => [node.id, toCode(node.name, node.type)])), [nodes])
  const nodeById = useMemo(() => new Map(nodes.map(node => [node.id, node])), [nodes])
  const selectedNode = useMemo(() => nodeById.get(nodeId) ?? null, [nodeById, nodeId])
  const incomingShipments = useMemo(
    () => shipments.filter(shipment => shipment.state === "in_transit" && shipment.target_node_id === nodeId),
    [shipments, nodeId],
  )
  const localShipments = useMemo(
    () => shipments.filter(shipment => shipment.current_node_id === nodeId && shipment.state !== "in_transit"),
    [shipments, nodeId],
  )

  function formatNodeRefs(text: string): string {
    let formatted = text
    for (const [id, code] of nodeCodeById.entries()) {
      formatted = formatted.replace(new RegExp(`\\b${id}\\b`, "g"), code)
    }
    return formatted
  }

  function resetSubmissionForm() {
    setShipmentId("")
    setDestinationNodeId("")
    setLoad("")
    setPriority("medium")
    setDeadlineTick("")
  }

  function resetNodeSession() {
    cacheRemove("node.chain_id")
    cacheRemove("node.node_id")
    cacheRemove("node.token")
    cacheRemove("node.admin_key")
    setChainId("")
    setNodeId("")
    setNodeToken("")
    setAdminKey("")
    setNodes([])
    setEdges([])
    setTimeline([])
    setShipmentOptions([])
    setDecisionExplanations({})
    setActiveDecisionId(null)
    setLoadingDecisionId(null)
    setError("")
    resetSubmissionForm()
  }

  useEffect(() => {
    if (didRestore && chainId && nodeId && nodeToken && adminKey) {
      void refreshAll()
    }
  }, [didRestore])

  async function refreshAll() {
    if (!chainId || !nodeId) {
      return
    }
    setError("")
    setLoading(true)
    try {
      const [snapshotResp, timelineResp, searchResp] = await Promise.all([
        fetch(`${API_BASE}/live/chains/${chainId}/snapshot`, { headers: { "X-Admin-Api-Key": adminKey }, cache: "no-store" }),
        fetch(`${API_BASE}/live/chains/${chainId}/nodes/${nodeId}/timeline`, { headers: { Authorization: `Bearer ${nodeToken}` }, cache: "no-store" }),
        fetch(`${API_BASE}/live/chains/${chainId}/shipments/search?q=${encodeURIComponent(shipmentId)}`, { headers: { "X-Admin-Api-Key": adminKey }, cache: "no-store" }),
      ])

      const snapshot = await snapshotResp.json() as { nodes?: NodeInput[]; edges?: EdgeInput[]; shipments?: LiveShipment[]; detail?: string }
      const timelinePayload = await timelineResp.json() as { timeline?: NodeTimelineItem[]; detail?: string }
      const searchPayload = await searchResp.json() as { shipment_ids?: string[]; detail?: string }

      if (!snapshotResp.ok) {
        throw new Error(snapshot.detail || `snapshot failed (${snapshotResp.status})`)
      }

      setNodes(snapshot.nodes ?? [])
      setEdges(snapshot.edges ?? [])
      setShipments(snapshot.shipments ?? [])

      if (timelineResp.ok) {
        setTimeline(timelinePayload.timeline ?? [])
        cacheSet(`node.timeline.${chainId}.${nodeId}`, timelinePayload.timeline ?? [])
      } else {
        setTimeline(cacheGet(`node.timeline.${chainId}.${nodeId}`, [] as NodeTimelineItem[]))
      }

      if (searchResp.ok) {
        setShipmentOptions(searchPayload.shipment_ids ?? [])
      } else {
        setShipmentOptions([])
      }

      if (!timelineResp.ok) {
        setError(timelinePayload.detail || `timeline failed (${timelineResp.status})`)
      } else if (!searchResp.ok) {
        setError(searchPayload.detail || `search failed (${searchResp.status})`)
      }
    } catch (err) {
      setError((err as Error).message)
      setNodes([])
      setEdges([])
      setShipments([])
      setTimeline(cacheGet(`node.timeline.${chainId}.${nodeId}`, [] as NodeTimelineItem[]))
    } finally {
      setLoading(false)
    }
  }

  async function submitEvent() {
    if (!canOperate || !shipmentId.trim()) {
      return
    }
    if (needsDestinationNode && !destinationNodeId.trim()) {
      setError("Destination / target node is required for this action")
      return
    }
    if (needsShipmentMetadata && !load.trim()) {
      setError("Load is required when creating a shipment")
      return
    }
    setError("")
    try {
      const body: {
        event_type: typeof eventType
        shipment_id: string
        source_node_id: string
        current_node_id: string
        destination_node_id?: string
        load?: number
        priority?: typeof priority
        deadline_tick?: number
      } = {
        event_type: eventType,
        shipment_id: shipmentId.trim(),
        source_node_id: nodeId,
        current_node_id: nodeId,
      }

      if (needsDestinationNode && destinationNodeId.trim()) {
        body.destination_node_id = destinationNodeId.trim()
      }
      if (needsShipmentMetadata) {
        body.load = Math.max(0.1, Number.parseFloat(load) || 0.1)
        body.priority = priority
        body.deadline_tick = Math.max(0, Number.parseInt(deadlineTick, 10) || 0)
      }

      const response = await fetch(`${API_BASE}/live/events`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${nodeToken}`,
        },
        body: JSON.stringify(body),
      })
      const payload = await response.json() as { detail?: string }
      if (!response.ok) {
        throw new Error(payload.detail || `event failed (${response.status})`)
      }
      resetSubmissionForm()
      await refreshAll()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function explain(decisionId: string) {
    setActiveDecisionId(decisionId)
    if (decisionExplanations[decisionId]) {
      return
    }
    setLoadingDecisionId(decisionId)
    try {
      const response = await fetch(`${API_BASE}/live/decisions/${decisionId}/explain`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-Api-Key": adminKey,
        },
        body: JSON.stringify({ chain_id: chainId, decision_id: decisionId }),
      })
      const payload = await response.json() as { explanation?: string; detail?: string }
      if (!response.ok || !payload.explanation) {
        throw new Error(payload.detail || `explain failed (${response.status})`)
      }
      setDecisionExplanations(prev => ({ ...prev, [decisionId]: payload.explanation || "" }))
    } catch (err) {
      setDecisionExplanations(prev => ({ ...prev, [decisionId]: `Explain failed: ${(err as Error).message}` }))
    } finally {
      setLoadingDecisionId(current => (current === decisionId ? null : current))
    }
  }

  const handleNodeContextMenu = useCallback((nodeId: string, nodeName: string, nodeType: string, status?: string) => {
    setContextMenuNode({ id: nodeId, name: nodeName, type: nodeType, status })
  }, [])

  async function handleFlushNode() {
    if (!contextMenuNode || !adminKey || !chainId) {
      return
    }
    const nodeId = contextMenuNode.id
    setFlushingNode(nodeId)
    setContextMenuNode(null)
    try {
      const response = await fetch(`${API_BASE}/live/chains/${chainId}/nodes/${nodeId}/flush`, {
        method: "POST",
        headers: { "X-Admin-Api-Key": adminKey },
      })
      const payload = await response.json() as { ok?: boolean; detail?: string }
      if (!response.ok) {
        throw new Error(payload.detail || `flush failed (${response.status})`)
      }
      void refreshAll()
    } catch (err) {
      setError(`Flush failed: ${(err as Error).message}`)
    } finally {
      setFlushingNode(null)
    }
  }

  async function handleReopenNode() {
    if (!contextMenuNode || !adminKey || !chainId) {
      return
    }
    const nodeId = contextMenuNode.id
    setFlushingNode(nodeId)
    setContextMenuNode(null)
    try {
      const response = await fetch(`${API_BASE}/live/chains/${chainId}/nodes/${nodeId}/reopen`, {
        method: "POST",
        headers: { "X-Admin-Api-Key": adminKey },
      })
      const payload = await response.json() as { ok?: boolean; detail?: string }
      if (!response.ok) {
        throw new Error(payload.detail || `reopen failed (${response.status})`)
      }
      void refreshAll()
    } catch (err) {
      setError(`Reopen failed: ${(err as Error).message}`)
    } finally {
      setFlushingNode(null)
    }
  }

  return (
    <main className="grid min-h-svh place-items-center bg-gradient-to-br from-background via-background to-muted/40 p-6">
      <section className="relative h-[84vh] w-full max-w-[96vw] overflow-hidden rounded-none border border-border bg-card">
        <LiveTopNav />
        <div className="flex h-[calc(100%-2.5rem)] min-h-0">
          <aside className="flex w-[15rem] flex-col border-r border-border/80 bg-card/95">
            <div className="border-b border-border/70 px-3 py-2 text-[11px] font-semibold tracking-[0.08em] text-muted-foreground">NODE INFO</div>
            <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
              <div className="rounded-none border border-border/60 bg-background/25 p-2 text-[10px]">
                <div className="text-muted-foreground">Node</div>
                <div className="mt-1 text-foreground/95">{selectedNode ? `${toCode(selectedNode.name, selectedNode.type)} · ${selectedNode.name}` : nodeId || "-"}</div>
              </div>
              <div className="grid grid-cols-2 gap-2 text-[10px]">
                <div className="rounded-none border border-border/60 bg-background/25 p-2">
                  <div className="text-muted-foreground">Capacity</div>
                  <div className="mt-1 text-foreground/95">{selectedNode ? selectedNode.capacity : "-"}</div>
                </div>
                <div className="rounded-none border border-border/60 bg-background/25 p-2">
                  <div className="text-muted-foreground">Type</div>
                  <div className="mt-1 text-foreground/95">{selectedNode?.type ?? "-"}</div>
                </div>
                <div className="rounded-none border border-border/60 bg-background/25 p-2">
                  <div className="text-muted-foreground">Region</div>
                  <div className="mt-1 text-foreground/95">{selectedNode?.region ?? "-"}</div>
                </div>
                <div className="rounded-none border border-border/60 bg-background/25 p-2">
                  <div className="text-muted-foreground">Avg Hold</div>
                  <div className="mt-1 text-foreground/95">{selectedNode ? selectedNode.avg_hold_ticks_per_ton : "-"}</div>
                </div>
              </div>
              <div className="rounded-none border border-border/60 bg-background/25 p-2 text-[10px]">
                <div className="text-muted-foreground">Incoming Shipments</div>
                <div className="mt-1 text-lg font-semibold text-foreground">{incomingShipments.length}</div>
                <button type="button" onClick={() => setIncomingDrawerOpen(open => !open)} className="mt-2 w-full rounded-none border border-border/70 bg-background/35 px-2 py-1 text-[10px] font-semibold text-foreground hover:bg-foreground/10">
                  {incomingDrawerOpen ? "Hide Incoming" : "Show Incoming"}
                </button>
              </div>
              {incomingDrawerOpen ? (
                <div className="space-y-2">
                  {incomingShipments.length > 0 ? (
                    incomingShipments.map(shipment => (
                      <div key={shipment.shipment_id} className="rounded-none border border-border/60 bg-background/30 p-2 text-[10px]">
                        <div className="text-foreground/95">Shipment {shipment.shipment_id}</div>
                        <div className="mt-1 text-muted-foreground">From {nodeCodeById.get(shipment.current_node_id) ?? shipment.current_node_id}</div>
                        <div className="text-muted-foreground">Origin {nodeCodeById.get(shipment.source_node_id) ?? shipment.source_node_id}</div>
                      </div>
                    ))
                  ) : (
                    <div className="rounded-none border border-border/60 bg-background/25 p-2 text-xs text-muted-foreground">No inbound shipments right now.</div>
                  )}
                </div>
              ) : null}
              <div className="rounded-none border border-border/60 bg-background/25 p-2 text-[10px]">
                <div className="text-muted-foreground">At This Node</div>
                <div className="mt-1 text-lg font-semibold text-foreground">{localShipments.length}</div>
              </div>
            </div>
          </aside>

          <section className="flex min-w-0 flex-1 flex-col border-r border-border/80 bg-card/95">
            <SupplyChainMap nodes={nodes} edges={edges} shipments={shipments} onNodeContextMenu={handleNodeContextMenu} />
            <div className="border-t border-border/70 bg-card/95 px-3 py-1 text-[9px] text-muted-foreground">
              Node timeline = suggestions + observed events | Submit actions using node token
            </div>
          </section>

          <section className="flex w-[21rem] flex-col border-l border-border/80 bg-card/95">
            <div className="flex items-center justify-between border-b border-border/70 px-3 py-2 text-[11px] font-semibold tracking-[0.08em] text-muted-foreground">
              <span>SUGGESTIONS + EVENT LOG</span>
              <button type="button" onClick={() => void refreshAll()} className="rounded-none border border-border/70 bg-background/35 px-2 py-1 text-[10px] font-semibold text-foreground hover:bg-foreground/10">
                {loading ? "..." : "Refresh"}
              </button>
            </div>
            <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
              <div className="rounded-none border border-border/60 bg-background/25 p-2 text-[10px] text-muted-foreground">
                Showing only suggestions and events for node {(nodeCodeById.get(nodeId) ?? nodeId) || "-"}
              </div>
              {timeline.map(item => (
                <div key={item.id} className="rounded-none border border-border/60 bg-background/30 p-2 text-[10px]">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className={`text-[10px] font-semibold tracking-[0.08em] ${item.kind === "suggestion" ? "text-sky-200" : "text-amber-200"}`}>{item.kind === "suggestion" ? "SUGGESTION" : "EVENT"}</div>
                      <div className="mt-1 text-foreground/95">{item.kind === "suggestion" ? formatNodeRefs(item.message) : item.message}</div>
                      <div className="mt-1 text-muted-foreground">{formatTimestamp(item.created_at)}</div>
                    </div>
                    {item.kind === "suggestion" && item.decision_id ? (
                      <button type="button" onClick={() => void explain(item.decision_id as string)} className="rounded-none border border-sky-300/40 bg-sky-400/12 px-2 py-0.5 text-[10px] text-sky-200">
                        {loadingDecisionId === item.decision_id ? "Loading..." : "Why"}
                      </button>
                    ) : null}
                  </div>
                  {item.kind === "suggestion" && item.decision_id && activeDecisionId === item.decision_id && (loadingDecisionId === item.decision_id || decisionExplanations[item.decision_id]) ? (
                    <div className="mt-2 whitespace-pre-wrap rounded-none border border-border/60 bg-background/25 p-2 text-[10px] text-foreground/95">
                      {loadingDecisionId === item.decision_id ? "Loading explanation..." : formatNodeRefs(decisionExplanations[item.decision_id])}
                    </div>
                  ) : null}
                </div>
              ))}
              {timeline.length === 0 ? <div className="rounded-none border border-border/60 bg-background/25 p-2 text-xs text-muted-foreground">No timeline items yet.</div> : null}
            </div>
          </section>

          <aside className="flex w-[19rem] flex-col border-l border-border/80 bg-card/95">
            <div className="border-b border-border/70 px-3 py-2 text-[11px] font-semibold tracking-[0.08em] text-muted-foreground">NODE ACTION CONSOLE</div>
            <div className="min-h-0 flex-1 overflow-y-auto p-3">
            <label className="flex flex-col gap-1 text-[10px] text-muted-foreground">
              <span>Chain ID</span>
              <input value={chainId} onChange={event => setChainId(event.target.value)} className="h-8 rounded-none border border-border/70 bg-background/35 px-2 text-xs text-foreground" />
            </label>
            <label className="mt-2 flex flex-col gap-1 text-[10px] text-muted-foreground">
              <span>Node ID</span>
              <input list="node-ids" value={nodeId} onChange={event => setNodeId(event.target.value)} className="h-8 rounded-none border border-border/70 bg-background/35 px-2 text-xs text-foreground" />
            </label>
            <label className="mt-2 flex flex-col gap-1 text-[10px] text-muted-foreground">
              <span>Node Bearer Token</span>
              <textarea value={nodeToken} onChange={event => setNodeToken(event.target.value)} className="h-14 rounded-none border border-border/70 bg-background/35 p-2 text-[10px] text-foreground" />
            </label>
            <label className="mt-2 flex flex-col gap-1 text-[10px] text-muted-foreground">
              <span>Supply Admin API Key (for Why + search)</span>
              <input value={adminKey} onChange={event => setAdminKey(event.target.value)} className="h-8 rounded-none border border-border/70 bg-background/35 px-2 text-xs text-foreground" />
            </label>
            <div className="mt-2 grid grid-cols-1 gap-2">
              <label className="flex flex-col gap-1 text-[10px] text-muted-foreground">
                <span>Action</span>
                <select value={eventType} onChange={event => setEventType(event.target.value as typeof eventType)} className="h-8 rounded-none border border-border/70 bg-background/35 px-2 text-xs text-foreground">
                  <option value="shipment_created">Create Shipment</option>
                  <option value="shipment_arrived">Shipment Arrived</option>
                  <option value="shipment_dispatched">Shipment Dispatched</option>
                  <option value="shipment_held">Shipment Held</option>
                  <option value="shipment_rerouted">Shipment Rerouted</option>
                  <option value="shipment_delivered">Shipment Delivered</option>
                </select>
              </label>
            </div>
            <label className="mt-2 flex flex-col gap-1 text-[10px] text-muted-foreground">
              <span>{isCreateAction ? "Shipment ID" : "Shipment ID (autocomplete)"}</span>
              <input list="shipment-ids" value={shipmentId} onChange={event => setShipmentId(event.target.value)} className="h-8 rounded-none border border-border/70 bg-background/35 px-2 text-xs text-foreground" />
              <datalist id="shipment-ids">
                {shipmentOptions.map(id => (
                  <option key={id} value={id} />
                ))}
              </datalist>
            </label>
            {needsDestinationNode ? (
              <label className="mt-2 flex flex-col gap-1 text-[10px] text-muted-foreground">
                <span>{eventType === "shipment_created" ? "Destination Node ID" : "Target Node ID"}</span>
                <input list="node-ids" value={destinationNodeId} onChange={event => setDestinationNodeId(event.target.value)} className="h-8 rounded-none border border-border/70 bg-background/35 px-2 text-xs text-foreground" />
              </label>
            ) : null}
            <datalist id="node-ids">
              {nodes.map(node => (
                <option key={node.id} value={node.id}>{toCode(node.name, node.type)}</option>
              ))}
            </datalist>
            {needsShipmentMetadata ? (
              <>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <label className="flex flex-col gap-1 text-[10px] text-muted-foreground">
                    <span>Priority</span>
                    <select value={priority} onChange={event => setPriority(event.target.value as typeof priority)} className="h-8 rounded-none border border-border/70 bg-background/35 px-2 text-xs text-foreground">
                      <option value="low">low</option>
                      <option value="medium">medium</option>
                      <option value="high">high</option>
                      <option value="critical">critical</option>
                    </select>
                  </label>
                  <label className="flex flex-col gap-1 text-[10px] text-muted-foreground">
                    <span>Load</span>
                    <input value={load} onChange={event => setLoad(event.target.value)} className="h-8 rounded-none border border-border/70 bg-background/35 px-2 text-xs text-foreground" />
                  </label>
                </div>
                <label className="mt-2 flex flex-col gap-1 text-[10px] text-muted-foreground">
                  <span>Deadline Tick</span>
                  <input value={deadlineTick} onChange={event => setDeadlineTick(event.target.value)} className="h-8 rounded-none border border-border/70 bg-background/35 px-2 text-xs text-foreground" />
                </label>
              </>
            ) : null}
            <div className="mt-2 grid grid-cols-2 gap-2">
              <button type="button" onClick={() => void submitEvent()} disabled={!canOperate} className="rounded-none border border-border/70 bg-background/35 px-2 py-1 text-[10px] font-semibold text-foreground hover:bg-foreground/10 disabled:opacity-60">
                Submit
              </button>
              <button type="button" onClick={() => void refreshAll()} className="rounded-none border border-border/70 bg-background/35 px-2 py-1 text-[10px] font-semibold text-foreground hover:bg-foreground/10">
                Refresh
              </button>
            </div>
            <button type="button" onClick={resetNodeSession} className="mt-2 w-full rounded-none border border-rose-300/30 bg-rose-400/10 px-2 py-1 text-[10px] font-semibold text-rose-200 hover:bg-rose-400/15">
              Reset Node Session
            </button>
            {error ? <div className="mt-2 text-[10px] text-amber-300">{error}</div> : null}
            </div>
          </aside>

          <Dialog open={!!contextMenuNode} onOpenChange={(open: boolean) => !open && setContextMenuNode(null)}>
            <DialogContent className="max-w-sm rounded-none border border-border/80 bg-card/95 p-4">
              <DialogHeader>
                <DialogTitle className="text-sm font-semibold tracking-wide">
                  {contextMenuNode?.name || contextMenuNode?.id}
                </DialogTitle>
                <DialogDescription className="text-[10px]">
                  {contextMenuNode?.status === "flushed" ? "Node is currently flushed" : "Node is active"}
                </DialogDescription>
              </DialogHeader>
              <div className="mt-2 flex flex-col gap-2">
                {contextMenuNode?.status === "flushed" ? (
                  <Button
                    onClick={handleReopenNode}
                    disabled={!!flushingNode}
                    className="w-full rounded-none border border-emerald-500/40 bg-emerald-500/15 text-[10px] font-semibold text-emerald-200 hover:bg-emerald-500/25"
                  >
                    {flushingNode ? "Reopening..." : "Reopen Node"}
                  </Button>
                ) : (
                  <Button
                    onClick={handleFlushNode}
                    disabled={!!flushingNode}
                    className="w-full rounded-none border border-rose-500/40 bg-rose-500/15 text-[10px] font-semibold text-rose-200 hover:bg-rose-500/25"
                  >
                    {flushingNode ? "Flushing..." : "Flush Node"}
                  </Button>
                )}
              </div>
            </DialogContent>
          </Dialog>
        </div>
      </section>
    </main>
  )
}
