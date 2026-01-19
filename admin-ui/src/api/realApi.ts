import { assertSuccess, type ApiEnvelope, ApiException } from './envelope'
import { toastApiError } from './errorToast'
import { mapUiStatusToAdmin, normalizeAdminStatusToUi } from './statusMapping'
import { z, type ZodTypeAny } from 'zod'
import type {
  AuditLogEntry,
  ClearingCycles,
  Equivalent,
  GraphSnapshot,
  Incident,
  LiquiditySummary,
  Paginated,
  Participant,
  ParticipantMetrics,
  ParticipantsStats,
  Trustline,
} from '../types/domain'

const DEFAULT_BASE = ''
const DEFAULT_DEV_ADMIN_TOKEN = 'dev-admin-token-change-me'
const DEFAULT_DEV_BASE_URL = 'http://127.0.0.1:18000'

let warnedDefaultDevToken = false

function safeJsonPreview(value: unknown, maxLen = 500): string | null {
  try {
    return JSON.stringify(value).slice(0, maxLen)
  } catch {
    return null
  }
}

// Backend may serialize Decimal-like values as strings (preferred) but some environments
// might emit numbers. Normalize to string to keep the UI stable.
const DecimalString = z.union([z.string(), z.number()]).transform((v) => String(v))

function isApiEnvelopeLike(value: unknown): value is ApiEnvelope<unknown> {
  if (!value || typeof value !== 'object') return false
  const v = value as Record<string, unknown>
  if (typeof v.success !== 'boolean') return false
  if (v.success === true) return 'data' in v

  if (!('error' in v)) return false
  const err = v.error
  if (!err || typeof err !== 'object') return false
  const e = err as Record<string, unknown>
  return typeof e.message === 'string' || typeof e.code === 'string' || 'details' in e
}

const ParticipantSchema = z
  .object({
    pid: z.string(),
    display_name: z.string(),
    type: z.string(),
    status: z.string(),
    created_at: z.string().optional(),
    meta: z.record(z.string(), z.unknown()).optional(),

    net_balance_atoms: z.string().nullable().optional(),
    net_sign: z.union([z.literal(-1), z.literal(0), z.literal(1)]).nullable().optional(),
    viz_color_key: z
      .union([
        z.literal('person'),
        z.literal('business'),
        z.literal('debt'),
        z.literal('suspended'),
        z.literal('left'),
        z.literal('deleted'),
      ])
      .nullable()
      .optional(),
    viz_size: z
      .object({
        w: z.number(),
        h: z.number(),
      })
      .nullable()
      .optional(),
  })
  .passthrough()

const TrustlineSchema = z
  .object({
    equivalent: z.string(),
    from: z.string(),
    to: z.string(),
    from_display_name: z.string().nullable().optional(),
    to_display_name: z.string().nullable().optional(),
    limit: DecimalString,
    used: DecimalString,
    available: DecimalString,
    status: z.string(),
    created_at: z.string(),
    policy: z.record(z.string(), z.unknown()).optional(),
  })
  .passthrough()

const IncidentSchema = z
  .object({
    tx_id: z.string(),
    state: z.string(),
    initiator_pid: z.string(),
    equivalent: z.string(),
    age_seconds: z.number(),
    sla_seconds: z.number(),
    created_at: z.string().optional(),
  })
  .passthrough()

const EquivalentSchema = z
  .object({
    code: z.string(),
    precision: z.number(),
    description: z.string(),
    is_active: z.boolean(),
  })
  .passthrough()

const DebtSchema = z
  .object({
    equivalent: z.string(),
    debtor: z.string(),
    creditor: z.string(),
    amount: DecimalString,
  })
  .passthrough()

const AuditLogEntrySchema = z
  .object({
    id: z.string(),
    timestamp: z.string(),
    actor_id: z.string(),
    actor_role: z.string(),
    action: z.string(),
    object_type: z.string(),
    object_id: z.string(),
    reason: z.string().nullable().optional(),
    before_state: z.unknown().optional(),
    after_state: z.unknown().optional(),
    request_id: z.string().optional(),
    ip_address: z.string().optional(),
  })
  .passthrough()

