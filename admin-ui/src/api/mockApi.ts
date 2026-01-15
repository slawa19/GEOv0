import { ApiException, type ApiEnvelope } from './envelope'
import { isRatioBelowThreshold } from '../utils/decimal'
import type {
  AuditLogEntry,
  BalanceRow,
  ClearingCycles,
  CounterpartySplitRow,
  Debt,
  Equivalent,
  GraphSnapshot,
  Incident,
  Paginated,
  Participant,
  ParticipantMetrics,
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

const cache = new Map<string, unknown>()

let lastToastAt = 0
let lastToastMsg = ''

async function notifyLoadError(message: string) {
  // Best-effort toast: do not crash the app if UI layer isn't available.
  const now = Date.now()
  if (message === lastToastMsg && now - lastToastAt < 2000) return
  lastToastAt = now
  lastToastMsg = message

  try {
    const mod = await import('element-plus')
    const ElMessage = (mod as any)?.ElMessage
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

async function loadJson<T>(relPath: string): Promise<T> {
  const key = relPath
  if (cache.has(key)) return cache.get(key) as T

  const url = `${FIXTURES_BASE}/${relPath}`
  const attempts = 3

  let lastErr: unknown = null
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      const res = await fetch(url)
      if (!res.ok) {
        throw new ApiException({
          status: res.status,
          code: 'INTERNAL_ERROR',
          message: `Failed to load ${relPath}`,
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

  await notifyLoadError(`Failed to load fixtures: ${relPath}`)

  if (lastErr instanceof ApiException) throw lastErr
  throw new ApiException({ status: 0, code: 'INTERNAL_ERROR', message: `Failed to load ${relPath}`, details: lastErr })
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
    pid: string,
    params?: { equivalent?: string | null; threshold?: string | number | null },
  ): Promise<ApiEnvelope<ParticipantMetrics>> {
    return withScenario('/api/v1/admin/participants/metrics', async () => {
      const pidKey = String(pid || '').trim()
      if (!pidKey) return { success: false, error: { code: 'VALIDATION_ERROR', message: 'pid is required' } }

      const eqRaw = String(params?.equivalent || '').trim().toUpperCase()
      const eqCode = eqRaw && eqRaw !== 'ALL' ? eqRaw : null
      const thrStr = String(params?.threshold ?? '0.10').trim() || '0.10'

      const [participants, equivalents, trustlines, debts] = await Promise.all([
        getParticipantsDataset(),
        getEquivalentsDataset(),
        getTrustlinesDataset(),
        loadOptionalJson<Debt[]>('datasets/debts.json', []),
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

      // Basic activity placeholder: keep schema stable without depending on extra datasets.
      const nowMs = Date.now()
      const windows = [7, 30, 90]
      const trustline_created: Record<number, number> = {}
      const trustline_closed: Record<number, number> = {}
      for (const w of windows) {
        trustline_created[w] = 0
        trustline_closed[w] = 0
      }

      for (const t of trustlines || []) {
        if (!eqCode || String(t.equivalent || '').trim().toUpperCase() !== eqCode) continue
        if (t.from !== pidKey && t.to !== pidKey) continue
        const ms = safeIsoToMs(t.created_at)
        if (!ms) continue
        for (const w of windows) {
          if (ms >= nowMs - w * 24 * 3600 * 1000) trustline_created[w] = (trustline_created[w] ?? 0) + 1
        }
      }

      const activity: ParticipantMetrics['activity'] = {
        windows,
        trustline_created,
        trustline_closed,
        incident_count: { 7: 0, 30: 0, 90: 0 },
        participant_ops: { 7: 0, 30: 0, 90: 0 },
        payment_committed: { 7: 0, 30: 0, 90: 0 },
        clearing_committed: { 7: 0, 30: 0, 90: 0 },
        has_transactions: false,
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

  async integrityVerify(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return withScenario('/api/v1/integrity/verify', async () => ({
      success: true,
      data: {
        status: 'finished',
        checked_at: new Date().toISOString(),
      },
    }))
  },

  async integrityRepairNetMutualDebts(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return withScenario('/api/v1/integrity/repair/net-mutual-debts', async () => ({
      success: true,
      data: { status: 'finished', repaired: true },
    }))
  },

  async integrityRepairCapDebtsToTrustLimits(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return withScenario('/api/v1/integrity/repair/cap-debts-to-trust-limits', async () => ({
      success: true,
      data: { status: 'finished', repaired: true },
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

// Test-only helper: clears in-memory fixture caches and state.
// This keeps unit tests isolated (vitest runs files in the same process).
export function __resetMockApiForTests() {
  cache.clear()
  mockConfig = null
  mockFlags = null
  mockParticipants = null
  mockEquivalents = null
  mockAuditLog = null
  lastToastAt = 0
  lastToastMsg = ''
}
