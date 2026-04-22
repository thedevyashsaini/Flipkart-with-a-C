export type SimulationLog = {
  tick: number
  message: string
}

export type NodeSimulationStat = {
  queued_shipments: number
  inbound_shipments: number
  outbound_shipments: number
  holding_shipments: number
  consumed_shipments_tick: number
  active_load_tons: number
  capacity: number
}

export type EdgeSimulationStat = {
  dispatched_shipments_tick: number
  dispatched_load_tick: number
  in_transit_shipments: number
  in_transit_load: number
  avg_remaining_ticks: number
  min_remaining_ticks: number
  max_remaining_ticks: number
}

export type SimulationState = {
  tick: number
  running: boolean
  failed: boolean
  failure_reason: string
  full_nodes: number
  failed_node_names: string[]
  generated_shipments_tick: number
  blocked_admission_tick: number
  moved_shipments_tick: number
  moved_load_tick: number
  queued_shipments: number
  in_transit_shipments: number
  holding_shipments: number
  consumed_shipments: number
  node_stats: Record<string, NodeSimulationStat>
  edge_stats: Record<string, EdgeSimulationStat>
  recent_logs: SimulationLog[]
}
