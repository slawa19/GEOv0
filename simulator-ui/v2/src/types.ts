export type GraphSnapshot = {
  equivalent: string
  generated_at: string
  nodes: GraphNode[]
  links: GraphLink[]
  palette?: Record<string, { color: string; label?: string }>
  limits?: {
    max_nodes?: number
    max_links?: number
    max_particles?: number
  }
}

export type GraphNode = {
  id: string
  name?: string
  type?: string
  status?: string
  links_count?: number
  net_balance_atoms?: string | null
  net_sign?: -1 | 0 | 1 | null
  // Backend-authoritative signed major-units value (preferred for display).
  net_balance?: string | null
  viz_color_key?: string | null
  viz_shape_key?: string | null
  viz_size?: { w: number; h: number } | null
  viz_badge_key?: string | null
}

export type GraphLink = {
  id?: string
  source: string
  target: string
  trust_limit?: string | number
  used?: string | number
  available?: string | number
  status?: string
  viz_color_key?: string | null
  viz_width_key?: string | null
  viz_alpha_key?: string | null
}

export type NodePatch = {
  id: string
  net_balance_atoms?: string | null
  net_sign?: -1 | 0 | 1 | null
  net_balance?: string | null
  viz_color_key?: string | null
  viz_shape_key?: string | null
  viz_size?: { w: number; h: number } | null
}

export type EdgePatch = {
  source: string
  target: string
  used?: string | number
  available?: string | number
  viz_color_key?: string | null
  viz_width_key?: string | null
  viz_alpha_key?: string | null
}

export type TxUpdatedEvent = {
  event_id: string
  ts: string
  type: 'tx.updated'
  equivalent: string
  // Optional explicit endpoints + amount in major units (backend-first).
  from?: string
  to?: string
  amount?: string

  // Explicit control from backend whether amount flyout labels should be emitted.
  // - true: labels are expected (amount + endpoints must be resolvable)
  // - false/undefined: best-effort/backward-compatible
  amount_flyout?: boolean
  ttl_ms: number
  intensity_key?: string
  edges: Array<{ from: string; to: string; style?: { viz_width_key?: string; viz_alpha_key?: string } }>
  node_badges?: Array<{ id: string; viz_badge_key: string | null }>
  node_patch?: NodePatch[]
  edge_patch?: EdgePatch[]
}

export type ClearingPlanEvent = {
  event_id: string
  ts: string
  type: 'clearing.plan'
  equivalent: string
  plan_id: string
  steps: Array<{
    at_ms: number
    intensity_key?: string
    highlight_edges?: Array<{ from: string; to: string }>
    particles_edges?: Array<{ from: string; to: string }>
  }>
}

export type ClearingDoneEvent = {
  event_id: string
  ts: string
  type: 'clearing.done'
  equivalent: string
  plan_id: string
  cleared_cycles?: number
  cleared_amount?: string
  node_patch?: NodePatch[]
  edge_patch?: EdgePatch[]
}

export type DemoEvent = TxUpdatedEvent | ClearingPlanEvent | ClearingDoneEvent
