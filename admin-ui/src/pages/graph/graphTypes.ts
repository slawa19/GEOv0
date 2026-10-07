export type Participant = {
  pid: string
  display_name?: string
  type?: string
  status?: string

  // Backend-provided net visualization fields (present only when equivalent is specified).
  net_balance_atoms?: string | null
  net_sign?: -1 | 0 | 1 | null
  viz_color_key?:
    | 'person'
    | 'business'
    | 'debt'
    | `debt-${0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8}`
    | 'suspended'
    | 'left'
    | 'deleted'
    | null
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
  close_requested_at?: string | null
}

export type Equivalent = { code: string; precision: number; description: string; is_active: boolean }

// 032 S5 (F-1): the graph page reads only these collections of a snapshot; the optional
// `audit_log` / `transactions` collections fed the removed activity card and are not requested.
export type GraphSnapshotPayload = {
  participants: Participant[]
  trustlines: Trustline[]
  equivalents: Equivalent[]
}
