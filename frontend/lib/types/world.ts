export type NodeType = "warehouse" | "hub" | "city" | "airport"

export type Node = {
  id: string
  name: string
  type: NodeType
  region: string
  capacity: number
  x: number
  y: number
}

export type Edge = {
  id: string
  source: string
  target: string
  base_eta: number
  base_cost: number
}

export type World = {
  nodes: Node[]
  edges: Edge[]
}
