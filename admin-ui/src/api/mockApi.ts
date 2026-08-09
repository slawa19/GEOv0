import { ApiException, type ApiEnvelope } from './envelope'
import {
  AdminAbortTxResponseSchema,
  AdminAuditLogEntrySchema,
  AdminAuditLogSchema,
  AdminConfigPatchResponseSchema,
  AdminConfigPatchSchema,
  AdminConfigSchema,
  AdminEquivalentDeleteResponseSchema,
  AdminEquivalentCodeSchema,
  AdminEquivalentMutationResponseSchema,
  AdminEquivalentPrecisionSchema,
  AdminEquivalentUsageResponseSchema,
  AdminFeatureFlagsSchema,
  AdminParticipantActionResponseSchema,
  IntegrityRepairCapDebtsResponseSchema,
  IntegrityRepairNetMutualDebtsResponseSchema,
  IntegrityStatusResponseSchema,
  IntegrityVerifyResponseSchema,
  decodeAdminResponse,
  type AdminAbortTxResponse,
  type AdminConfigPatchResponse,
  type AdminEquivalentDeleteResponse,
  type AdminEquivalentUsageResponse,
  type AdminFeatureFlags,
  type AdminParticipantActionResponse,
  type IntegrityRepairCapDebtsResponse,
  type IntegrityRepairNetMutualDebtsResponse,
  type IntegrityStatusResponse,
  type IntegrityVerifyResponse,
} from './adminContracts'
import { TOAST_DEDUPE_MS } from '../constants/timing'
import { absDecimalString, addDecimalStrings, compareDecimalStrings, isRatioBelowThreshold } from '../utils/decimal'
import { t } from '../i18n'
import type {
  AuditLogEntry,
  BalanceRow,
  ClearingCycles,
  CounterpartySplitRow,
  Debt,
  Equivalent,
  GraphSnapshot,
  Incident,
  LiquiditySummary,
  Paginated,
  Participant,
  ParticipantMetrics,
  ParticipantsStats,
  Trustline,
  Transaction,
} from '../types/domain'

type ScenarioOverride =
  | { mode: 'empty' }
  | { mode: 'error'; status: number; code: string; message?: string; details?: unknown }

type Scenario = {
  name: string
  description?: string
  latency_ms?: { min: number; max: number }
  overrides?: Record<string, ScenarioOverride>
}

const FIXTURES_BASE = '/admin-fixtures/v1'
const MOCK_EQUIVALENT_TIMESTAMP = '2025-01-01T00:00:00.000Z'

function toMockEquivalentWire(value: Equivalent) {
  return {
    code: value.code,
    symbol: null,
    description: value.description,
    precision: value.precision,
    metadata: null,
    is_active: value.is_active,
    created_at: MOCK_EQUIVALENT_TIMESTAMP,
    updated_at: MOCK_EQUIVALENT_TIMESTAMP,
  }
}

const cache = new Map<string, unknown>()

let lastToastAt = 0
let lastToastMsg = ''

async function notifyLoadError(message: string) {
  // Best-effort toast: do not crash the app if UI layer isn't available.
  const now = Date.now()
  if (message === lastToastMsg && now - lastToastAt < TOAST_DEDUPE_MS) return
  lastToastAt = now
  lastToastMsg = message

  try {
    const mod = await import('element-plus')
    const ElMessage = (mod as unknown as { ElMessage?: { error?: (msg: string) => void } }).ElMessage
    if (typeof ElMessage?.error === 'function') {
      ElMessage.error(message)
      return
    }
  } catch {
    // ignore
  }

  // Fallback: console (e.g., in tests or stripped UI runtimes).
  // eslint-disable-next-line no-console
  console.error(message)
}

function scenarioNameFromUrl(): string {
  const u = new URL(window.location.href)
  return (u.searchParams.get('scenario') || 'happy').trim() || 'happy'
}

async function loadJson<T>(relPath: string, opts?: { silent?: boolean }): Promise<T> {
  const key = relPath
  if (cache.has(key)) return cache.get(key) as T

  const url = `${FIXTURES_BASE}/${relPath}`
  const attempts = opts?.silent ? 1 : 3

  let lastErr: unknown = null
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      const res = await fetch(url)
      if (!res.ok) {
        throw new ApiException({
          status: res.status,
          code: 'INTERNAL_ERROR',
          message: t('fixtures.loadFailedPath', { path: relPath }),
          details: { url, status: res.status, statusText: res.statusText },
        })
      }

      const data = (await res.json()) as T
      cache.set(key, data)
      return data
    } catch (e) {
      lastErr = e

      // Offline fallback: if we have cached data, prefer it.
      if (cache.has(key)) return cache.get(key) as T

      if (attempt < attempts) {
        const base = 120
        const backoff = base * Math.pow(3, attempt - 1)
        const jitter = Math.floor(Math.random() * 40)
        await sleep(backoff + jitter)
        continue
      }
    }
  }

  if (!opts?.silent) {
    await notifyLoadError(t('fixtures.loadFailedMany', { path: relPath }))
  }

  if (lastErr instanceof ApiException) throw lastErr
  throw new ApiException({
    status: 0,
    code: 'INTERNAL_ERROR',
    message: t('fixtures.loadFailedPath', { path: relPath }),
    details: lastErr,
  })
}

