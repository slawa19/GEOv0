export type TooltipKey =
  | 'dashboard.api'
  | 'dashboard.db'
  | 'dashboard.migrations'
  | 'dashboard.bottlenecks'
  | 'dashboard.incidentsOverSla'
  | 'participants.pid'
  | 'participants.displayName'
  | 'participants.type'
  | 'participants.status'
  | 'trustlines.eq'
  | 'trustlines.from'
  | 'trustlines.to'
  | 'trustlines.limit'
  | 'trustlines.used'
  | 'trustlines.available'
  | 'trustlines.status'
  | 'trustlines.createdAt'
  | 'incidents.txId'
  | 'incidents.state'
  | 'incidents.initiator'
  | 'incidents.eq'
  | 'incidents.age'
  | 'incidents.sla'
  | 'audit.timestamp'
  | 'audit.actor'
  | 'audit.role'
  | 'audit.action'
  | 'audit.objectType'
  | 'audit.objectId'
  | 'audit.reason'

export type TooltipLink = {
  label: string
  to: { path: string; query?: Record<string, string> }
}

export type TooltipContent = {
  title: string
  body: string[]
  links?: TooltipLink[]
}

// Keep content short and review-friendly.
export const TOOLTIPS: Record<TooltipKey, TooltipContent> = {
  'dashboard.api': {
    title: 'API',
    body: ['Basic service health from /api/v1/health.', 'Used to validate that the backend is reachable.'],
  },
  'dashboard.db': {
    title: 'DB',
    body: ['Database reachability/latency from /api/v1/health/db.', 'Helps detect DB connectivity issues.'],
  },
  'dashboard.migrations': {
    title: 'Migrations',
    body: ['Schema migration status from /api/v1/admin/migrations.', 'Should be “up to date” on healthy deployments.'],
  },
  'dashboard.bottlenecks': {
    title: 'Trustline bottlenecks',
    body: ['Trustlines where available / limit is below the threshold.', 'Threshold is a UI control and uses decimal-safe math (no floats).'],
    links: [{ label: 'Open Trustlines', to: { path: '/trustlines' } }],
  },
  'dashboard.incidentsOverSla': {
    title: 'Incidents over SLA',
    body: ['Transactions stuck beyond their SLA.', 'age_seconds > sla_seconds → over-SLA.'],
    links: [{ label: 'Open Incidents', to: { path: '/incidents' } }],
  },

  'participants.pid': {
    title: 'PID',
    body: ['Participant identifier in the GEO network.'],
  },
  'participants.displayName': {
    title: 'Name',
    body: ['Human-friendly display name (if set).'],
  },
  'participants.type': {
    title: 'Type',
    body: ['Participant category (e.g., user/org/admin depending on backend).'],
  },
  'participants.status': {
    title: 'Status',
    body: ['Account status used for access/routing decisions.', 'Typical values: active, frozen, banned.'],
  },

  'trustlines.eq': {
    title: 'eq',
    body: ['Equivalent (currency/unit) code, e.g. UAH, USD.'],
    links: [{ label: 'Open Equivalents', to: { path: '/equivalents' } }],
  },
  'trustlines.from': {
    title: 'from',
    body: ['Creditor PID (trustline source).'],
  },
  'trustlines.to': {
    title: 'to',
    body: ['Debtor PID (trustline destination).'],
  },
  'trustlines.limit': {
    title: 'limit',
    body: ['Trustline credit limit (decimal string).', 'Edge case: limit = 0 is allowed.'],
    links: [
      {
        label: 'Default limit (Config)',
        to: { path: '/config', query: { key: 'limits.default_trustline_limit' } },
      },
    ],
  },
  'trustlines.used': {
    title: 'used',
    body: ['Currently reserved/used amount on the trustline (decimal string).'],
  },
  'trustlines.available': {
    title: 'available',
    body: ['Remaining available amount (decimal string).', 'Shown in red when below the threshold.'],
  },
  'trustlines.status': {
    title: 'status',
    body: ['Trustline lifecycle state.', 'Typical values: active, frozen, closed.'],
  },
  'trustlines.createdAt': {
    title: 'created_at',
    body: ['Creation timestamp (ISO 8601).'],
  },

  'incidents.txId': {
    title: 'tx_id',
    body: ['Transaction identifier.'],
  },
  'incidents.state': {
    title: 'state',
    body: ['Current state of the stuck transaction.'],
  },
  'incidents.initiator': {
    title: 'initiator',
    body: ['PID of the transaction initiator.'],
  },
  'incidents.eq': {
    title: 'eq',
    body: ['Equivalent involved in the transaction.'],
    links: [{ label: 'Open Equivalents', to: { path: '/equivalents' } }],
  },
  'incidents.age': {
    title: 'age',
    body: ['How long the transaction has been stuck (seconds).'],
  },
  'incidents.sla': {
    title: 'sla',
    body: ['Allowed time budget (seconds).', 'age > sla → over-SLA.'],
  },

  'audit.timestamp': {
    title: 'timestamp',
    body: ['When the admin/audit event happened (ISO 8601).'],
  },
  'audit.actor': {
    title: 'actor',
    body: ['Who performed the action (PID/user id depending on backend).'],
  },
  'audit.role': {
    title: 'role',
    body: ['Role under which the action was performed.'],
  },
  'audit.action': {
    title: 'action',
    body: ['Action name (e.g., create/update/verify).'],
  },
  'audit.objectType': {
    title: 'object',
    body: ['Domain object type affected by the action.'],
  },
  'audit.objectId': {
    title: 'object_id',
    body: ['Identifier of the affected object.'],
  },
  'audit.reason': {
    title: 'reason',
    body: ['Optional operator-provided reason for the action.'],
  },
}
