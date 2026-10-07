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
  /** 026: the creditor asked to close; limit 0, the line stays live until the debt it supports is repaid. */
  close_requested_at?: string | null
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
  actor_id?: string | null
  actor_role?: string | null
  action: string
  object_type?: string | null
  object_id?: string | null
  reason?: string | null
  before_state?: unknown
  after_state?: unknown
  request_id?: string | null
  ip_address?: string | null
  user_agent?: string | null
}

export type Debt = {
  equivalent: string
  debtor: string
  creditor: string
  amount: string
}

// 032 S5 (F-2, F-3): one equivalent's active lines and their money - the Dashboard row.
export type LiquiditySummary = {
  equivalent: string | null
  updated_at: string
  active_trustlines: number
  // 028 F-028-37: null without an equivalent - money is never summed across equivalents.
  total_limit: string | null
  total_used: string | null
  total_available: string | null
}

// F-013-1 / T1302. The graph snapshot's projection of a transaction. `payload` is NOT on that
// wire - the producer lifts `equivalent` out of it and publishes nothing else - so `payload` is
// optional here and `equivalent` is the top-level field a consumer must read. The mock fixture
// still carries a full `payload`, which is exactly why a consumer test built on the fixture proves
// nothing about real mode.
export type Transaction = {
  id?: string
  tx_id: string
  idempotency_key?: string | null
  type: string
  /** Null on a CLEARING, which records no initiator (028 F-028-45). */
  initiator_pid: string | null
  equivalent?: string | null
  /** PAYMENT: who paid. Absent when the internal payload did not carry it. */
  from?: string
  /** PAYMENT: who was paid. */
  to?: string
  /** CLEARING: the cycle reduced to who owed whom. Amounts and debt ids stay internal. */
  edges?: Array<{ debtor: string; creditor: string }>
  payload?: Record<string, unknown>
  signatures?: unknown[] | null
  state: string
  error?: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export type GraphSnapshot = {
  participants: Participant[]
  trustlines: Trustline[]
  equivalents: Equivalent[]
  debts: Debt[]
  audit_log: AuditLogEntry[]
  transactions: Transaction[]
  // F-013-1 / T1302. `included` names the optional collections this body actually carries;
  // `truncated` names those of them that hit the include limit, so their counts are lower bounds.
  // Optional because the canon does not require them - a server that omits them has told us
  // nothing, which is not the same as telling us the collection was empty.
  included?: string[]
  truncated?: string[]
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

// 032 S5 (F-1): the participant metrics are the balance rows; the analytics were removed.
export type ParticipantMetrics = {
  pid: string
  equivalent: string | null
  balance_rows: BalanceRow[]
}