const TransactionSchema = z
  .object({
    id: z.string().optional(),
    tx_id: z.string(),
    idempotency_key: z.string().nullable().optional(),
    type: z.string(),
    initiator_pid: z.string(),
    payload: z.record(z.string(), z.unknown()),
    signatures: z.array(z.unknown()).nullable().optional(),
    state: z.string(),
    error: z.record(z.string(), z.unknown()).nullable().optional(),
    created_at: z.string(),
    updated_at: z.string(),
  })
  .passthrough()

const GraphSnapshotSchema = z
  .object({
    participants: z.array(ParticipantSchema),
    trustlines: z.array(TrustlineSchema),
    incidents: z.array(IncidentSchema),
    equivalents: z.array(EquivalentSchema),
    debts: z.array(DebtSchema),
    audit_log: z.array(AuditLogEntrySchema),
    transactions: z.array(TransactionSchema),
  })
  .passthrough()

const ClearingCycleEdgeSchema = z
  .object({
    equivalent: z.string(),
    debtor: z.string(),
    creditor: z.string(),
    amount: DecimalString,
  })
  .passthrough()

const ClearingCyclesSchema = z
  .object({
    equivalents: z.record(
      z.string(),
      z
        .object({
          cycles: z.array(z.array(ClearingCycleEdgeSchema)),
        })
        .passthrough(),
    ),
  })
  .passthrough()

const BalanceRowSchema = z
  .object({
    equivalent: z.string(),
    outgoing_limit: DecimalString,
    outgoing_used: DecimalString,
    incoming_limit: DecimalString,
    incoming_used: DecimalString,
    total_debt: DecimalString,
    total_credit: DecimalString,
    net: DecimalString,
  })
  .passthrough()

const ParticipantMetricsSchema = z
  .object({
    pid: z.string(),
    equivalent: z.string().nullable(),
    balance_rows: z.array(BalanceRowSchema),
    counterparty: z.unknown().nullable().optional(),
    concentration: z.unknown().nullable().optional(),
    distribution: z.unknown().nullable().optional(),
    rank: z.unknown().nullable().optional(),
    capacity: z.unknown().nullable().optional(),
    activity: z.unknown().nullable().optional(),
  })
  .passthrough()

const ParticipantsStatsSchema = z
  .object({
    participants_by_status: z.record(z.string(), z.number()),
    participants_by_type: z.record(z.string(), z.number()),
    total_participants: z.number(),
  })
  .passthrough()

const TrustlineBottlenecksSchema = z
  .object({
    threshold: z.number(),
    items: z.array(TrustlineSchema),
  })
  .passthrough()

const LiquidityNetRowSchema = z
  .object({
    pid: z.string(),
    display_name: z.string(),
    net: DecimalString,
  })
  .passthrough()

const LiquiditySummarySchema = z
  .object({
    equivalent: z.string().nullable(),
    threshold: z.number(),
    updated_at: z.string(),
    active_trustlines: z.number(),
    bottlenecks: z.number(),
    incidents_over_sla: z.number(),
    total_limit: DecimalString,
    total_used: DecimalString,
    total_available: DecimalString,
    top_creditors: z.array(LiquidityNetRowSchema),
    top_debtors: z.array(LiquidityNetRowSchema),
    top_by_abs_net: z.array(LiquidityNetRowSchema),
    top_bottleneck_edges: z.array(TrustlineSchema),
  })
  .passthrough()

