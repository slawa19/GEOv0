export type Paginated<T> = { items: T[]; page: number; per_page: number; total: number }

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