async function loadOptionalJson<T>(relPath: string, fallback: T): Promise<T> {
  try {
    return await loadJson<T>(relPath, { silent: true })
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
let mockFlags: AdminFeatureFlags | null = null
let mockParticipants: Participant[] | null = null
let mockEquivalents: Equivalent[] | null = null
let mockAuditLog: AuditLogEntry[] | null = null
let mockIncidents: Incident[] | null | undefined

function roleFromLocalStorage(): string {
  const v = (localStorage.getItem('admin-ui.role') || '').trim().toLowerCase()
  if (v === 'admin' || v === 'operator' || v === 'auditor') return v
  return 'admin'
}

function nowIso(): string {
  return new Date().toISOString()
}

function newUuid(): string {
  if (typeof crypto?.randomUUID === 'function') return crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const value = Math.floor(Math.random() * 16)
    return (char === 'x' ? value : (value & 0x3) | 0x8).toString(16)
  })
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
  if (mockAuditLog) return mockAuditLog
  const decoded = decodeAdminResponse(
    AdminAuditLogSchema,
    await loadJson<unknown>('datasets/audit-log.json'),
    'mock GET /api/v1/admin/audit-log',
  )
  mockAuditLog = decoded
  return decoded
}

async function getIncidentsDataset(): Promise<Incident[] | null> {
  if (mockIncidents === undefined) {
    const dataset = await loadOptionalJson<{ items: Incident[] } | null>('datasets/incidents.json', null)
    mockIncidents = dataset?.items ?? null
  }
  return mockIncidents
}

function normalizeEqCode(code: string): string {
  return (code || '').trim().toUpperCase()
}

async function getTrustlinesDataset(): Promise<Trustline[]> {
  return await loadJson<Trustline[]>('datasets/trustlines.json')
}

function decimalToAtoms(amount: string, precision: number): bigint {
  const m = String(amount || '').trim().match(/^(-)?(\d+)(?:\.(\d+))?$/)
  if (!m) return 0n
  const neg = Boolean(m[1])
  const intPart = m[2] || '0'
  const fracPart = m[3] || ''
  const frac = (fracPart + '0'.repeat(precision)).slice(0, precision)
  const atoms = BigInt((intPart + frac).replace(/^0+(?=\d)/, '') || '0')
  return neg ? -atoms : atoms
}

function atomsToDecimal(atoms: bigint, precision: number): string {
  const neg = atoms < 0n
  const abs = neg ? -atoms : atoms
  const s = abs.toString()
  if (precision <= 0) return (neg ? '-' : '') + s
  const pad = precision + 1
  const padded = s.length >= pad ? s : '0'.repeat(pad - s.length) + s
  const head = padded.slice(0, padded.length - precision)
  const frac = padded.slice(padded.length - precision)
  return (neg ? '-' : '') + head + '.' + frac
}

function safeIsoToMs(ts: string | undefined | null): number | null {
  const t = String(ts || '').trim()
  if (!t) return null
  const ms = Date.parse(t)
  return Number.isFinite(ms) ? ms : null
}

function computeShareRows(
  amountsByPid: Map<string, bigint>,
  total: bigint,
  prec: number,
  pidToName: Map<string, string>,
): CounterpartySplitRow[] {
  const denom = total > 0n ? Number(total) : 0
  const rows: CounterpartySplitRow[] = []
  for (const [pid, amt] of amountsByPid.entries()) {
    rows.push({
      pid,
      display_name: pidToName.get(pid) || pid,
      amount: atomsToDecimal(amt, prec),
      share: denom > 0 ? Number(amt) / denom : 0,
    })
  }
  rows.sort((a, b) => Number(decimalToAtoms(b.amount, prec) - decimalToAtoms(a.amount, prec)))
  return rows
}

function topShares(shares: number[]): { top1: number; top5: number; hhi: number } {
  const sorted = [...shares].sort((a, b) => b - a)
  const top1 = sorted[0] ?? 0
  const top5 = sorted.slice(0, 5).reduce((acc, x) => acc + x, 0)
  const hhi = sorted.reduce((acc, x) => acc + x * x, 0)
  return { top1, top5, hhi }
}

async function getEquivalentUsageCounts(
  code: string,
): Promise<{ trustlines: number; debts: number; integrity_checkpoints: number }> {
  const key = normalizeEqCode(code)
  if (!key) return { trustlines: 0, debts: 0, integrity_checkpoints: 0 }

  const [trustlines, debts] = await Promise.all([
    getTrustlinesDataset(),
    loadJson<Debt[]>('datasets/debts.json'),
  ])
  return {
    trustlines: trustlines.filter((t) => normalizeEqCode(t.equivalent) === key).length,
    debts: debts.filter((d) => normalizeEqCode(d.equivalent) === key).length,
    integrity_checkpoints: 0,
  }
}

async function getIntegrityStatusDataset(): Promise<IntegrityStatusResponse> {
  return decodeAdminResponse(
    IntegrityStatusResponseSchema,
    await loadJson<unknown>('datasets/integrity-status.json'),
    'mock GET /api/v1/integrity/status',
  )
}