function isProdBuild(): boolean {
  const forced = (globalThis as unknown as { __GEO_ADMINUI_FORCE_PROD__?: unknown })?.__GEO_ADMINUI_FORCE_PROD__
  if (forced === true) return true
  if (forced === false) return false

  // In Vite builds, PROD is a boolean constant; MODE is typically 'production'.
  // In tests, PROD may be non-writable; MODE is easier to stub.
  const mode = String((import.meta.env as unknown as Record<string, unknown>).MODE || '').toLowerCase()
  // Read NODE_ENV via globalThis to avoid transform-time replacement.
  const nodeEnv =
    typeof globalThis !== 'undefined' && (globalThis as unknown as { process?: { env?: Record<string, unknown> } })?.process?.env
      ? String((globalThis as unknown as { process?: { env?: Record<string, unknown> } }).process?.env?.NODE_ENV || '')
      : ''
  return Boolean(import.meta.env.PROD) || mode === 'production' || nodeEnv.toLowerCase() === 'production'
}

function baseUrl(): string {
  // When using Vite proxy, keep base empty and call relative paths.
  const envVal = (import.meta.env as unknown as Record<string, unknown>).VITE_API_BASE_URL
  const raw = (envVal === undefined || envVal === null ? DEFAULT_BASE : String(envVal)).trim()
  if (raw) return raw.replace(/\/$/, '')

  // Dev ergonomics: if API mode is real and base URL is not configured, default
  // to the standard local backend port used by scripts/run_local.ps1.
  if (import.meta.env.DEV && (envVal === undefined || envVal === null)) return DEFAULT_DEV_BASE_URL

  return ''
}

function adminToken(): string | null {
  const key = 'admin-ui.adminToken'

  // Prefer explicit env-configured token (useful for teams / non-default backend config).
  const envTok = (import.meta.env.VITE_ADMIN_TOKEN || '').toString().trim()
  if (envTok) {
    if (envTok === DEFAULT_DEV_ADMIN_TOKEN) {
      if (isProdBuild()) {
        throw new Error('Refusing to use DEFAULT_DEV_ADMIN_TOKEN in production build')
      }
      if (!warnedDefaultDevToken) {
        warnedDefaultDevToken = true
        // eslint-disable-next-line no-console
        console.info('Using DEFAULT_DEV_ADMIN_TOKEN (dev-only). Do not use in production.')
      }
    }
    return envTok
  }

  try {
    const v = (localStorage.getItem(key) || '').trim()
    if (v) {
      if (v === DEFAULT_DEV_ADMIN_TOKEN) {
        if (isProdBuild()) {
          throw new Error('Refusing to use DEFAULT_DEV_ADMIN_TOKEN from localStorage in production build')
        }
        if (!warnedDefaultDevToken) {
          warnedDefaultDevToken = true
          // eslint-disable-next-line no-console
          console.info('Using DEFAULT_DEV_ADMIN_TOKEN from localStorage (dev-only). Do not use in production.')
        }
      }
      return v
    }

    // Dev ergonomics: if no token is set yet, seed the default backend token.
    // This avoids the UI spamming 403s on first run.
    if (import.meta.env.DEV) {
      try {
        localStorage.setItem(key, DEFAULT_DEV_ADMIN_TOKEN)
      } catch {
        // ignore
      }

      if (!warnedDefaultDevToken) {
        warnedDefaultDevToken = true
        // eslint-disable-next-line no-console
        console.info('Seeding DEFAULT_DEV_ADMIN_TOKEN into localStorage (dev-only). Do not use in production.')
      }
      return DEFAULT_DEV_ADMIN_TOKEN
    }

    return null
  } catch (err) {
    if (err instanceof Error && /refusing to use default_dev_admin_token/i.test(err.message)) {
      throw err
    }
    if (isProdBuild()) return null
    if (!warnedDefaultDevToken) {
      warnedDefaultDevToken = true
      // eslint-disable-next-line no-console
      console.warn('Using DEFAULT_DEV_ADMIN_TOKEN due to localStorage access error (dev-only). Do not use in production.')
    }
    return DEFAULT_DEV_ADMIN_TOKEN
  }
}

