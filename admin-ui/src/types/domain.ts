export type Paginated<T> = { items: T[]; page: number; per_page: number; total: number }

export type ParticipantsStats = {
  participants_by_status: Record<string, number>
  participants_by_type: Record<string, number>
  total_participants: number
}

export type Participant = {
  pid: string
  display_name: string
  type: string
  status: string
  created_at?: string
  meta?: Record<string, unknown>
}

export type Trustline = {
  equivalent: string
  from: string
  to: string
  from_display_name?: string | null
  to_display_name?: string | null
  limit: string
  used: string
  available: string
  status: string
  created_at: string
  policy?: Record<string, unknown>
}

export type Incident = {
  tx_id: string
  state: string
  initiator_pid: string
  equivalent: string
  age_seconds: number
  sla_seconds: number
  created_at?: string
}

export type Equivalent = {
  code: string
  precision: number
  description: string
  is_active: boolean
}

export type AuditLogEntry = {
  id: string
  timestamp: string
  actor_id: string
  actor_role: string
  action: string
  object_type: string
  object_id: string
  reason?: string | null
  before_state?: unknown
  after_state?: unknown
  request_id?: string
  ip_address?: string
}

export type Debt = {
  equivalent: string
  debtor: string
  creditor: string
  amount: string
}

export type LiquidityNetRow = {
  pid: string
  display_name: string
  net: string
}

export type LiquiditySummary = {
  equivalent: string | null
  threshold: number
  updated_at: string
  active_trustlines: number
  bottlenecks: number
  incidents_over_sla: number
  total_limit: string
  total_used: string
  total_available: string
  top_creditors: LiquidityNetRow[]
  top_debtors: LiquidityNetRow[]
  top_by_abs_net: LiquidityNetRow[]
  top_bottleneck_edges: Trustline[]
}

export type Transaction = {
  id?: string
  tx_id: string
  idempotency_key?: string | null
  type: string
  initiator_pid: string
  payload: Record<string, unknown>
  signatures?: unknown[] | null
  state: string
  error?: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export type GraphSnapshot = {
  participants: Participant[]
  trustlines: Trustline[]
  incidents: Incident[]
  equivalents: Equivalent[]
  debts: Debt[]
  audit_log: AuditLogEntry[]
  transactions: Transaction[]
}

export type ClearingCycleEdge = {
  equivalent: string
  debtor: string
  creditor: string
  amount: string
}

export type ClearingCycles = {
  equivalents: Record<string, { cycles: ClearingCycleEdge[][] }>
}

export type BalanceRow = {
  equivalent: string
  outgoing_limit: string
  outgoing_used: string
  incoming_limit: string
  incoming_used: string
  total_debt: string
  total_credit: string
  net: string
}

export type CounterpartySplitRow = {
  pid: string
  display_name: string
  amount: string
  share: number
}

export type ParticipantMetrics = {
  pid: string
  equivalent: string | null
  balance_rows: BalanceRow[]
  counterparty?: {
    eq: string
    totalDebt: string
    totalCredit: string
    creditors: CounterpartySplitRow[]
    debtors: CounterpartySplitRow[]
  } | null
  concentration?: {
    eq: string
    outgoing: { top1: number; top5: number; hhi: number }
    incoming: { top1: number; top5: number; hhi: number }
  } | null
  distribution?: {
    eq: string
    min_atoms: string
    max_atoms: string
    bins: Array<{ from_atoms: string; to_atoms: string; count: number }>
  } | null
  rank?: {
    eq: string
    rank: number
    n: number
    percentile: number
    net: string
  } | null
  capacity?: {
    eq: string
    out: { limit: string; used: string; pct: number }
    inc: { limit: string; used: string; pct: number }
    bottlenecks: Array<{ dir: 'out' | 'in'; other: string; trustline: Trustline }>
  } | null
  activity?: {
    windows: number[]
    trustline_created: Record<number, number>
    trustline_closed: Record<number, number>
    incident_count: Record<number, number>
    participant_ops: Record<number, number>
    payment_committed: Record<number, number>
    clearing_committed: Record<number, number>
    has_transactions: boolean
  } | null
}
