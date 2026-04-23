"use client"

import { useEffect, useState } from "react"

import { LiveTopNav } from "@/app/live-layout"
import { SupplyChainMap } from "@/components/live/supply-chain-map"

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

type SuggestionLog = {
  decision_id: string
  tick: number
  message: string
  reason: string
  created_at: string
}

type NodeEvent = {
  event_id: string
  created_at: string
  event_type: string
  shipment_id: string
  node_id: string
}

type SupplyTimelineItem = {
  id: string
  kind: "suggestion" | "event"
  created_at: string
  message: string
  decision_id: string | null
}

function toCode(name: string, type: NodeInput["type"]): string {
  const city = name.split(" ")[0]?.replace(/[^a-zA-Z]/g, "") || "NODE"
  const cityPart = city.toUpperCase().slice(0, 3).padEnd(3, "X")
  const typePart = type.slice(0, 1).toUpperCase()
  return `${cityPart}${typePart}`
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

function cachedGet<T>(key: string, fallback: T): T {
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

function cachedSet(key: string, value: unknown) {
  if (typeof window === "undefined") {
    return
  }
  window.localStorage.setItem(key, JSON.stringify(value))
}

function cachedRemove(key: string) {
  if (typeof window === "undefined") {
    return
  }
  window.localStorage.removeItem(key)
}

export default function SupplyAdminPage() {
  const [adminKey, setAdminKey] = useState("")
  const [chainName, setChainName] = useState("Demo Live Chain")
  const [chainId, setChainId] = useState("")
  const [nodesJson, setNodesJson] = useState("[]")
  const [edgesJson, setEdgesJson] = useState("[]")
  const [previewNodes, setPreviewNodes] = useState<NodeInput[]>([])
  const [previewEdges, setPreviewEdges] = useState<EdgeInput[]>([])
  const [shipments, setShipments] = useState<LiveShipment[]>([])
  const [tokens, setTokens] = useState<Array<{ node_id: string; api_token: string }>>([])
  const [suggestions, setSuggestions] = useState<SuggestionLog[]>([])
  const [events, setEvents] = useState<NodeEvent[]>([])
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const [activeExplainDecisionId, setActiveExplainDecisionId] = useState<string | null>(null)
  const [decisionExplanations, setDecisionExplanations] = useState<Record<string, string>>({})
  const [loadingExplainDecisionId, setLoadingExplainDecisionId] = useState<string | null>(null)
  const [tokensOpen, setTokensOpen] = useState(false)
  const nodeCodeById = new Map(previewNodes.map(node => [node.id, toCode(node.name, node.type)]))
  const timelineItems: SupplyTimelineItem[] = [
    ...suggestions.map(item => ({
      id: item.decision_id,
      kind: "suggestion" as const,
      created_at: item.created_at,
      message: formatNodeRefs(item.message),
      decision_id: item.decision_id,
    })),
    ...events.map(item => ({
      id: item.event_id,
      kind: "event" as const,
      created_at: item.created_at,
      message: `${item.event_type}: shipment ${item.shipment_id} at ${nodeCodeById.get(item.node_id) ?? item.node_id}`,
      decision_id: null,
    })),
  ].sort((left, right) => right.created_at.localeCompare(left.created_at))

  function formatNodeRefs(text: string): string {
    let formatted = text
    for (const [id, code] of nodeCodeById.entries()) {
      formatted = formatted.replace(new RegExp(`\\b${id}\\b`, "g"), code)
    }
    return formatted
  }

  useEffect(() => {
    setAdminKey(cachedGet("live.admin_key", ""))
    setChainId(cachedGet("live.chain_id", ""))
    setTokens(cachedGet<Array<{ node_id: string; api_token: string }>>("live.node_tokens", []))
    const cachedNodes = cachedGet<NodeInput[]>("live.nodes", [])
    const cachedEdges = cachedGet<EdgeInput[]>("live.edges", [])
    setNodesJson(JSON.stringify(cachedNodes, null, 2))
    setEdgesJson(JSON.stringify(cachedEdges, null, 2))
    setPreviewNodes(cachedNodes)
    setPreviewEdges(cachedEdges)
  }, [])

  useEffect(() => {
    cachedSet("live.admin_key", adminKey)
  }, [adminKey])

  useEffect(() => {
    cachedSet("live.chain_id", chainId)
  }, [chainId])

  useEffect(() => {
    try {
      const parsed = JSON.parse(nodesJson) as NodeInput[]
      if (Array.isArray(parsed)) {
        setPreviewNodes(parsed)
      }
    } catch {
      setPreviewNodes([])
    }
  }, [nodesJson])

  useEffect(() => {
    try {
      const parsed = JSON.parse(edgesJson) as EdgeInput[]
      if (Array.isArray(parsed)) {
        setPreviewEdges(parsed)
      }
    } catch {
      setPreviewEdges([])
    }
  }, [edgesJson])

  const chainExists = chainId.trim().length > 0

  async function createChain() {
    setError("")
    setLoading(true)
    try {
      const nodes = JSON.parse(nodesJson) as NodeInput[]
      const edges = JSON.parse(edgesJson) as EdgeInput[]
      cachedSet("live.nodes", nodes)
      cachedSet("live.edges", edges)

      const response = await fetch(`${API_BASE}/live/chains`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-Api-Key": adminKey,
        },
        body: JSON.stringify({ chain_name: chainName, nodes, edges }),
      })
      const payload = await response.json() as { chain_id?: string; node_tokens?: Array<{ node_id: string; api_token: string }>; detail?: string }
      if (!response.ok || !payload.chain_id) {
        throw new Error(payload.detail || `create failed (${response.status})`)
      }
      setChainId(payload.chain_id)
      setTokens(payload.node_tokens ?? [])
      cachedSet("live.node_tokens", payload.node_tokens ?? [])
      setSuggestions([])
      setEvents([])
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  async function refreshLiveData() {
    if (!adminKey || !chainId) {
      return
    }
    setError("")
    try {
      const headers = { "X-Admin-Api-Key": adminKey }
      const [snapshotResp, suggestionsResp, eventsResp] = await Promise.all([
        fetch(`${API_BASE}/live/chains/${chainId}/snapshot`, { headers, cache: "no-store" }),
        fetch(`${API_BASE}/live/chains/${chainId}/suggestions`, { headers, cache: "no-store" }),
        fetch(`${API_BASE}/live/chains/${chainId}/events`, { headers, cache: "no-store" }),
      ])

      const snapshot = await snapshotResp.json() as { nodes?: NodeInput[]; edges?: EdgeInput[]; shipments?: LiveShipment[]; detail?: string }
      const suggestionPayload = await suggestionsResp.json() as { suggestions?: SuggestionLog[]; detail?: string }
      const eventPayload = await eventsResp.json() as { events?: NodeEvent[]; detail?: string }

      if (!snapshotResp.ok) {
        throw new Error(snapshot.detail || `snapshot failed (${snapshotResp.status})`)
      }
      if (!suggestionsResp.ok) {
        throw new Error(suggestionPayload.detail || `suggestions failed (${suggestionsResp.status})`)
      }
      if (!eventsResp.ok) {
        throw new Error(eventPayload.detail || `events failed (${eventsResp.status})`)
      }

      setPreviewNodes(snapshot.nodes ?? [])
      setPreviewEdges(snapshot.edges ?? [])
      setShipments(snapshot.shipments ?? [])
      setSuggestions(suggestionPayload.suggestions ?? [])
      setEvents(eventPayload.events ?? [])
      cachedSet(`live.suggestions.${chainId}`, suggestionPayload.suggestions ?? [])
      cachedSet(`live.events.${chainId}`, eventPayload.events ?? [])
    } catch (err) {
      setError((err as Error).message)
      setSuggestions(cachedGet(`live.suggestions.${chainId}`, [] as SuggestionLog[]))
      setEvents(cachedGet(`live.events.${chainId}`, [] as NodeEvent[]))
    }
  }

  async function deleteChain() {
    if (!adminKey || !chainId) {
      return
    }
    setError("")
    setLoading(true)
    try {
      const response = await fetch(`${API_BASE}/live/chains/${chainId}`, {
        method: "DELETE",
        headers: {
          "X-Admin-Api-Key": adminKey,
        },
      })
      const payload = await response.json() as { ok?: boolean; detail?: string }
      if (!response.ok) {
        throw new Error(payload.detail || `delete failed (${response.status})`)
      }

      cachedRemove(`live.suggestions.${chainId}`)
      cachedRemove(`live.events.${chainId}`)
      cachedRemove("live.chain_id")
      cachedRemove("live.node_tokens")
      setChainId("")
      setTokens([])
      setSuggestions([])
      setEvents([])
      setDecisionExplanations({})
      setActiveExplainDecisionId(null)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  async function explainDecision(decisionId: string) {
    setActiveExplainDecisionId(decisionId)
    if (decisionExplanations[decisionId]) {
      return
    }
    setLoadingExplainDecisionId(decisionId)
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
      setLoadingExplainDecisionId(current => (current === decisionId ? null : current))
    }
  }

  return (
    <main className="grid min-h-svh place-items-center bg-gradient-to-br from-background via-background to-muted/40 p-6">
      <section className="relative h-[84vh] w-full max-w-[96vw] overflow-hidden rounded-none border border-border bg-card">
        <LiveTopNav />
        <div className="flex h-[calc(100%-2.5rem)] min-h-0">
          <section className="flex min-w-0 flex-1 flex-col border-r border-border/80 bg-card/95">
            <SupplyChainMap nodes={previewNodes} edges={previewEdges} shipments={shipments} />
            <div className="border-t border-border/70 bg-card/95 px-3 py-1 text-[9px] text-muted-foreground">
              Live graph preview | Nodes load after valid JSON | Edges appear when valid
            </div>
          </section>

          {!chainExists ? (
            <aside className="flex w-[30rem] flex-col bg-card/95">
              <div className="border-b border-border/70 px-3 py-2 text-xs font-medium tracking-[0.08em] text-muted-foreground md:text-sm">CREATE SUPPLY CHAIN</div>
              <div className="min-h-0 flex-1 overflow-y-auto p-3">
                <label className="flex flex-col gap-1 text-[10px] text-muted-foreground">
                  <span>Supply Admin API Key</span>
                  <input value={adminKey} onChange={event => setAdminKey(event.target.value)} className="h-8 rounded-none border border-border/70 bg-background/35 px-2 text-xs text-foreground" />
                </label>
                <label className="mt-2 flex flex-col gap-1 text-[10px] text-muted-foreground">
                  <span>Chain Name</span>
                  <input value={chainName} onChange={event => setChainName(event.target.value)} className="h-8 rounded-none border border-border/70 bg-background/35 px-2 text-xs text-foreground" />
                </label>
                <label className="mt-2 flex flex-col gap-1 text-[10px] text-muted-foreground">
                  <span>Nodes JSON</span>
                  <textarea value={nodesJson} onChange={event => setNodesJson(event.target.value)} className="h-44 rounded-none border border-border/70 bg-background/35 p-2 text-[10px] text-foreground" />
                </label>
                <label className="mt-2 flex flex-col gap-1 text-[10px] text-muted-foreground">
                  <span>Edges JSON</span>
                  <textarea value={edgesJson} onChange={event => setEdgesJson(event.target.value)} className="h-36 rounded-none border border-border/70 bg-background/35 p-2 text-[10px] text-foreground" />
                </label>
                <button type="button" onClick={createChain} disabled={loading} className="mt-2 w-full rounded-none border border-border/70 bg-background/35 px-2 py-1 text-[10px] font-semibold text-foreground hover:bg-foreground/10 disabled:opacity-60">
                  {loading ? "Submitting..." : "Submit Chain"}
                </button>
                {error ? <div className="mt-2 text-[10px] text-amber-300">{error}</div> : null}
                {tokens.length > 0 ? (
                  <div className="mt-3 border-t border-border/70 pt-2">
                    <div className="text-[10px] font-semibold tracking-[0.08em] text-muted-foreground">NODE TOKENS (SHOW ONCE)</div>
                    <div className="mt-2 space-y-2">
                      {tokens.map(item => (
                        <div key={item.node_id} className="rounded-none border border-border/60 bg-background/30 p-2 text-[10px]">
                          <div className="text-muted-foreground">{item.node_id}</div>
                          <div className="mt-1 break-all text-foreground/95">{item.api_token}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            </aside>
          ) : (
            <>
              <aside className="flex w-[24rem] flex-col border-l border-border/80 bg-card/95">
                <div className="flex items-center justify-between border-b border-border/70 px-3 py-2 text-[11px] font-semibold tracking-[0.08em] text-muted-foreground">
                  <span>SUGGESTIONS + EVENT LOG</span>
                  <div className="flex items-center gap-2">
                    <button type="button" onClick={() => void refreshLiveData()} className="rounded-none border border-border/70 bg-background/35 px-2 py-1 text-[10px] font-semibold text-foreground hover:bg-foreground/10">
                      Refresh
                    </button>
                    <button type="button" onClick={() => void deleteChain()} disabled={loading} className="rounded-none border border-rose-300/30 bg-rose-400/10 px-2 py-1 text-[10px] font-semibold text-rose-200 hover:bg-rose-400/15 disabled:opacity-60">
                      Delete Chain
                    </button>
                  </div>
                </div>
                <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
                  <div className="rounded-none border border-border/60 bg-background/25 p-2 text-[10px]">
                    <div className="text-muted-foreground">CHAIN ID</div>
                    <div className="mt-1 break-all text-foreground/95">{chainId}</div>
                  </div>
                  {timelineItems.map(item => (
                    <div key={item.id} className="rounded-none border border-border/60 bg-background/30 p-2 text-[10px]">
                      <div className={`font-semibold tracking-[0.08em] ${item.kind === "suggestion" ? "text-sky-200" : "text-amber-200"}`}>
                        {item.kind === "suggestion" ? "SUGGESTION" : "EVENT"}
                      </div>
                      <div className="mt-1 text-foreground/95">{item.message}</div>
                      <div className="mt-1 text-muted-foreground">{formatTimestamp(item.created_at)}</div>
                      {item.kind === "suggestion" && item.decision_id ? (
                        <button type="button" className="mt-2 rounded-none border border-sky-300/40 bg-sky-400/12 px-2 py-0.5 text-[10px] text-sky-200" onClick={() => void explainDecision(item.decision_id as string)}>
                          {loadingExplainDecisionId === item.decision_id ? "Loading..." : "Why"}
                        </button>
                      ) : null}
                      {item.kind === "suggestion" && item.decision_id && activeExplainDecisionId === item.decision_id && (loadingExplainDecisionId === item.decision_id || decisionExplanations[item.decision_id]) ? (
                        <div className="mt-2 whitespace-pre-wrap rounded-none border border-border/60 bg-background/25 p-2 text-[10px] text-foreground/95">
                          {loadingExplainDecisionId === item.decision_id ? "Loading explanation..." : formatNodeRefs(decisionExplanations[item.decision_id])}
                        </div>
                      ) : null}
                    </div>
                  ))}
                  {timelineItems.length === 0 ? <div className="rounded-none border border-border/60 bg-background/25 p-2 text-xs text-muted-foreground">No suggestions or events yet.</div> : null}
                </div>
              </aside>

              <aside className={`flex flex-col border-l border-border/80 bg-card/95 transition-[width] duration-200 ${tokensOpen ? "w-[20rem]" : "w-12"}`}>
                {tokensOpen ? (
                  <>
                    <div className="flex items-center justify-between border-b border-border/70 px-3 py-2 text-[11px] font-semibold tracking-[0.08em] text-muted-foreground">
                      <span>NODE TOKENS</span>
                      <button type="button" onClick={() => setTokensOpen(false)} className="rounded-none border border-border/70 bg-background/35 px-2 py-1 text-[10px] font-semibold text-foreground hover:bg-foreground/10">
                        Hide
                      </button>
                    </div>
                  <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
                    {tokens.length > 0 ? (
                      tokens.map(item => (
                        <div key={item.node_id} className="rounded-none border border-border/60 bg-background/30 p-2 text-[10px]">
                          <div className="text-muted-foreground">{item.node_id}</div>
                          <div className="mt-1 break-all text-foreground/95">{item.api_token}</div>
                        </div>
                      ))
                    ) : (
                      <div className="rounded-none border border-border/60 bg-background/25 p-2 text-xs text-muted-foreground">No node tokens cached.</div>
                    )}
                  </div>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => setTokensOpen(true)}
                    className="flex h-full w-full items-center justify-center border-0 bg-transparent px-0 text-[10px] font-semibold tracking-[0.08em] text-muted-foreground hover:bg-foreground/5 hover:text-foreground"
                    aria-label="Show node tokens"
                    title="Show node tokens"
                  >
                    <span className="origin-center rotate-[-90deg] whitespace-nowrap">SHOW TOKENS</span>
                  </button>
                )}
              </aside>
              {error ? <div className="absolute bottom-0 right-12 border-l border-t border-border/70 bg-card/95 p-2 text-[10px] text-amber-300">{error}</div> : null}
            </>
          )}
        </div>
      </section>
    </main>
  )
}