export async function requestJson<T>(
  pathname: string,
  opts?: {
    method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
    body?: unknown
    headers?: Record<string, string>
    admin?: boolean
    timeoutMs?: number
    schema?: ZodTypeAny
    toast?: boolean
  },
): Promise<ApiEnvelope<T>> {
  const method = opts?.method || 'GET'
  const url = `${baseUrl()}${pathname}`

  try {
    const headers: Record<string, string> = {
      Accept: 'application/json',
      ...(opts?.body ? { 'Content-Type': 'application/json' } : {}),
      ...(opts?.headers || {}),
    }

    if (opts?.admin) {
      const tok = adminToken()
      if (tok) headers['X-Admin-Token'] = tok
    }

    const timeoutMs = typeof opts?.timeoutMs === 'number' && Number.isFinite(opts.timeoutMs) ? opts.timeoutMs : undefined
    const controller = timeoutMs !== undefined ? new AbortController() : undefined
    const timeoutId: ReturnType<typeof setTimeout> | undefined =
      controller && timeoutMs !== undefined && timeoutMs > 0
        ? setTimeout(() => controller.abort(), timeoutMs)
        : undefined

    let res: Response
    try {
      res = await fetch(url, {
        method,
        headers,
        body: opts?.body ? JSON.stringify(opts.body) : undefined,
        signal: controller?.signal,
      })
    } catch (err) {
      if (timeoutId) clearTimeout(timeoutId)
      const isAbort = controller?.signal.aborted || (err instanceof Error && /abort/i.test(err.name))
      if (isAbort) {
        throw new ApiException({
          status: 0,
          code: 'TIMEOUT',
          message: `${method} ${url} -> timeout after ${timeoutMs}ms`,
          details: {
            url,
            method,
            timeout_ms: timeoutMs,
          },
        })
      }
      throw err
    } finally {
      if (timeoutId) clearTimeout(timeoutId)
    }

    // 204/205 are valid successful responses without a body.
    // Some fetch implementations may throw on res.text() for these.
    if (res.ok && (res.status === 204 || res.status === 205)) {
      return { success: true, data: undefined as T }
    }

    // The backend might already return ApiEnvelope. If not, adapt here.
    const text = await res.text()
    let parsed: unknown = undefined
    try {
      parsed = text ? JSON.parse(text) : undefined
    } catch {
      parsed = undefined
    }

    // If backend claims OK but returns empty/invalid JSON, fail loudly.
    if (res.ok && (!text || parsed === undefined || parsed === null)) {
      throw new ApiException({
        status: res.status,
        code: 'INVALID_JSON',
        message: `${method} ${url} -> ${res.status}: Invalid/empty JSON response`,
        details: {
          url,
          method,
          status: res.status,
          status_text: (res.statusText || '').trim(),
          body_preview: (text || '').slice(0, 500),
        },
      })
    }

    let env: ApiEnvelope<T>
    if (res.ok && isApiEnvelopeLike(parsed)) {
      env = parsed as ApiEnvelope<T>
    } else if (!res.ok) {
      const parsedObj = parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : undefined
      const errorObj = parsedObj?.error && typeof parsedObj.error === 'object' ? (parsedObj.error as Record<string, unknown>) : undefined

      const msg = String((errorObj?.message ?? parsedObj?.message ?? `HTTP ${res.status}`) as unknown)
      const code = String((errorObj?.code ?? parsedObj?.code ?? 'HTTP_ERROR') as unknown)
      const details = (errorObj?.details ?? parsedObj?.details) as unknown
      const statusText = (res.statusText || '').trim()
      const decorated = `${method} ${url} -> ${res.status}${statusText ? ` ${statusText}` : ''}: ${msg}`
      throw new ApiException({
        status: res.status,
        code,
        message: decorated,
        details: {
          url,
          method,
          status: res.status,
          status_text: statusText,
          code,
          message: msg,
          details,
        },
      })
    } else {
      // If backend returns raw payload (non-envelope), wrap it.
      env = { success: true, data: parsed as T }
    }

    const schema = opts?.schema
    if (schema && env.success) {
      const validated = schema.safeParse(env.data)
      if (!validated.success) {
        throw new ApiException({
          status: res.status,
          code: 'INVALID_RESPONSE',
          message: `${method} ${url} -> ${res.status}: Response JSON does not match expected schema`,
          details: {
            url,
            method,
            status: res.status,
            issues: validated.error.issues,
            data_preview: safeJsonPreview(env.data),
          },
        })
      }

      env = { ...env, data: validated.data as T }
    }

    return env
  } catch (err) {
    if (opts?.toast !== false) {
      void toastApiError(err, { fallbackTitle: `${method} ${pathname} failed` })
    }
    throw err
  }
}