async function appendAuditLog(
  entry: Omit<AuditLogEntry, 'id' | 'timestamp' | 'actor_id' | 'actor_role'> & { actor_role?: string },
) {
  const all = await getAuditLogDataset()
  const full = decodeAdminResponse(
    AdminAuditLogEntrySchema,
    {
      id: newUuid(),
      timestamp: nowIso(),
      actor_id: null,
      actor_role: entry.actor_role || roleFromLocalStorage(),
      ...entry,
    },
    'mock audit mutation',
  )
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
      const cfg =
        mockConfig ??
        (mockConfig = decodeAdminResponse(
          AdminConfigSchema,
          await loadJson<unknown>('datasets/config.json'),
          'mock GET /api/v1/admin/config',
        ))
      return { success: true, data: cfg }
    })
  },

  async participantMetrics(
    pid: string,
    params?: { equivalent?: string | null; threshold?: string | number | null },
  ): Promise<ApiEnvelope<ParticipantMetrics>> {
    return withScenario('/api/v1/admin/participants/metrics', async () => {
      const pidKey = String(pid || '').trim()
      if (!pidKey) return { success: false, error: { code: 'VALIDATION_ERROR', message: 'pid is required' } }

      const eqRaw = String(params?.equivalent || '').trim().toUpperCase()
      const eqCode = eqRaw && eqRaw !== 'ALL' ? eqRaw : null
      const thrStr = String(params?.threshold ?? '0.10').trim() || '0.10'

      const [participants, equivalents, trustlines, debts, incidents, transactions] = await Promise.all([
        getParticipantsDataset(),
        getEquivalentsDataset(),
        getTrustlinesDataset(),
        loadOptionalJson<Debt[]>('datasets/debts.json', []),
        loadOptionalJson<Incident[]>('datasets/incidents.json', []),
        loadOptionalJson<Transaction[]>('datasets/transactions.json', []),
      ])

      const pidToName = new Map(participants.map((p) => [p.pid, String(p.display_name || '').trim() || p.pid]))
      const precisionByEq = new Map(
        (equivalents || [])
          .map((e) => [String(e.code || '').trim().toUpperCase(), Number(e.precision)] as const)
          .filter((x) => x[0] && Number.isFinite(x[1])),
      )

      const eqs = eqCode
        ? [eqCode]
        : Array.from(
            new Set([
              ...(equivalents || []).map((e) => String(e.code || '').trim().toUpperCase()).filter(Boolean),
              ...(trustlines || []).map((t) => String(t.equivalent || '').trim().toUpperCase()).filter(Boolean),
              ...(debts || []).map((d) => String(d.equivalent || '').trim().toUpperCase()).filter(Boolean),
            ]),
          ).sort()

      const balance_rows: BalanceRow[] = []

      for (const code of eqs) {
        const prec = precisionByEq.get(code) ?? 2

        let outLimit = 0n
        let outUsed = 0n
        let inLimit = 0n
        let inUsed = 0n

        for (const t of trustlines || []) {
          if (String(t.equivalent || '').trim().toUpperCase() !== code) continue
          const lim = decimalToAtoms(t.limit, prec)
          const used = decimalToAtoms(t.used, prec)
          if (t.from === pidKey) {
            outLimit += lim
            outUsed += used
          } else if (t.to === pidKey) {
            inLimit += lim
            inUsed += used
          }
        }

        let debtAtoms = 0n
        let creditAtoms = 0n
        for (const d of debts || []) {
          if (String(d.equivalent || '').trim().toUpperCase() !== code) continue
          const amt = decimalToAtoms(d.amount, prec)
          if (d.debtor === pidKey) debtAtoms += amt
          if (d.creditor === pidKey) creditAtoms += amt
        }

        const netAtoms = creditAtoms - debtAtoms
        balance_rows.push({
          equivalent: code,
          outgoing_limit: atomsToDecimal(outLimit, prec),
          outgoing_used: atomsToDecimal(outUsed, prec),
          incoming_limit: atomsToDecimal(inLimit, prec),
          incoming_used: atomsToDecimal(inUsed, prec),
          total_debt: atomsToDecimal(debtAtoms, prec),
          total_credit: atomsToDecimal(creditAtoms, prec),
          net: atomsToDecimal(netAtoms, prec),
        })
      }

      let counterparty: ParticipantMetrics['counterparty'] = null
      let concentration: ParticipantMetrics['concentration'] = null
      let distribution: ParticipantMetrics['distribution'] = null
      let rank: ParticipantMetrics['rank'] = null
      let capacity: ParticipantMetrics['capacity'] = null

      if (eqCode) {
        const prec = precisionByEq.get(eqCode) ?? 2

        const creditorsAtoms = new Map<string, bigint>()
        const debtorsAtoms = new Map<string, bigint>()
        let totalDebtAtoms = 0n
        let totalCreditAtoms = 0n

        for (const d of debts || []) {
          if (String(d.equivalent || '').trim().toUpperCase() !== eqCode) continue
          const amt = decimalToAtoms(d.amount, prec)
          if (d.debtor === pidKey) {
            totalDebtAtoms += amt
            const other = String(d.creditor || '').trim()
            creditorsAtoms.set(other, (creditorsAtoms.get(other) || 0n) + amt)
          }
          if (d.creditor === pidKey) {
            totalCreditAtoms += amt
            const other = String(d.debtor || '').trim()
            debtorsAtoms.set(other, (debtorsAtoms.get(other) || 0n) + amt)
          }
        }

        const creditors = computeShareRows(creditorsAtoms, totalDebtAtoms, prec, pidToName)
        const debtors = computeShareRows(debtorsAtoms, totalCreditAtoms, prec, pidToName)
        counterparty = {
          eq: eqCode,
          totalDebt: atomsToDecimal(totalDebtAtoms, prec),
          totalCredit: atomsToDecimal(totalCreditAtoms, prec),
          creditors,
          debtors,
        }

        concentration = {
          eq: eqCode,
          outgoing: topShares(creditors.map((r) => r.share)),
          incoming: topShares(debtors.map((r) => r.share)),
        }

        // Distribution + rank use net = credit - debt over all participants.
        const netAtomsByPid = new Map<string, bigint>()
        for (const p of participants || []) netAtomsByPid.set(p.pid, 0n)
        for (const d of debts || []) {
          if (String(d.equivalent || '').trim().toUpperCase() !== eqCode) continue
          const amt = decimalToAtoms(d.amount, prec)
          netAtomsByPid.set(d.debtor, (netAtomsByPid.get(d.debtor) || 0n) - amt)
          netAtomsByPid.set(d.creditor, (netAtomsByPid.get(d.creditor) || 0n) + amt)
        }

        const sortedPids = Array.from(netAtomsByPid.entries())
          .sort((a, b) => (a[1] === b[1] ? a[0].localeCompare(b[0]) : b[1] > a[1] ? 1 : -1))
          .map(([p]) => p)

        const atomsVals = Array.from(netAtomsByPid.values())
        const minAtoms = atomsVals.reduce((m, v) => (v < m ? v : m), 0n)
        const maxAtoms = atomsVals.reduce((m, v) => (v > m ? v : m), 0n)

        const binsCount = 20
        const range = maxAtoms - minAtoms
        const bins: Array<{ from_atoms: string; to_atoms: string; count: number }> = []
        if (range === 0n) {
          bins.push({ from_atoms: String(minAtoms), to_atoms: String(maxAtoms), count: atomsVals.length })
        } else {
          const step = range / BigInt(binsCount)
          const safeStep = step > 0n ? step : 1n
          for (let i = 0; i < binsCount; i++) {
            const from = minAtoms + BigInt(i) * safeStep
            const to = i === binsCount - 1 ? maxAtoms : from + safeStep
            bins.push({ from_atoms: String(from), to_atoms: String(to), count: 0 })
          }
          for (const v of atomsVals) {
            const idx = Number((v - minAtoms) / safeStep)
            const clamped = Math.max(0, Math.min(binsCount - 1, idx))
            bins[clamped]!.count += 1
          }
        }

        distribution = {
          eq: eqCode,
          min_atoms: String(minAtoms),
          max_atoms: String(maxAtoms),
          bins,
        }

        const idx = sortedPids.indexOf(pidKey)
        if (idx >= 0) {
          const rnk = idx + 1
          const n = sortedPids.length
          const percentile = n <= 1 ? 1 : (n - rnk) / (n - 1)
          rank = {
            eq: eqCode,
            rank: rnk,
            n,
            percentile,
            net: atomsToDecimal(netAtomsByPid.get(pidKey) || 0n, prec),
          }
        }

        // Capacity: total in/out usage + bottlenecks (available/limit < threshold).
        let outLimit = 0n
        let outUsed = 0n
        let inLimit = 0n
        let inUsed = 0n
        const bottlenecks: Array<{ dir: 'out' | 'in'; other: string; trustline: Trustline }> = []

        for (const t of trustlines || []) {
          if (String(t.equivalent || '').trim().toUpperCase() !== eqCode) continue
          const lim = decimalToAtoms(t.limit, prec)
          const used = decimalToAtoms(t.used, prec)
          if (t.from === pidKey) {
            outLimit += lim
            outUsed += used
            if (t.status === 'active' && isRatioBelowThreshold({ numerator: t.available, denominator: t.limit, threshold: thrStr })) {
              bottlenecks.push({ dir: 'out', other: t.to, trustline: t })
            }
          } else if (t.to === pidKey) {
            inLimit += lim
            inUsed += used
            if (t.status === 'active' && isRatioBelowThreshold({ numerator: t.available, denominator: t.limit, threshold: thrStr })) {
              bottlenecks.push({ dir: 'in', other: t.from, trustline: t })
            }
          }
        }

        const outPct = outLimit > 0n ? Number(outUsed) / Number(outLimit) : 0
        const inPct = inLimit > 0n ? Number(inUsed) / Number(inLimit) : 0

        capacity = {
          eq: eqCode,
          out: { limit: atomsToDecimal(outLimit, prec), used: atomsToDecimal(outUsed, prec), pct: outPct },
          inc: { limit: atomsToDecimal(inLimit, prec), used: atomsToDecimal(inUsed, prec), pct: inPct },
          bottlenecks,
        }
      }

      const nowMs = Date.now()
      const windows = [7, 30, 90]
      const dayMs = 24 * 3600 * 1000

      const trustline_created: Record<number, number> = {}
      const trustline_closed: Record<number, number> = {}
      const incident_count: Record<number, number> = {}
      const participant_ops: Record<number, number> = {}
      const payment_committed: Record<number, number> = {}
      const clearing_committed: Record<number, number> = {}
      for (const w of windows) {
        trustline_created[w] = 0
        trustline_closed[w] = 0
        incident_count[w] = 0
        participant_ops[w] = 0
        payment_committed[w] = 0
        clearing_committed[w] = 0
      }

      const getTxEq = (tx: Transaction): string => {
        const payload = (tx as unknown as { payload?: unknown }).payload
        const v = payload && typeof payload === 'object' ? (payload as Record<string, unknown>).equivalent : undefined
        return typeof v === 'string' ? v.trim().toUpperCase() : ''
      }

      for (const t of trustlines || []) {
        if (eqCode && String(t.equivalent || '').trim().toUpperCase() !== eqCode) continue
        if (t.from !== pidKey && t.to !== pidKey) continue
        const ms = safeIsoToMs(t.created_at)
        if (!ms) continue
        for (const w of windows) {
          if (ms >= nowMs - w * dayMs) {
            trustline_created[w] = (trustline_created[w] ?? 0) + 1
            if (String(t.status || '').trim().toLowerCase() === 'closed') {
              trustline_closed[w] = (trustline_closed[w] ?? 0) + 1
            }
          }
        }
      }

      for (const inc of incidents || []) {
        if (String(inc.initiator_pid || '').trim() !== pidKey) continue
        if (eqCode && String(inc.equivalent || '').trim().toUpperCase() !== eqCode) continue
        const createdMs =
          safeIsoToMs(inc.created_at) ??
          (Number.isFinite(inc.age_seconds) ? nowMs - Math.max(0, Number(inc.age_seconds)) * 1000 : null)
        if (!createdMs) continue
        for (const w of windows) {
          if (createdMs >= nowMs - w * dayMs) incident_count[w] = (incident_count[w] ?? 0) + 1
        }
      }

      for (const tx of transactions || []) {
        if (String(tx.initiator_pid || '').trim() !== pidKey) continue
        if (eqCode && getTxEq(tx) !== eqCode) continue
        const createdMs = safeIsoToMs(tx.created_at)
        if (!createdMs) continue

        const type = String(tx.type || '').trim().toUpperCase()
        const state = String(tx.state || '').trim().toUpperCase()
        const isCommitted = state === 'COMMITTED'

        for (const w of windows) {
          if (createdMs < nowMs - w * dayMs) continue
          participant_ops[w] = (participant_ops[w] ?? 0) + 1
          if (isCommitted && type === 'PAYMENT') payment_committed[w] = (payment_committed[w] ?? 0) + 1
          if (isCommitted && type === 'CLEARING') clearing_committed[w] = (clearing_committed[w] ?? 0) + 1
        }
      }

      const activity: ParticipantMetrics['activity'] = {
        windows,
        trustline_created,
        trustline_closed,
        incident_count,
        participant_ops,
        payment_committed,
        clearing_committed,
        has_transactions: (transactions || []).some(
          (tx) => String(tx.initiator_pid || '').trim() === pidKey && (!eqCode || getTxEq(tx) === eqCode),
        ),
      }

      return {
        success: true,
        data: {
          pid: pidKey,
          equivalent: eqCode,
          balance_rows,
          counterparty,
          concentration,
          distribution,
          rank,
          capacity,
          activity,
        },
      }
    })
  },

  async patchConfig(patch: Record<string, unknown>): Promise<ApiEnvelope<AdminConfigPatchResponse>> {
    return withScenario('/api/v1/admin/config', async () => {
      const validatedPatch = AdminConfigPatchSchema.safeParse(patch)
      if (!validatedPatch.success) {
        return {
          success: false,
          error: {
            code: 'VALIDATION_ERROR',
            message: 'config patch contains unknown keys or invalid values',
            details: validatedPatch.error.issues,
          },
        }
      }
      if (!mockConfig) {
        mockConfig = decodeAdminResponse(
          AdminConfigSchema,
          await loadJson<unknown>('datasets/config.json'),
          'mock GET /api/v1/admin/config',
        )
      }
      const updated = Object.keys(validatedPatch.data)
      const beforeState = Object.fromEntries(updated.map((key) => [key, mockConfig![key]]))
      mockConfig = decodeAdminResponse(
        AdminConfigSchema,
        { ...mockConfig, ...validatedPatch.data },
        'mock PATCH /api/v1/admin/config',
      )
      const afterState = Object.fromEntries(updated.map((key) => [key, mockConfig![key]]))
      await appendAuditLog({
        action: 'admin.config.patch',
        object_type: 'config',
        object_id: null,
        reason: null,
        before_state: beforeState,
        after_state: afterState,
      })
      return {
        success: true,
        data: decodeAdminResponse(
          AdminConfigPatchResponseSchema,
          { updated },
          'mock PATCH /api/v1/admin/config',
        ),
      }
    })
  },

  async getFeatureFlags(): Promise<ApiEnvelope<AdminFeatureFlags>> {
    return withScenario('/api/v1/admin/feature-flags', async () => {
      const flags =
        mockFlags ??
        (mockFlags = decodeAdminResponse(
          AdminFeatureFlagsSchema,
          await loadJson<unknown>('datasets/feature-flags.json'),
          'mock GET /api/v1/admin/feature-flags',
        ))
      return { success: true, data: flags }
    })
  },

  async patchFeatureFlags(patch: Record<string, unknown>): Promise<ApiEnvelope<AdminFeatureFlags>> {
    return withScenario('/api/v1/admin/feature-flags', async () => {
      if (!mockFlags) {
        mockFlags = decodeAdminResponse(
          AdminFeatureFlagsSchema,
          await loadJson<unknown>('datasets/feature-flags.json'),
          'mock GET /api/v1/admin/feature-flags',
        )
      }

      const next = { ...mockFlags }
      const before = { ...mockFlags }
      for (const key of ['multipath_enabled', 'full_multipath_enabled', 'clearing_enabled'] as const) {
        if (typeof patch[key] === 'boolean') next[key] = patch[key]
      }
      mockFlags = decodeAdminResponse(
        AdminFeatureFlagsSchema,
        next,
        'mock PATCH /api/v1/admin/feature-flags',
      )
      await appendAuditLog({
        action: 'admin.feature_flags.patch',
        object_type: 'feature_flags',
        object_id: null,
        reason: null,
        before_state: before,
        after_state: { ...mockFlags },
      })
      return { success: true, data: mockFlags }
    })
  },

  async integrityStatus(): Promise<ApiEnvelope<IntegrityStatusResponse>> {
    return withScenario('/api/v1/integrity/status', async () => ({
      success: true,
      data: await getIntegrityStatusDataset(),
    }))
  },

  async integrityVerify(): Promise<ApiEnvelope<IntegrityVerifyResponse>> {
    return withScenario('/api/v1/integrity/verify', async () => {
      const status = await getIntegrityStatusDataset()
      return {
        success: true,
        data: decodeAdminResponse(
          IntegrityVerifyResponseSchema,
          {
            status: status.status,
            checked_at: new Date().toISOString(),
            equivalents: status.equivalents,
            alerts: status.alerts,
          },
          'mock POST /api/v1/integrity/verify',
        ),
      }
    })
  },

  async integrityRepairNetMutualDebts(): Promise<ApiEnvelope<IntegrityRepairNetMutualDebtsResponse>> {
    return withScenario('/api/v1/integrity/repair/net-mutual-debts', async () => ({
      success: true,
      data: decodeAdminResponse(
        IntegrityRepairNetMutualDebtsResponseSchema,
        { ok: true, action: 'net-mutual-debts', netted_pairs: 0, updated: 0, deleted: 0 },
        'mock POST /api/v1/integrity/repair/net-mutual-debts',
      ),
    }))
  },

  async integrityRepairCapDebtsToTrustLimits(): Promise<ApiEnvelope<IntegrityRepairCapDebtsResponse>> {
    return withScenario('/api/v1/integrity/repair/cap-debts-to-trust-limits', async () => ({
      success: true,
      data: decodeAdminResponse(
        IntegrityRepairCapDebtsResponseSchema,
        { ok: true, action: 'cap-debts-to-trust-limits', scanned: 0, updated: 0, deleted: 0 },
        'mock POST /api/v1/integrity/repair/cap-debts-to-trust-limits',
      ),
    }))
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

  async participantsStats(): Promise<ApiEnvelope<ParticipantsStats>> {
    return withScenario('/api/v1/admin/participants/stats', async () => {
      const all = await getParticipantsDataset()
      const byStatus: Record<string, number> = {}
      const byType: Record<string, number> = {}
      for (const p of all) {
        const st = String(p.status || '').trim().toLowerCase() || 'unknown'
        const ty = String(p.type || '').trim().toLowerCase() || 'unknown'
        byStatus[st] = (byStatus[st] || 0) + 1
        byType[ty] = (byType[ty] || 0) + 1
      }
      return { success: true, data: { participants_by_status: byStatus, participants_by_type: byType, total_participants: all.length } }
    })
  },

  async trustlineBottlenecks(params: {
    threshold?: string
    limit?: number
    equivalent?: string
  }): Promise<ApiEnvelope<{ threshold: number; items: Trustline[] }>> {
    return withScenario('/api/v1/admin/trustlines/bottlenecks', async () => {
      const thr = String(params.threshold ?? '').trim() || '0.10'
      const limit = Math.max(1, Math.min(50, Number(params.limit ?? 10) || 10))
      const eq = String(params.equivalent || '').trim().toUpperCase()

      const all = await loadJson<Trustline[]>('datasets/trustlines.json')
      const candidates = all.filter((t) => {
        if (String(t.status || '').trim().toLowerCase() !== 'active') return false
        if (eq && String(t.equivalent || '').trim().toUpperCase() !== eq) return false
        return isRatioBelowThreshold({ numerator: t.available, denominator: t.limit, threshold: thr })
      })

      candidates.sort((a, b) => {
        const c = compareDecimalStrings(String(a.available || '0'), String(b.available || '0'))
        if (c !== 0) return c
        return String(a.created_at || '').localeCompare(String(b.created_at || ''))
      })

      return { success: true, data: { threshold: Number(thr), items: candidates.slice(0, limit) } }
    })
  },

  async liquiditySummary(params: { equivalent?: string; threshold?: string; limit?: number }): Promise<ApiEnvelope<LiquiditySummary>> {
    return withScenario('/api/v1/admin/liquidity/summary', async () => {
      const threshold = String(params.threshold ?? '').trim() || '0.10'
      const limit = Math.max(1, Math.min(50, Number(params.limit ?? 10) || 10))
      const eqRaw = String(params.equivalent ?? '').trim().toUpperCase()
      const eq = eqRaw && eqRaw !== 'ALL' ? eqRaw : ''

      const [participants, trustlines, debts, incidents] = await Promise.all([
        getParticipantsDataset(),
        loadJson<Trustline[]>('datasets/trustlines.json'),
        loadOptionalJson<Debt[]>('datasets/debts.json', []),
        loadJson<{ items: Incident[] }>('datasets/incidents.json').then((r) => r.items || []),
      ])

      const byPid = new Map(participants.map((p) => [p.pid, p]))

      const activeTrustlines = trustlines.filter((t) => {
        if (String(t.status || '').trim().toLowerCase() !== 'active') return false
        if (eq && String(t.equivalent || '').trim().toUpperCase() !== eq) return false
        return true
      })

      const bottleneckEdges = activeTrustlines.filter((t) =>
        isRatioBelowThreshold({ numerator: t.available, denominator: t.limit, threshold }),
      )

      const incidentsOverSla = incidents.filter((i) => {
        if (eq && String(i.equivalent || '').trim().toUpperCase() !== eq) return false
        return i.age_seconds > i.sla_seconds
      })

      let totalLimit = '0'
      let totalUsed = '0'
      let totalAvailable = '0'
      for (const t of activeTrustlines) {
        totalLimit = addDecimalStrings(totalLimit, String(t.limit || '0'))
        totalUsed = addDecimalStrings(totalUsed, String(t.used || '0'))
        totalAvailable = addDecimalStrings(totalAvailable, String(t.available || '0'))
      }

      const netByPid = new Map<string, string>()
      for (const d of debts) {
        if (eq && String(d.equivalent || '').trim().toUpperCase() !== eq) continue
        const debtor = String(d.debtor || '').trim()
        const creditor = String(d.creditor || '').trim()
        const amt = String(d.amount || '0')
        if (debtor) netByPid.set(debtor, addDecimalStrings(netByPid.get(debtor) || '0', '-' + amt))
        if (creditor) netByPid.set(creditor, addDecimalStrings(netByPid.get(creditor) || '0', amt))
      }

      const netRows = [...netByPid.entries()].map(([pid, net]) => ({
        pid,
        display_name: byPid.get(pid)?.display_name || '',
        net,
      }))

      const topCreditors = netRows.filter((r) => compareDecimalStrings(r.net, '0') > 0)
      topCreditors.sort((a, b) => compareDecimalStrings(b.net, a.net))

      const topDebtors = netRows.filter((r) => compareDecimalStrings(r.net, '0') < 0)
      topDebtors.sort((a, b) => compareDecimalStrings(a.net, b.net))

      const topByAbsNet = [...netRows]
      topByAbsNet.sort((a, b) => compareDecimalStrings(absDecimalString(b.net), absDecimalString(a.net)))

      const topBottleneckEdges = [...bottleneckEdges]
      topBottleneckEdges.sort((a, b) => {
        const c = compareDecimalStrings(String(a.available || '0'), String(b.available || '0'))
        if (c !== 0) return c
        return String(a.created_at || '').localeCompare(String(b.created_at || ''))
      })

      return {
        success: true,
        data: {
          equivalent: eq || null,
          threshold: Number(threshold),
          updated_at: new Date().toISOString(),
          active_trustlines: activeTrustlines.length,
          bottlenecks: bottleneckEdges.length,
          incidents_over_sla: incidentsOverSla.length,
          total_limit: totalLimit,
          total_used: totalUsed,
          total_available: totalAvailable,
          top_creditors: topCreditors.slice(0, limit),
          top_debtors: topDebtors.slice(0, limit),
          top_by_abs_net: topByAbsNet.slice(0, limit),
          top_bottleneck_edges: topBottleneckEdges.slice(0, limit),
        },
      }
    })
  },

  async freezeParticipant(pid: string, reason: string): Promise<ApiEnvelope<AdminParticipantActionResponse>> {
    return withScenario('/api/v1/admin/participants/freeze', async () => {
      if (!reason.trim()) return { success: false, error: { code: 'VALIDATION_ERROR', message: 'reason is required' } }
      const all = await getParticipantsDataset()
      const p = all.find((x) => x.pid === pid)
      if (!p) return { success: false, error: { code: 'NOT_FOUND', message: 'participant not found' } }
      const before = { ...p }
      const response = decodeAdminResponse(
        AdminParticipantActionResponseSchema,
        { pid, status: 'suspended' },
        'mock POST /api/v1/admin/participants/{pid}/freeze',
      )
      p.status = response.status
      await appendAuditLog({
        action: 'admin.participants.freeze',
        object_type: 'participant',
        object_id: pid,
        reason,
        before_state: before,
        after_state: { ...p },
      })
      return { success: true, data: response }
    })
  },

  async unfreezeParticipant(pid: string, reason: string): Promise<ApiEnvelope<AdminParticipantActionResponse>> {
    return withScenario('/api/v1/admin/participants/unfreeze', async () => {
      if (!reason.trim()) return { success: false, error: { code: 'VALIDATION_ERROR', message: 'reason is required' } }
      const all = await getParticipantsDataset()
      const p = all.find((x) => x.pid === pid)
      if (!p) return { success: false, error: { code: 'NOT_FOUND', message: 'participant not found' } }
      const before = { ...p }
      const response = decodeAdminResponse(
        AdminParticipantActionResponseSchema,
        { pid, status: 'active' },
        'mock POST /api/v1/admin/participants/{pid}/unfreeze',
      )
      p.status = response.status
      await appendAuditLog({
        action: 'admin.participants.unfreeze',
        object_type: 'participant',
        object_id: pid,
        reason,
        before_state: before,
        after_state: { ...p },
      })
      return { success: true, data: response }
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
      const incidents = (await getIncidentsDataset()) ?? []
      return { success: true, data: paginate(incidents, params.page ?? 1, params.per_page ?? 20) }
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
      const code = input.code
      if (!AdminEquivalentCodeSchema.safeParse(code).success) {
        return { success: false, error: { code: 'VALIDATION_ERROR', message: 'invalid equivalent code' } }
      }
      if (!AdminEquivalentPrecisionSchema.safeParse(input.precision).success) {
        return { success: false, error: { code: 'VALIDATION_ERROR', message: 'precision must be an integer from 0 to 18' } }
      }
      const all = await getEquivalentsDataset()
      if (all.some((e) => e.code === code)) return { success: false, error: { code: 'CONFLICT', message: 'code already exists' } }

      const created = decodeAdminResponse(
        AdminEquivalentMutationResponseSchema,
        toMockEquivalentWire({
          code,
          precision: input.precision,
          description: String(input.description || '').trim() || code,
          is_active: Boolean(input.is_active ?? true),
        }),
        'mock POST /api/v1/admin/equivalents',
      )
      all.unshift(created)
      await appendAuditLog({
        action: 'admin.equivalents.create',
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
      if (patch.precision !== undefined && !AdminEquivalentPrecisionSchema.safeParse(patch.precision).success) {
        return { success: false, error: { code: 'VALIDATION_ERROR', message: 'precision must be an integer from 0 to 18' } }
      }
      const key = code
      const all = await getEquivalentsDataset()
      const eq = all.find((e) => e.code === key)
      if (!eq) return { success: false, error: { code: 'NOT_FOUND', message: 'equivalent not found' } }
      const before = { ...eq }
      const updated = decodeAdminResponse(
        AdminEquivalentMutationResponseSchema,
        toMockEquivalentWire({
          ...eq,
          precision: patch.precision === undefined ? eq.precision : patch.precision,
          description:
            patch.description === undefined ? eq.description : String(patch.description || '').trim() || eq.description,
        }),
        'mock PATCH /api/v1/admin/equivalents/{code}',
      )
      Object.assign(eq, updated)
      await appendAuditLog({
        action: 'admin.equivalents.patch',
        object_type: 'equivalent',
        object_id: key,
        reason: 'update',
        before_state: before,
        after_state: { ...eq },
      })
      return { success: true, data: { updated } }
    })
  },

  async setEquivalentActive(code: string, isActive: boolean, reason: string): Promise<ApiEnvelope<{ updated: Equivalent }>> {
    return withScenario('/api/v1/admin/equivalents/active', async () => {
      if (!String(reason || '').trim()) return { success: false, error: { code: 'VALIDATION_ERROR', message: 'reason is required' } }
      const key = code
      const all = await getEquivalentsDataset()
      const eq = all.find((e) => e.code === key)
      if (!eq) return { success: false, error: { code: 'NOT_FOUND', message: 'equivalent not found' } }
      const before = { ...eq }
      const updated = decodeAdminResponse(
        AdminEquivalentMutationResponseSchema,
        toMockEquivalentWire({ ...eq, is_active: Boolean(isActive) }),
        'mock PATCH /api/v1/admin/equivalents/{code}',
      )
      Object.assign(eq, updated)
      await appendAuditLog({
        action: 'admin.equivalents.patch',
        object_type: 'equivalent',
        object_id: key,
        reason,
        before_state: before,
        after_state: { ...eq },
      })
      return { success: true, data: { updated } }
    })
  },

  async getEquivalentUsage(code: string): Promise<ApiEnvelope<AdminEquivalentUsageResponse>> {
    return withScenario('/api/v1/admin/equivalents/usage', async () => {
      const key = normalizeEqCode(code)
      if (!key) return { success: false, error: { code: 'VALIDATION_ERROR', message: 'code is required' } }
      const counts = await getEquivalentUsageCounts(key)
      return {
        success: true,
        data: decodeAdminResponse(
          AdminEquivalentUsageResponseSchema,
          { code: key, ...counts },
          'mock GET /api/v1/admin/equivalents/{code}/usage',
        ),
      }
    })
  },

  async deleteEquivalent(code: string, reason: string): Promise<ApiEnvelope<AdminEquivalentDeleteResponse>> {
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
      if (usage.trustlines > 0 || usage.debts > 0 || usage.integrity_checkpoints > 0) {
        return { success: false, error: { code: 'CONFLICT', message: 'Equivalent is in use', details: usage } }
      }

      const response = decodeAdminResponse(
        AdminEquivalentDeleteResponseSchema,
        { deleted: key },
        'mock DELETE /api/v1/admin/equivalents/{code}',
      )
      all.splice(idx, 1)

      await appendAuditLog({
        action: 'admin.equivalents.delete',
        object_type: 'equivalent',
        object_id: key,
        reason,
        before_state: before,
        after_state: null,
      })

      return { success: true, data: response }
    })
  },

  async abortTx(txId: string, reason: string): Promise<ApiEnvelope<AdminAbortTxResponse>> {
    return withScenario('/api/v1/admin/transactions/abort', async () => {
      if (!reason.trim()) {
        return {
          success: false,
          error: { code: 'VALIDATION_ERROR', message: 'reason is required' },
        }
      }
      const incidents = await getIncidentsDataset()
      const incidentIndex = incidents?.findIndex((incident) => incident.tx_id === txId) ?? -1
      const transactions = await loadOptionalJson<Transaction[]>('datasets/transactions.json', [])
      const knownTransaction = transactions.find((transaction) => transaction.tx_id === txId)
      if (knownTransaction?.state === 'COMMITTED') {
        return { success: false, error: { code: 'CONFLICT', message: 'Transaction is already committed' } }
      }
      const before = incidentIndex >= 0 && incidents
        ? { ...incidents[incidentIndex] }
        : knownTransaction
          ? { state: knownTransaction.state, error: knownTransaction.error ?? null }
          : { state: 'unknown', error: null }
      const response = decodeAdminResponse(
        AdminAbortTxResponseSchema,
        { tx_id: txId, status: 'aborted' },
        'mock POST /api/v1/admin/transactions/{tx_id}/abort',
      )
      if (incidentIndex >= 0) incidents?.splice(incidentIndex, 1)
      await appendAuditLog({
        action: 'admin.transactions.abort',
        object_type: 'transaction',
        object_id: txId,
        reason,
        before_state: before,
        after_state: { state: 'ABORTED' },
      })
      return { success: true, data: response }
    })
  },

  async graphSnapshot(params?: { equivalent?: string }): Promise<ApiEnvelope<GraphSnapshot>> {
    return withScenario('/api/v1/admin/graph/snapshot', async () => {
      const eq = String(params?.equivalent || '').trim().toUpperCase()
      const participantsPath = eq ? `datasets/participants.viz-${eq}.json` : ''

      const baseParticipants = await loadJson<Participant[]>('datasets/participants.json')
      const participants = eq ? await loadOptionalJson<Participant[]>(participantsPath, baseParticipants) : baseParticipants

      const [trustlines, incidents, equivalents, debts, auditLog, transactions] = await Promise.all([
        loadJson<Trustline[]>('datasets/trustlines.json'),
        getIncidentsDataset().then((items) => items ?? []),
        loadJson<Equivalent[]>('datasets/equivalents.json'),
        loadOptionalJson<Debt[]>('datasets/debts.json', []),
        getAuditLogDataset(),
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

// Test-only helper: clears in-memory fixture caches and state.
// This keeps unit tests isolated (vitest runs files in the same process).
export function __resetMockApiForTests() {
  cache.clear()
  mockConfig = null
  mockFlags = null
  mockParticipants = null
  mockEquivalents = null
  mockAuditLog = null
  mockIncidents = undefined
  lastToastAt = 0
  lastToastMsg = ''
}
