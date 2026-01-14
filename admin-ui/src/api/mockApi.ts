import { ApiException, type ApiEnvelope } from './envelope'

type ScenarioOverride =
  | { mode: 'empty' }
  | { mode: 'error'; status: number; code: string; message?: string; details?: unknown }

type Scenario = {
  name: string
  description?: string
  latency_ms?: { min: number; max: number }
  overrides?: Record<string, ScenarioOverride>
}

type Paginated<T> = { items: T[]; page: number; per_page: number; total: number }

type Participant = { pid: string; display_name: string; type: string; status: string }

type Trustline = {
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
  policy: Record<string, unknown>
}

type AuditLogEntry = {
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

type Incident = {
  tx_id: string
  state: string
  initiator_pid: string
  equivalent: string
  age_seconds: number
  created_at?: string
  sla_seconds: number
}

type Debt = {
  equivalent: string
  debtor: string
  creditor: string
  amount: string
}

type Transaction = {
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

type ClearingCycles = {
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

type GraphSnapshot = {
  participants: Participant[]
  trustlines: Trustline[]
  incidents: Incident[]
  equivalents: Equivalent[]
  debts: Debt[]
  audit_log: AuditLogEntry[]
  transactions: Transaction[]
}

type Equivalent = { code: string; precision: number; description: string; is_active: boolean }

const FIXTURES_BASE = '/admin-fixtures/v1'

const cache = new Map<string, unknown>()

function scenarioNameFromUrl(): string {
  const u = new URL(window.location.href)
  return (u.searchParams.get('scenario') || 'happy').trim() || 'happy'
}

async function loadJson<T>(relPath: string): Promise<T> {
  const key = relPath
  if (cache.has(key)) return cache.get(key) as T
  const res = await fetch(`${FIXTURES_BASE}/${relPath}`)
  if (!res.ok) throw new ApiException({ status: res.status, code: 'INTERNAL_ERROR', message: `Failed to load ${relPath}` })
  const data = (await res.json()) as T
  cache.set(key, data)
  return data
}

async function loadOptionalJson<T>(relPath: string, fallback: T): Promise<T> {
  try {
    return await loadJson<T>(relPath)
  } catch {
    return fallback
  }
}

function matchOverride(pathname: string, overrides?: Record<string, ScenarioOverride>): ScenarioOverride | null {
  if (!overrides) return null
  // Exact match first
  if (overrides[pathname]) return overrides[pathname]!
  // Wildcards like "/api/v1/admin/*"
  for (const [pattern, ov] of Object.entries(overrides)) {
    if (!pattern.endsWith('*')) continue
    const prefix = pattern.slice(0, -1)
    if (pathname.startsWith(prefix)) return ov
  }
  return null
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}

function randInt(min: number, max: number): number {
  const a = Math.min(min, max)
  const b = Math.max(min, max)
  return a + Math.floor(Math.random() * (b - a + 1))
}

function paginate<T>(items: T[], page: number, perPage: number): Paginated<T> {
  const total = items.length
  const p = Math.max(1, page)
  const pp = Math.max(1, perPage)
  const start = (p - 1) * pp
  return {
    items: items.slice(start, start + pp),
    page: p,
    per_page: pp,
    total,
  }
}

let mockConfig: Record<string, unknown> | null = null
let mockFlags: Record<string, unknown> | null = null
let mockParticipants: Participant[] | null = null
let mockEquivalents: Equivalent[] | null = null
let mockAuditLog: AuditLogEntry[] | null = null

function roleFromLocalStorage(): string {
  const v = (localStorage.getItem('admin-ui.role') || '').trim().toLowerCase()
  if (v === 'admin' || v === 'operator' || v === 'auditor') return v
  return 'admin'
}

function nowIso(): string {
  return new Date().toISOString()
}

function newId(prefix: string): string {
  return `${prefix}_${Math.random().toString(16).slice(2)}_${Date.now()}`
}

async function getParticipantsDataset(): Promise<Participant[]> {
  if (!mockParticipants) mockParticipants = await loadJson<Participant[]>('datasets/participants.json')
  return mockParticipants
}

async function getEquivalentsDataset(): Promise<Equivalent[]> {
  if (!mockEquivalents) mockEquivalents = await loadJson<Equivalent[]>('datasets/equivalents.json')
  return mockEquivalents
}

async function getAuditLogDataset(): Promise<AuditLogEntry[]> {
  if (!mockAuditLog) mockAuditLog = await loadJson<AuditLogEntry[]>('datasets/audit-log.json')
  return mockAuditLog
}

function normalizeEqCode(code: string): string {
  return (code || '').trim().toUpperCase()
}

async function getTrustlinesDataset(): Promise<Trustline[]> {
  return await loadJson<Trustline[]>('datasets/trustlines.json')
}

async function getIncidentsDataset(): Promise<Incident[]> {
  const ds = await loadJson<{ items: Incident[] }>('datasets/incidents.json')
  return ds.items
}

async function getEquivalentUsageCounts(code: string): Promise<{ trustlines: number; incidents: number }> {
  const key = normalizeEqCode(code)
  if (!key) return { trustlines: 0, incidents: 0 }

  const [trustlines, incidents] = await Promise.all([getTrustlinesDataset(), getIncidentsDataset()])
  return {
    trustlines: trustlines.filter((t) => normalizeEqCode(t.equivalent) === key).length,
    incidents: incidents.filter((i) => normalizeEqCode(i.equivalent) === key).length,
  }
}

async function appendAuditLog(entry: Omit<AuditLogEntry, 'id' | 'timestamp' | 'actor_role'> & { actor_role?: string }) {
  const all = await getAuditLogDataset()
  const full: AuditLogEntry = {
    id: newId('audit'),
    timestamp: nowIso(),
    actor_role: entry.actor_role || roleFromLocalStorage(),
    ...entry,
  }
  all.unshift(full)
}

async function getScenario(): Promise<Scenario> {
  const name = scenarioNameFromUrl()
  return await loadJson<Scenario>(`scenarios/${name}.json`)
}

async function withScenario<T>(pathname: string, handler: () => Promise<ApiEnvelope<T>>): Promise<ApiEnvelope<T>> {
  const scenario = await getScenario()
  const latency = scenario.latency_ms || { min: 30, max: 120 }
  await sleep(randInt(latency.min, latency.max))

  const ov = matchOverride(pathname, scenario.overrides)
  if (ov?.mode === 'error') {
    throw new ApiException({
      status: ov.status,
      code: ov.code,
      message: ov.message || 'Error',
      details: ov.details,
    })
  }

  if (ov?.mode === 'empty') {
    // Handler can still be called for non-list endpoints; for list endpoints we return empty structure.
    // We infer list endpoints by pathname.
    if (
      pathname === '/api/v1/admin/participants' ||
      pathname === '/api/v1/admin/trustlines' ||
      pathname === '/api/v1/admin/audit-log' ||
      pathname === '/api/v1/admin/incidents'
    ) {
      const empty = { items: [], page: 1, per_page: 20, total: 0 } as unknown as T
      return { success: true, data: empty }
    }
  }

  return await handler()
}

export const mockApi = {
  async health(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return withScenario('/api/v1/health', async () => ({ success: true, data: await loadJson('datasets/health.json') }))
  },

  async healthDb(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return withScenario('/api/v1/health/db', async () => ({ success: true, data: await loadJson('datasets/health-db.json') }))
  },

  async migrations(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return withScenario('/api/v1/admin/migrations', async () => ({ success: true, data: await loadJson('datasets/migrations.json') }))
  },

  async getConfig(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return withScenario('/api/v1/admin/config', async () => {
      const cfg = mockConfig ?? (mockConfig = await loadJson<Record<string, unknown>>('datasets/config.json'))
      return { success: true, data: cfg }
    })
  },

  async participantMetrics(
    _pid: string,
    _params?: { equivalent?: string | null; threshold?: string | number | null }
  ): Promise<ApiEnvelope<never>> {
    throw new Error('NOT_IMPLEMENTED')
  },

  async patchConfig(patch: Record<string, unknown>): Promise<ApiEnvelope<{ updated: string[] }>> {
    return withScenario('/api/v1/admin/config', async () => {
      if (!mockConfig) mockConfig = await loadJson('datasets/config.json')
      const updated = Object.keys(patch)
      mockConfig = { ...mockConfig, ...patch }
      return { success: true, data: { updated } }
    })
  },

  async getFeatureFlags(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return withScenario('/api/v1/admin/feature-flags', async () => {
      const flags = mockFlags ?? (mockFlags = await loadJson<Record<string, unknown>>('datasets/feature-flags.json'))
      return { success: true, data: flags }
    })
  },

  async patchFeatureFlags(patch: Record<string, unknown>): Promise<ApiEnvelope<{ updated: string[] }>> {
    return withScenario('/api/v1/admin/feature-flags', async () => {
      if (!mockFlags) mockFlags = await loadJson('datasets/feature-flags.json')
      const updated = Object.keys(patch)
      mockFlags = { ...mockFlags, ...patch }
      return { success: true, data: { updated } }
    })
  },

  async integrityStatus(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return withScenario('/api/v1/integrity/status', async () => ({ success: true, data: await loadJson('datasets/integrity-status.json') }))
  },

  async integrityVerify(): Promise<ApiEnvelope<{ status: 'started' }>> {
    return withScenario('/api/v1/integrity/verify', async () => ({ success: true, data: { status: 'started' } }))
  },

  async listParticipants(params: {
    page?: number
    per_page?: number
    status?: string
    type?: string
    q?: string
  }): Promise<ApiEnvelope<Paginated<Participant>>> {
    return withScenario('/api/v1/admin/participants', async () => {
      const all = await getParticipantsDataset()
      const q = (params.q || '').trim().toLowerCase()
      const status = (params.status || '').trim().toLowerCase()
      const type = (params.type || '').trim().toLowerCase()
      const filtered = all.filter((p) => {
        if (status && p.status.toLowerCase() !== status) return false
        if (type && p.type.toLowerCase() !== type) return false
        if (q && !(p.pid.toLowerCase().includes(q) || p.display_name.toLowerCase().includes(q))) return false
        return true
      })
      return {
        success: true,
        data: paginate(filtered, params.page ?? 1, params.per_page ?? 20),
      }
    })
  },

  async freezeParticipant(pid: string, reason: string): Promise<ApiEnvelope<{ pid: string; status: string }>> {
    return withScenario('/api/v1/admin/participants/freeze', async () => {
      if (!reason.trim()) return { success: false, error: { code: 'VALIDATION_ERROR', message: 'reason is required' } }
      const all = await getParticipantsDataset()
      const p = all.find((x) => x.pid === pid)
      if (!p) return { success: false, error: { code: 'NOT_FOUND', message: 'participant not found' } }
      const before = { ...p }
      p.status = 'frozen'
      await appendAuditLog({
        actor_id: 'admin-ui',
        action: 'participant.freeze',
        object_type: 'participant',
        object_id: pid,
        reason,
        before_state: before,
        after_state: { ...p },
      })
      return { success: true, data: { pid, status: p.status } }
    })
  },

  async unfreezeParticipant(pid: string, reason: string): Promise<ApiEnvelope<{ pid: string; status: string }>> {
    return withScenario('/api/v1/admin/participants/unfreeze', async () => {
      if (!reason.trim()) return { success: false, error: { code: 'VALIDATION_ERROR', message: 'reason is required' } }
      const all = await getParticipantsDataset()
      const p = all.find((x) => x.pid === pid)
      if (!p) return { success: false, error: { code: 'NOT_FOUND', message: 'participant not found' } }
      const before = { ...p }
      p.status = 'active'
      await appendAuditLog({
        actor_id: 'admin-ui',
        action: 'participant.unfreeze',
        object_type: 'participant',
        object_id: pid,
        reason,
        before_state: before,
        after_state: { ...p },
      })
      return { success: true, data: { pid, status: p.status } }
    })
  },

  async listTrustlines(params: {
    page?: number
    per_page?: number
    equivalent?: string
    creditor?: string
    debtor?: string
    status?: string
  }): Promise<ApiEnvelope<Paginated<Trustline>>> {
    return withScenario('/api/v1/admin/trustlines', async () => {
      const all = await loadJson<Trustline[]>('datasets/trustlines.json')
      const eq = (params.equivalent || '').trim().toUpperCase()
      const creditor = (params.creditor || '').trim().toLowerCase()
      const debtor = (params.debtor || '').trim().toLowerCase()
      const status = (params.status || '').trim().toLowerCase()

      const filtered = all.filter((t) => {
        if (eq && !t.equivalent.toUpperCase().includes(eq)) return false
        if (creditor && !t.from.toLowerCase().includes(creditor)) return false
        if (debtor && !t.to.toLowerCase().includes(debtor)) return false
        if (status && t.status.toLowerCase() !== status) return false
        return true
      })

      return { success: true, data: paginate(filtered, params.page ?? 1, params.per_page ?? 20) }
    })
  },

  async listAuditLog(params: {
    page?: number
    per_page?: number
    q?: string
    action?: string
    object_type?: string
    object_id?: string
  }): Promise<ApiEnvelope<Paginated<AuditLogEntry>>> {
    return withScenario('/api/v1/admin/audit-log', async () => {
      const all = await getAuditLogDataset()

      const needle = String(params.q || '').trim().toLowerCase()
      const action = String(params.action || '').trim().toLowerCase()
      const objectType = String(params.object_type || '').trim().toLowerCase()
      const objectId = String(params.object_id || '').trim().toLowerCase()

      const filtered = all.filter((e) => {
        if (action && String(e.action || '').toLowerCase() !== action) return false
        if (objectType && String(e.object_type || '').toLowerCase() !== objectType) return false
        if (objectId && String(e.object_id || '').toLowerCase() !== objectId) return false

        if (!needle) return true

        return (
          String(e.id || '').toLowerCase().includes(needle) ||
          String(e.actor_id || '').toLowerCase().includes(needle) ||
          String(e.actor_role || '').toLowerCase().includes(needle) ||
          String(e.action || '').toLowerCase().includes(needle) ||
          String(e.object_type || '').toLowerCase().includes(needle) ||
          String(e.object_id || '').toLowerCase().includes(needle) ||
          String(e.reason || '').toLowerCase().includes(needle)
        )
      })

      return { success: true, data: paginate(filtered, params.page ?? 1, params.per_page ?? 20) }
    })
  },

  async listIncidents(params: { page?: number; per_page?: number }): Promise<ApiEnvelope<Paginated<Incident>>> {
    return withScenario('/api/v1/admin/incidents', async () => {
      const ds = await loadJson<{ items: Incident[] }>('datasets/incidents.json')
      return { success: true, data: paginate(ds.items, params.page ?? 1, params.per_page ?? 20) }
    })
  },

  async listEquivalents(params: { include_inactive?: boolean }): Promise<ApiEnvelope<{ items: Equivalent[] }>> {
    return withScenario('/api/v1/admin/equivalents', async () => {
      const all = await getEquivalentsDataset()
      const items = params.include_inactive ? all : all.filter((e) => e.is_active)
      return { success: true, data: { items } }
    })
  },

  async createEquivalent(input: {
    code: string
    precision: number
    description: string
    is_active?: boolean
  }): Promise<ApiEnvelope<{ created: Equivalent }>> {
    return withScenario('/api/v1/admin/equivalents', async () => {
      const code = (input.code || '').trim().toUpperCase()
      if (!code) return { success: false, error: { code: 'VALIDATION_ERROR', message: 'code is required' } }
      const all = await getEquivalentsDataset()
      if (all.some((e) => e.code === code)) return { success: false, error: { code: 'CONFLICT', message: 'code already exists' } }

      const created: Equivalent = {
        code,
        precision: Math.max(0, Math.min(18, Math.floor(Number(input.precision ?? 2)))),
        description: String(input.description || '').trim() || code,
        is_active: Boolean(input.is_active ?? true),
      }
      all.unshift(created)
      await appendAuditLog({
        actor_id: 'admin-ui',
        action: 'equivalent.create',
        object_type: 'equivalent',
        object_id: code,
        reason: 'create',
        before_state: null,
        after_state: created,
      })
      return { success: true, data: { created } }
    })
  },

  async updateEquivalent(code: string, patch: Partial<Pick<Equivalent, 'precision' | 'description'>>): Promise<ApiEnvelope<{ updated: Equivalent }>> {
    return withScenario('/api/v1/admin/equivalents', async () => {
      const key = (code || '').trim().toUpperCase()
      const all = await getEquivalentsDataset()
      const eq = all.find((e) => e.code === key)
      if (!eq) return { success: false, error: { code: 'NOT_FOUND', message: 'equivalent not found' } }
      const before = { ...eq }
      if (patch.precision !== undefined) eq.precision = Math.max(0, Math.min(18, Math.floor(Number(patch.precision))))
      if (patch.description !== undefined) eq.description = String(patch.description || '').trim() || eq.description
      await appendAuditLog({
        actor_id: 'admin-ui',
        action: 'equivalent.update',
        object_type: 'equivalent',
        object_id: key,
        reason: 'update',
        before_state: before,
        after_state: { ...eq },
      })
      return { success: true, data: { updated: eq } }
    })
  },

  async setEquivalentActive(code: string, isActive: boolean, reason: string): Promise<ApiEnvelope<{ updated: Equivalent }>> {
    return withScenario('/api/v1/admin/equivalents/active', async () => {
      if (!String(reason || '').trim()) return { success: false, error: { code: 'VALIDATION_ERROR', message: 'reason is required' } }
      const key = (code || '').trim().toUpperCase()
      const all = await getEquivalentsDataset()
      const eq = all.find((e) => e.code === key)
      if (!eq) return { success: false, error: { code: 'NOT_FOUND', message: 'equivalent not found' } }
      const before = { ...eq }
      eq.is_active = Boolean(isActive)
      await appendAuditLog({
        actor_id: 'admin-ui',
        action: isActive ? 'equivalent.activate' : 'equivalent.deactivate',
        object_type: 'equivalent',
        object_id: key,
        reason,
        before_state: before,
        after_state: { ...eq },
      })
      return { success: true, data: { updated: eq } }
    })
  },

  async getEquivalentUsage(code: string): Promise<ApiEnvelope<{ code: string; trustlines: number; incidents: number }>> {
    return withScenario('/api/v1/admin/equivalents/usage', async () => {
      const key = normalizeEqCode(code)
      if (!key) return { success: false, error: { code: 'VALIDATION_ERROR', message: 'code is required' } }
      const counts = await getEquivalentUsageCounts(key)
      return { success: true, data: { code: key, ...counts } }
    })
  },

  async deleteEquivalent(code: string, reason: string): Promise<ApiEnvelope<{ deleted: string }>> {
    return withScenario('/api/v1/admin/equivalents/delete', async () => {
      if (roleFromLocalStorage() === 'auditor') return { success: false, error: { code: 'FORBIDDEN', message: 'read-only' } }
      if (!String(reason || '').trim()) return { success: false, error: { code: 'VALIDATION_ERROR', message: 'reason is required' } }

      const key = normalizeEqCode(code)
      if (!key) return { success: false, error: { code: 'VALIDATION_ERROR', message: 'code is required' } }

      const all = await getEquivalentsDataset()
      const idx = all.findIndex((e) => e.code === key)
      if (idx < 0) return { success: false, error: { code: 'NOT_FOUND', message: 'equivalent not found' } }

      const before = { ...all[idx] }
      if (before.is_active) return { success: false, error: { code: 'CONFLICT', message: 'Deactivate before delete' } }

      const usage = await getEquivalentUsageCounts(key)
      if (usage.trustlines > 0 || usage.incidents > 0) {
        return { success: false, error: { code: 'CONFLICT', message: 'Equivalent is in use', details: usage } }
      }

      all.splice(idx, 1)

      await appendAuditLog({
        actor_id: 'admin-ui',
        action: 'equivalent.delete',
        object_type: 'equivalent',
        object_id: key,
        reason,
        before_state: before,
        after_state: null,
      })

      return { success: true, data: { deleted: key } }
    })
  },

  async abortTx(txId: string, reason: string): Promise<ApiEnvelope<{ tx_id: string; status: 'aborted' }>> {
    return withScenario('/api/v1/admin/transactions/abort', async () => {
      if (!reason.trim()) {
        return {
          success: false,
          error: { code: 'VALIDATION_ERROR', message: 'reason is required' },
        }
      }
      await appendAuditLog({
        actor_id: 'admin-ui',
        action: 'transaction.abort',
        object_type: 'transaction',
        object_id: txId,
        reason,
        before_state: { state: 'stuck' },
        after_state: { state: 'aborted' },
      })
      return { success: true, data: { tx_id: txId, status: 'aborted' } }
    })
  },

  async graphSnapshot(): Promise<ApiEnvelope<GraphSnapshot>> {
    return withScenario('/api/v1/admin/graph/snapshot', async () => {
      const [participants, trustlines, incidents, equivalents, debts, auditLog, transactions] = await Promise.all([
        loadJson<Participant[]>('datasets/participants.json'),
        loadJson<Trustline[]>('datasets/trustlines.json'),
        loadJson<{ items: Incident[] }>('datasets/incidents.json').then((r) => r.items || []),
        loadJson<Equivalent[]>('datasets/equivalents.json'),
        loadOptionalJson<Debt[]>('datasets/debts.json', []),
        loadOptionalJson<AuditLogEntry[]>('datasets/audit-log.json', []),
        loadOptionalJson<Transaction[]>('datasets/transactions.json', []),
      ])

      return {
        success: true,
        data: {
          participants,
          trustlines,
          incidents,
          equivalents,
          debts,
          audit_log: auditLog,
          transactions,
        },
      }
    })
  },

  async clearingCycles(): Promise<ApiEnvelope<ClearingCycles | null>> {
    return withScenario('/api/v1/admin/clearing/cycles', async () => {
      const cc = await loadOptionalJson<ClearingCycles | null>('datasets/clearing-cycles.json', null)
      return { success: true, data: cc }
    })
  },

  async graphEgo(params: { pid: string; depth?: 1 | 2; equivalent?: string; status?: string[] }): Promise<ApiEnvelope<GraphSnapshot>> {
    return withScenario('/api/v1/admin/graph/ego', async () => {
      const pid = String(params.pid || '').trim()
      const depth = params.depth ?? 1
      const eq = String(params.equivalent || '').trim()
      const statuses = new Set((params.status || []).map((s) => String(s || '').trim()).filter(Boolean))

      const snapEnv = await mockApi.graphSnapshot()
      if (!snapEnv.success) return snapEnv
      const snap = snapEnv.data
      if (!pid) return { success: true, data: snap }

      const byPid = new Map(snap.participants.map((p) => [p.pid, p]))
      const seen = new Set<string>()
      const frontier: string[] = []

      if (byPid.has(pid)) {
        seen.add(pid)
        frontier.push(pid)
      }

      const tlAllowed = (t: Trustline): boolean => {
        if (eq && String(t.equivalent || '').trim() !== eq) return false
        if (statuses.size && !statuses.has(String(t.status || '').trim())) return false
        return true
      }

      for (let d = 0; d < depth; d++) {
        const next: string[] = []
        for (const cur of frontier) {
          for (const t of snap.trustlines) {
            if (!tlAllowed(t)) continue
            if (t.from === cur && t.to && !seen.has(t.to)) {
              seen.add(t.to)
              next.push(t.to)
            }
            if (t.to === cur && t.from && !seen.has(t.from)) {
              seen.add(t.from)
              next.push(t.from)
            }
          }
        }
        frontier.splice(0, frontier.length, ...next)
      }

      const pids = seen
      const participants = snap.participants.filter((p) => pids.has(p.pid))
      const trustlines = snap.trustlines.filter((t) => pids.has(t.from) && pids.has(t.to) && tlAllowed(t))
      const incidents = snap.incidents.filter((i) => pids.has(i.initiator_pid))
      const debts = snap.debts.filter(
        (d) => (pids.has(d.debtor) || pids.has(d.creditor)) && (!eq || String(d.equivalent || '').trim() === eq),
      )

      return {
        success: true,
        data: {
          participants,
          trustlines,
          incidents,
          equivalents: snap.equivalents,
          debts,
          audit_log: snap.audit_log,
          transactions: snap.transactions,
        },
      }
    })
  },
}