let featureFlagsPatchQueue: Promise<unknown> = Promise.resolve()

function enqueueFeatureFlagsPatch<T>(job: () => Promise<T>): Promise<T> {
  const run = featureFlagsPatchQueue.then(job, job)
  featureFlagsPatchQueue = run.then(
    () => undefined,
    () => undefined,
  )
  return run
}

function bestEffortTotal(page: number, perPage: number, itemsLen: number): number {
  const p = Math.max(1, page || 1)
  const pp = Math.max(1, perPage || 1)
  const offset = (p - 1) * pp
  // Backend list endpoints currently don't return total. We approximate:
  // - if last page (itemsLen < pp) => total = offset + itemsLen
  // - else => total = offset + itemsLen + 1 (signals “has next page”)
  return itemsLen < pp ? offset + itemsLen : offset + itemsLen + 1
}

export function buildQuery(pathname: string, params: Record<string, unknown>): string {
  const rawBase = baseUrl()

  const [pathOnly = '', initialQuery = ''] = String(pathname || '').split('?', 2)
  const sp = new URLSearchParams(initialQuery)

  for (const [k, v] of Object.entries(params || {})) {
    if (v === undefined || v === null || v === '') continue
    if (Array.isArray(v)) {
      for (const item of v) {
        if (item === undefined || item === null || item === '') continue
        sp.append(k, String(item))
      }
      continue
    }
    sp.set(k, String(v))
  }

  const qs = sp.toString()
  if (!rawBase) return qs ? `${pathOnly}?${qs}` : pathOnly

  const u = new URL(`${rawBase}${pathOnly}`)
  u.search = qs ? `?${qs}` : ''
  return u.pathname + u.search
}

