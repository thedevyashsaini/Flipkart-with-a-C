export type DemandLaneSummary = {
  source_region: string
  destination_region: string
  shipment_count: number
  total_load: number
  avg_load: number
}

export type DemandState = {
  seed: number
  tick: number
  running: boolean
  total_generated: number
  in_window_shipments: number
  within_region: DemandLaneSummary[]
  cross_region: DemandLaneSummary[]
}
