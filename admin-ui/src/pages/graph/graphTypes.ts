export type Participant = {
  pid: string
  display_name?: string
  type?: string
  status?: string

  // Backend-provided net visualization fields (present only when equivalent is specified).
  net_balance_atoms?: string | null
  net_sign?: -1 | 0 | 1 | null
  viz_color_key?: 'person' | 'business' | 'debt' | 'suspended' | 'left' | 'deleted' | null
  viz_size?: { w: number; h: number } | null
}

export type Trustline = {
  equivalent: string
  from: string
  to: string
  limit: string
  used: string
  available: string
  status: string
  created_at: string
}

export type Debt = {
  equivalent: string
  debtor: string
  creditor: string
  amount: string
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

export type AuditLogEntry = {
  id: string
  timestamp: string
  actor_id?: string
  actor_role?: string
  action: string
  object_type: string
  object_id: string
  reason?: string | null
  before_state?: unknown
  after_state?: unknown
  request_id?: string
  ip_address?: string
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

export type Equivalent = { code: string; precision: number; description: string; is_active: boolean }

export type GraphSnapshotPayload = {
  participants: Participant[]
  trustlines: Trustline[]
  incidents: Incident[]
  equivalents: Equivalent[]
  debts: Debt[]
  audit_log: AuditLogEntry[]
  transactions: Transaction[]
}

export type ClearingCycles = {
  equivalents: Record<
    string,
    {
      cycles: Array<
        Array<{
          equivalent: string
          debtor: string
          creditor: string
          amount: string
        }>
      >
    }
  >
}