export const realApi = {
  health(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return requestJson('/api/v1/health')
  },

  healthDb(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return requestJson('/api/v1/health/db')
  },

  migrations(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return requestJson('/api/v1/admin/migrations', { admin: true })
  },

  async getConfig(): Promise<ApiEnvelope<Record<string, unknown>>> {
    // Backend returns { items: [{ key, value, mutable }] }. UI currently expects a flat object.
    const raw = await requestJson<{ items: Array<{ key: string; value: unknown; mutable?: boolean }> }>(
      '/api/v1/admin/config',
      { admin: true },
    )
    const data = assertSuccess(raw).items || []
    const mapped: Record<string, unknown> = {}
    for (const it of data) mapped[it.key] = it.value
    return { success: true, data: mapped }
  },

  patchConfig(patch: Record<string, unknown>): Promise<ApiEnvelope<{ updated: string[] }>> {
    return requestJson('/api/v1/admin/config', { method: 'PATCH', body: { updates: patch }, admin: true })
  },

  getFeatureFlags(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return requestJson('/api/v1/admin/feature-flags', { admin: true })
  },

  async patchFeatureFlags(patch: Record<string, unknown>): Promise<ApiEnvelope<Record<string, unknown>>> {
    // Backend expects full payload: { multipath_enabled, full_multipath_enabled, clearing_enabled, reason? }
    // Serialize calls within the same UI instance to reduce local lost-updates.
    return enqueueFeatureFlagsPatch(async () => {
      const current =
        assertSuccess(await requestJson<Record<string, unknown>>('/api/v1/admin/feature-flags', { admin: true })) || {}
      const body = { ...current, ...patch }
      return requestJson('/api/v1/admin/feature-flags', { method: 'PATCH', body, admin: true })
    })
  },

  integrityStatus(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return requestJson('/api/v1/integrity/status', { admin: true })
  },

  integrityVerify(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return requestJson('/api/v1/integrity/verify', { method: 'POST', body: {}, admin: true })
  },

  integrityRepairNetMutualDebts(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return requestJson('/api/v1/integrity/repair/net-mutual-debts', { method: 'POST', body: {}, admin: true })
  },

  integrityRepairCapDebtsToTrustLimits(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return requestJson('/api/v1/integrity/repair/cap-debts-to-trust-limits', { method: 'POST', body: {}, admin: true })
  },

  // The endpoints below should be aligned to OpenAPI; adjust pathname/query as backend stabilizes.
  async listParticipants(params: {
    page?: number
    per_page?: number
    status?: string
    type?: string
    q?: string
  }): Promise<ApiEnvelope<Paginated<Participant>>> {
    const page = params.page ?? 1
    const per_page = params.per_page ?? 20
    const status = mapUiStatusToAdmin(params.status)

    const payload = await requestJson<Paginated<Participant>>(
      buildQuery('/api/v1/admin/participants', { ...params, status: status || undefined, page, per_page }),
      { admin: true },
    )

    const backend = assertSuccess(payload)
    const items = (backend.items || []).map((p) => ({
      ...p,
      status: normalizeAdminStatusToUi(p.status),
    }))

    return {
      success: true,
      data: {
        items,
        page: backend.page ?? page,
        per_page: backend.per_page ?? per_page,
        total: typeof backend.total === 'number' ? backend.total : bestEffortTotal(page, per_page, items.length),
      },
    }
  },

  participantsStats(): Promise<ApiEnvelope<ParticipantsStats>> {
    return requestJson<ParticipantsStats>('/api/v1/admin/participants/stats', { admin: true, schema: ParticipantsStatsSchema })
  },

  trustlineBottlenecks(params: { threshold?: string; limit?: number; equivalent?: string }): Promise<ApiEnvelope<{ threshold: number; items: Trustline[] }>> {
    const threshold = String(params.threshold ?? '').trim() || undefined
    const limit = params.limit ?? 10
    const equivalent = String(params.equivalent ?? '').trim() || undefined
    return requestJson<{ threshold: number; items: Trustline[] }>(
      buildQuery('/api/v1/admin/trustlines/bottlenecks', { threshold, limit, equivalent }),
      { admin: true, schema: TrustlineBottlenecksSchema },
    )
  },

  liquiditySummary(params: { equivalent?: string; threshold?: string; limit?: number }): Promise<ApiEnvelope<LiquiditySummary>> {
    const threshold = String(params.threshold ?? '').trim() || undefined
    const limit = params.limit ?? 10
    const equivalentRaw = String(params.equivalent ?? '').trim().toUpperCase()
    const equivalent = equivalentRaw && equivalentRaw !== 'ALL' ? equivalentRaw : undefined
    return requestJson<LiquiditySummary>(
      buildQuery('/api/v1/admin/liquidity/summary', { equivalent, threshold, limit }),
      { admin: true, schema: LiquiditySummarySchema },
    )
  },

  async freezeParticipant(pid: string, reason: string): Promise<ApiEnvelope<{ pid: string; status: string }>> {
    const r = await requestJson<{ pid: string; status: string }>(
      `/api/v1/admin/participants/${encodeURIComponent(pid)}/freeze`,
      {
      method: 'POST',
      body: { reason },
      admin: true,
      },
    )

    if (!r.success) return r
    return { success: true, data: { pid: r.data?.pid || pid, status: normalizeAdminStatusToUi(r.data?.status) } }
  },

  async unfreezeParticipant(pid: string, reason: string): Promise<ApiEnvelope<{ pid: string; status: string }>> {
    const r = await requestJson<{ pid: string; status: string }>(
      `/api/v1/admin/participants/${encodeURIComponent(pid)}/unfreeze`,
      {
      method: 'POST',
      body: { reason },
      admin: true,
      },
    )

    if (!r.success) return r
    return { success: true, data: { pid: r.data?.pid || pid, status: normalizeAdminStatusToUi(r.data?.status) } }
  },

  async listTrustlines(params: {
    page?: number
    per_page?: number
    equivalent?: string
    creditor?: string
    debtor?: string
    status?: string
  }): Promise<ApiEnvelope<Paginated<Trustline>>> {
    const page = params.page ?? 1
    const per_page = params.per_page ?? 20

    const payload = await requestJson<Paginated<Trustline>>(
      buildQuery('/api/v1/admin/trustlines', { ...params, page, per_page }),
      { admin: true },
    )
    const backend = assertSuccess(payload)
    const items = backend.items || []
    return {
      success: true,
      data: {
        items,
        page: backend.page ?? page,
        per_page: backend.per_page ?? per_page,
        total: typeof backend.total === 'number' ? backend.total : bestEffortTotal(page, per_page, items.length),
      },
    }
  },

  async listAuditLog(params: {
    page?: number
    per_page?: number
    q?: string
    action?: string
    object_type?: string
    object_id?: string
  }): Promise<ApiEnvelope<Paginated<AuditLogEntry>>> {
    const page = params.page ?? 1
    const per_page = params.per_page ?? 50
    const payload = await requestJson<Paginated<AuditLogEntry>>(
      buildQuery('/api/v1/admin/audit-log', { ...params, page, per_page }),
      { admin: true },
    )
    const backend = assertSuccess(payload)
    const items = backend.items || []
    return {
      success: true,
      data: {
        items,
        page: backend.page ?? page,
        per_page: backend.per_page ?? per_page,
        total: typeof backend.total === 'number' ? backend.total : bestEffortTotal(page, per_page, items.length),
      },
    }
  },

  async listEquivalents(params: { include_inactive?: boolean }): Promise<ApiEnvelope<{ items: Equivalent[] }>> {
    const payload = await requestJson<{ items: Equivalent[] }>(
      buildQuery('/api/v1/admin/equivalents', {
        include_inactive: params.include_inactive ? true : undefined,
      }),
      { admin: true },
    )
    const items = assertSuccess(payload).items || []
    return { success: true, data: { items } }
  },

  async createEquivalent(input: {
    code: string
    precision: number
    description: string
    is_active?: boolean
  }): Promise<ApiEnvelope<{ created: Equivalent }>> {
    const created = await requestJson<Equivalent>('/api/v1/admin/equivalents', {
      method: 'POST',
      body: {
        code: input.code,
        precision: input.precision,
        description: input.description,
        is_active: input.is_active ?? true,
      },
      admin: true,
    })
    return { success: true, data: { created: assertSuccess(created) } }
  },

  async updateEquivalent(
    code: string,
    patch: Partial<{ precision: number; description: string }>,
  ): Promise<ApiEnvelope<{ updated: Equivalent }>> {
    const updated = await requestJson<Equivalent>(`/api/v1/admin/equivalents/${encodeURIComponent(code)}`, {
      method: 'PATCH',
      body: patch,
      admin: true,
    })
    return { success: true, data: { updated: assertSuccess(updated) } }
  },

  async setEquivalentActive(code: string, isActive: boolean, reason: string): Promise<ApiEnvelope<{ updated: Equivalent }>> {
    const updated = await requestJson<Equivalent>(`/api/v1/admin/equivalents/${encodeURIComponent(code)}`, {
      method: 'PATCH',
      body: { is_active: isActive, reason },
      admin: true,
    })
    return { success: true, data: { updated: assertSuccess(updated) } }
  },

  getEquivalentUsage(code: string): Promise<ApiEnvelope<Record<string, unknown>>> {
    return requestJson(`/api/v1/admin/equivalents/${encodeURIComponent(code)}/usage`, { admin: true })
  },

  deleteEquivalent(code: string, reason: string): Promise<ApiEnvelope<{ deleted: string }>> {
    return requestJson(`/api/v1/admin/equivalents/${encodeURIComponent(code)}`, {
      method: 'DELETE',
      body: { reason },
      admin: true,
    })
  },

  listIncidents(params: {
    page?: number
    per_page?: number
  }): Promise<ApiEnvelope<Paginated<Incident>>> {
    const page = params.page ?? 1
    const per_page = params.per_page ?? 20
    return requestJson<Paginated<Incident>>(
      buildQuery('/api/v1/admin/incidents', { page, per_page }),
      { admin: true },
    )
  },

  abortTx(txId: string, reason: string): Promise<ApiEnvelope<{ tx_id: string; status: 'aborted' }>> {
    return requestJson<{ tx_id: string; status: 'aborted' }>(
      `/api/v1/admin/transactions/${encodeURIComponent(txId)}/abort`,
      { method: 'POST', body: { reason }, admin: true },
    )
  },

  graphSnapshot(params?: { equivalent?: string }): Promise<ApiEnvelope<GraphSnapshot>> {
    const equivalent = String(params?.equivalent || '').trim().toUpperCase()
    const url = buildQuery('/api/v1/admin/graph/snapshot', { equivalent: equivalent || undefined })
    return requestJson<GraphSnapshot>(url, { admin: true, schema: GraphSnapshotSchema }).then((r) => {
      const s = assertSuccess(r)
      const participants = (s.participants || []).map((p) => ({
        ...p,
        status: normalizeAdminStatusToUi(p.status),
      }))
      return { success: true, data: { ...s, participants } }
    })
  },

  graphEgo(params: { pid: string; depth?: 1 | 2; equivalent?: string; status?: string[] }): Promise<ApiEnvelope<GraphSnapshot>> {
    const pid = String(params?.pid || '').trim()
    const depth = params?.depth ?? 1
    const equivalent = String(params?.equivalent || '').trim()
    const status = (params?.status || []).map((s) => String(s || '').trim()).filter(Boolean)
    return requestJson<GraphSnapshot>(buildQuery('/api/v1/admin/graph/ego', { pid, depth, equivalent, status }), {
      admin: true,
      schema: GraphSnapshotSchema,
    }).then((r) => {
      const s = assertSuccess(r)
      const participants = (s.participants || []).map((p) => ({
        ...p,
        status: normalizeAdminStatusToUi(p.status),
      }))
      return { success: true, data: { ...s, participants } }
    })
  },

  clearingCycles(params?: { participant_pid?: string }): Promise<ApiEnvelope<ClearingCycles>> {
    const participant_pid = String(params?.participant_pid || '').trim()
    return requestJson<ClearingCycles>(buildQuery('/api/v1/admin/clearing/cycles', { participant_pid }), {
      admin: true,
      schema: ClearingCyclesSchema,
    })
  },

  async participantMetrics(
    pid: string,
    params?: { equivalent?: string | null; threshold?: string | number | null },
  ): Promise<ApiEnvelope<ParticipantMetrics>> {
    const eq = params?.equivalent ? String(params.equivalent) : undefined
    const thrRaw = params?.threshold
    const threshold = thrRaw === null || thrRaw === undefined || thrRaw === '' ? undefined : Number(thrRaw)

    const pathname = `/api/v1/admin/participants/${encodeURIComponent(pid)}/metrics`
    const url = buildQuery(pathname, { equivalent: eq, threshold: Number.isFinite(threshold) ? threshold : undefined })
    return await requestJson<ParticipantMetrics>(url, { admin: true, schema: ParticipantMetricsSchema })
  },
}
