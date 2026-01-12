export type TooltipKey =
  | 'nav.dashboard'
  | 'nav.integrity'
  | 'nav.incidents'
  | 'nav.trustlines'
  | 'nav.graph'
  | 'nav.participants'
  | 'nav.config'
  | 'nav.featureFlags'
  | 'nav.auditLog'
  | 'nav.equivalents'
  | 'dashboard.api'
  | 'dashboard.db'
  | 'dashboard.migrations'
  | 'dashboard.bottlenecks'
  | 'dashboard.incidentsOverSla'
  | 'dashboard.recentAudit'
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
  | 'graph.eq'
  | 'graph.status'
  | 'graph.threshold'
  | 'graph.layout'
  | 'graph.type'
  | 'graph.minDegree'
  | 'graph.labels'
  | 'graph.incidents'
  | 'graph.hideIsolates'
  | 'graph.search'
  | 'graph.zoom'
  | 'graph.actions'
  | 'graph.spacing'
  | 'graph.legend'
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
  // Navigation section tooltips
  'nav.dashboard': {
    title: 'Dashboard',
    body: ['System health overview: API, DB, migrations status.', 'Shows trustline bottlenecks and incidents over SLA.'],
  },
  'nav.integrity': {
    title: 'Integrity',
    body: ['Data integrity checks: zero-sum balances, limits consistency.', 'Automated validation of network invariants.'],
  },
  'nav.incidents': {
    title: 'Incidents',
    body: ['Stuck transactions requiring manual intervention.', 'Sorted by age; admin can force-abort.'],
  },
  'nav.trustlines': {
    title: 'Trustlines',
    body: ['Credit lines between network participants.', 'Filter by equivalent, creditor, debtor, status.'],
  },
  'nav.graph': {
    title: 'Network Graph',
    body: ['Visual representation of the trust network.', 'Interactive graph with filtering and zoom.'],
  },
  'nav.participants': {
    title: 'Participants',
    body: ['Manage network participants.', 'View details, freeze/unfreeze accounts.'],
  },
  'nav.config': {
    title: 'Config',
    body: ['System configuration: limits, routing, policies.', 'Some keys require service restart.'],
  },
  'nav.featureFlags': {
    title: 'Feature Flags',
    body: ['Runtime feature toggles.', 'Enable/disable experimental features.'],
  },
  'nav.auditLog': {
    title: 'Audit Log',
    body: ['Administrative actions log.', 'Who, what, when, and why for all changes.'],
  },
  'nav.equivalents': {
    title: 'Equivalents',
    body: ['Currency/unit catalog (UAH, USD, POINT).', 'Precision and description for each.'],
  },

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
  'dashboard.recentAudit': {
    title: 'Recent audit log',
    body: ['Latest 10 administrative actions.', 'Shows who did what and when.'],
    links: [{ label: 'Open Audit Log', to: { path: '/audit-log' } }],
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
    title: 'Equivalent',
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
    title: 'Equivalent',
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

  'graph.eq': {
    title: 'Equivalent filter',
    body: ['Filters edges by equivalent code.', 'ALL shows all equivalents present in fixtures.'],
    links: [{ label: 'Open Equivalents', to: { path: '/equivalents' } }],
  },
  'graph.status': {
    title: 'Status filter',
    body: ['Filters edges by trustline status.'],
    links: [{ label: 'Open Trustlines', to: { path: '/trustlines' } }],
  },
  'graph.threshold': {
    title: 'Bottleneck threshold',
    body: ['A trustline is a bottleneck when available / limit is below this value.', 'Uses decimal-safe math (no floats).'],
  },
  'graph.layout': {
    title: 'Layout',
    body: ['Controls how the graph is arranged.', 'fcose is good for clustered networks; grid/circle are deterministic.'],
  },
  'graph.type': {
    title: 'Participant type',
    body: [
      'Filters nodes by participant type (e.g., person vs business).',
      'Edges are shown only between same-type participants (cross-type edges are hidden).',
    ],
  },
  'graph.minDegree': {
    title: 'Minimum degree',
    body: ['Hides low-connected nodes to reduce noise.', 'Degree is computed after current filters are applied.'],
  },
  'graph.labels': {
    title: 'Labels',
    body: ['Shows display name + PID on nodes.', 'Disable for performance on large graphs.'],
  },
  'graph.incidents': {
    title: 'Incidents',
    body: ['Highlights nodes/edges related to incident initiators.', 'Dashed edges indicate the initiator side.'],
    links: [{ label: 'Open Incidents', to: { path: '/incidents' } }],
  },
  'graph.hideIsolates': {
    title: 'Hide isolates',
    body: ['When enabled, shows only participants that appear in trustlines after filtering.'],
  },
  'graph.search': {
    title: 'Search',
    body: [
      'Search by PID or participant name (partial match).',
      'Pick a suggestion to set the focus node used by Find and Fit component.',
      'If multiple matches exist, Find fits and highlights a subset; refine the query to narrow it down.',
    ],
  },
  'graph.zoom': {
    title: 'Zoom helpers',
    body: [
      'Use the slider or mouse wheel to zoom in/out.',
      'Edge thickness and label size are adjusted with zoom for readability.',
    ],
  },

  'graph.actions': {
    title: 'Graph actions',
    body: [
      'Find: centers on the focused participant (picked from search) or the last clicked node.',
      'Fit: fits the whole graph into the viewport.',
      'Re-layout: runs the selected layout algorithm again (use after changing filters/spacing).',
      'Zoom: use the slider to zoom in/out (also works with mouse wheel).',
    ],
  },

  'graph.spacing': {
    title: 'Layout spacing',
    body: ['Controls how spread-out the force layout is.', 'Higher values reduce clutter but take longer to settle.'],
  },
  'graph.legend': {
    title: 'Legend',
    body: ['Explains node/edge colors and styles used in this visualization.'],
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
