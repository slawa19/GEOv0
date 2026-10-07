import { DEFAULT_REQUEST_TIMEOUT_MS, HEALTH_REQUEST_TIMEOUT_MS } from '../constants/timing'
import { ApiException } from './apiException'
import { mapUiStatusToAdmin, normalizeAdminStatusToUi } from './statusMapping'
import {
  AdminConfigPatchResponseSchema,
  AdminConfigResponseSchema,
  AdminEquivalentDeleteResponseSchema,
  AdminEquivalentMutationResponseSchema,
  AdminEquivalentUsageResponseSchema,
  AdminParticipantActionResponseSchema,
  IntegrityStatusResponseSchema,
  IntegritySummaryResponseSchema,
  IntegrityVerifyResponseSchema,
  flattenAdminConfig,
  type AdminConfigPatchResponse,
  type AdminEquivalentDeleteResponse,
  type AdminEquivalentUsageResponse,
  type AdminParticipantActionResponse,
  type IntegrityStatusResponse,
  type IntegritySummaryResponse,
  type IntegrityVerifyResponse,
} from './adminContracts'
import {
  AuditLogListSchema,
  EquivalentsListSchema,
  GraphSnapshotSchema,
  LiquiditySummarySchema,
  ParticipantMetricsSchema,
  ParticipantsListSchema,
  ParticipantsStatsSchema,
  TrustlinesListSchema,
} from './schemas'
import type { z } from 'zod'
import type {
  AuditLogEntry,
  Equivalent,
  GraphSnapshot,
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
  // `null` means "no token configured": `requestJson` turns it into an explicit 401 refusal before any
  // request is sent (032 S4), so a missing token is an authorization error, not a page of empty lists.
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
    // This avoids the UI spamming 403s on first run. Never in a production build: there a missing
    // token is an explicit authorization error (`requestJson`).
    if (!isProdBuild()) {
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

type RequestOptions = {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  body?: unknown
  headers?: Record<string, string>
  admin?: boolean
  /** Bound of the whole exchange - headers AND body. Default `DEFAULT_REQUEST_TIMEOUT_MS`. */
  timeoutMs?: number
}

function nonEmptyString(v: unknown): string | null {
  return typeof v === 'string' && v.trim() ? v.trim() : null
}

/**
 * One `fetch` bounded in time, covering the read of the body: a server that sends the headers and then stalls
 * must not hold the caller for ever (032 S6, E-2). The bound is enforced twice: the request is aborted, and the
 * wait is raced against the bound itself, so an implementation that ignores the abort signal cannot hang us.
 */
async function fetchBounded(
  method: string,
  url: string,
  init: { headers: Record<string, string>; body?: string },
  timeoutMs: number,
): Promise<{ res: Response; text: string }> {
  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined

  const timeoutError = () =>
    new ApiException({
      status: 0,
      code: 'TIMEOUT',
      message: `${method} ${url} -> timeout after ${timeoutMs}ms`,
      details: { url, method, timeout_ms: timeoutMs },
    })

  const bound = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      controller.abort()
      reject(timeoutError())
    }, timeoutMs)
  })
  // Consumed by the races below; without this a rejection after they settled would be an unhandled one.
  bound.catch(() => undefined)

  try {
    const res = await Promise.race([fetch(url, { method, ...init, signal: controller.signal }), bound])
    // 204/205 are valid successful responses without a body; some fetch implementations throw on res.text() for them.
    if (res.ok && (res.status === 204 || res.status === 205)) return { res, text: '' }
    const text = await Promise.race([res.text(), bound])
    return { res, text }
  } catch (err) {
    // The abort made `fetch` or `text()` reject with an AbortError before the bound's own rejection won the race.
    if (controller.signal.aborted && !(err instanceof ApiException)) throw timeoutError()
    throw err
  } finally {
    if (timer !== undefined) clearTimeout(timer)
  }
}

/**
 * The one HTTP call of the Admin UI. Returns the decoded body or throws `ApiException`; it raises no toast -
 * the caller shows the error with `describeError` (032 S6, E-3).
 *
 * With a `schema` the body is validated and the result has the schema's type; without one it is the caller's
 * unchecked claim `T` (health probes, tests).
 */
export async function requestJson<T>(pathname: string, opts: RequestOptions & { schema: z.ZodType<T> }): Promise<T>
export async function requestJson<T = unknown>(pathname: string, opts?: RequestOptions & { schema?: undefined }): Promise<T>
export async function requestJson(pathname: string, opts: RequestOptions & { schema?: z.ZodType } = {}): Promise<unknown> {
  const method = opts.method || 'GET'
  const url = `${baseUrl()}${pathname}`

  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(opts.body ? { 'Content-Type': 'application/json' } : {}),
    ...(opts.headers || {}),
  }

  if (opts.admin) {
    const tok = adminToken()
    if (!tok) {
      throw new ApiException({
        status: 401,
        code: 'ADMIN_TOKEN_MISSING',
        message: `${method} ${url} -> not sent: no admin token is configured (VITE_ADMIN_TOKEN or localStorage "admin-ui.adminToken")`,
        details: { url, method },
      })
    }
    headers['X-Admin-Token'] = tok
  }

  const timeoutMs =
    typeof opts.timeoutMs === 'number' && Number.isFinite(opts.timeoutMs) && opts.timeoutMs > 0
      ? opts.timeoutMs
      : DEFAULT_REQUEST_TIMEOUT_MS

  const { res, text } = await fetchBounded(
    method,
    url,
    { headers, body: opts.body ? JSON.stringify(opts.body) : undefined },
    timeoutMs,
  )
  const headerRequestId = nonEmptyString(res.headers?.get('X-Request-ID'))

  if (res.ok && (res.status === 204 || res.status === 205)) return undefined

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
      requestId: headerRequestId,
    })
  }

  if (!res.ok) {
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
      requestId: nonEmptyString(errorObj?.request_id) ?? nonEmptyString(parsedObj?.request_id) ?? headerRequestId,
    })
  }

  const schema = opts.schema
  if (!schema) return parsed
  const validated = schema.safeParse(parsed)
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
        data_preview: safeJsonPreview(parsed),
      },
      requestId: headerRequestId,
    })
  }
  return validated.data
}

// F-013-1 / T1302. `include` is a comma-separated list on the wire (`_parse_include_csv`), not a
// repeated query parameter - buildQuery would emit `include=a&include=b` for an array and the server
// would read only the last one. Joining here keeps that detail in one place.
export function normalizeGraphInclude(include?: string[]): string {
  return (include || [])
    .map((x) => String(x || '').trim().toLowerCase())
    .filter(Boolean)
    .join(',')
}

/**
 * `pathname` with `params` merged into its query string. Returns the path RELATIVE to the API base:
 * `requestJson` puts `VITE_API_BASE_URL` in front. It used to resolve the base here as well and return the
 * base's own path, so a base with a path (`https://h/prefix`) was applied twice (032 S6, E-4).
 */
export function buildQuery(pathname: string, params: Record<string, unknown>): string {
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
  return qs ? `${pathOnly}?${qs}` : pathOnly
}

export const realApi = {
  // The three health-poll probes get the short bound (`HEALTH_REQUEST_TIMEOUT_MS`): the header status polls them
  // one after another and must not wait on a dead hub as long as an ordinary request would.
  health(): Promise<Record<string, unknown>> {
    return requestJson('/api/v1/health', { timeoutMs: HEALTH_REQUEST_TIMEOUT_MS })
  },

  healthDb(): Promise<Record<string, unknown>> {
    return requestJson('/api/v1/health/db', { timeoutMs: HEALTH_REQUEST_TIMEOUT_MS })
  },

  migrations(): Promise<Record<string, unknown>> {
    return requestJson('/api/v1/admin/migrations', { admin: true, timeoutMs: HEALTH_REQUEST_TIMEOUT_MS })
  },

  async getConfig(): Promise<Record<string, unknown>> {
    // Backend returns { items: [{ key, value, mutable }] }. The UI works with a flat object of the mutable keys.
    const raw = await requestJson('/api/v1/admin/config', {
      admin: true,
      schema: AdminConfigResponseSchema,
    })
    return flattenAdminConfig(raw)
  },

  patchConfig(patch: Record<string, unknown>): Promise<AdminConfigPatchResponse> {
    return requestJson('/api/v1/admin/config', {
      method: 'PATCH',
      body: { updates: patch },
      admin: true,
      schema: AdminConfigPatchResponseSchema,
    })
  },

  integrityStatus(): Promise<IntegrityStatusResponse> {
    return requestJson('/api/v1/integrity/status', { admin: true, schema: IntegrityStatusResponseSchema })
  },

  // 032 S5 (F-4): which equivalents are on an integrity hold. The admin token is accepted by this
  // participant-or-admin route (`require_participant_or_admin`).
  integritySummary(): Promise<IntegritySummaryResponse> {
    return requestJson('/api/v1/integrity/summary', { admin: true, schema: IntegritySummaryResponseSchema })
  },

  // 032 S5 (F-4): lift an equivalent's integrity hold, with the operator's reason (required, audited). The
  // refusals (409 `no_integrity_hold`, `no_later_passed_reconciliation_result`) are shown by the Integrity screen
  // as text (`describeHoldClearRefusal`).
  clearIntegrityHold(code: string, reason: string): Promise<Equivalent> {
    return requestJson<Equivalent>(`/api/v1/admin/equivalents/${encodeURIComponent(code)}/integrity-hold/clear`, {
      method: 'POST',
      body: { reason },
      admin: true,
      schema: AdminEquivalentMutationResponseSchema,
    })
  },

  integrityVerify(): Promise<IntegrityVerifyResponse> {
    return requestJson('/api/v1/integrity/verify', {
      method: 'POST',
      body: {},
      admin: true,
      schema: IntegrityVerifyResponseSchema,
    })
  },

  // The endpoints below should be aligned to OpenAPI; adjust pathname/query as backend stabilizes.
  async listParticipants(params: {
    page?: number
    per_page?: number
    status?: string
    type?: string
    q?: string
  }): Promise<Paginated<Participant>> {
    const page = params.page ?? 1
    const per_page = params.per_page ?? 20
    const status = mapUiStatusToAdmin(params.status)

    const payload = await requestJson<Paginated<Participant>>(
      buildQuery('/api/v1/admin/participants', { ...params, status: status || undefined, page, per_page }),
      { admin: true, schema: ParticipantsListSchema },
    )

    const items = payload.items.map((p) => ({
      ...p,
      status: normalizeAdminStatusToUi(p.status),
    }))

    return {
      items,
      page: payload.page,
      per_page: payload.per_page,
      total: payload.total,
    }
  },

  participantsStats(): Promise<ParticipantsStats> {
    return requestJson<ParticipantsStats>('/api/v1/admin/participants/stats', { admin: true, schema: ParticipantsStatsSchema })
  },

  liquiditySummary(params: { equivalent?: string }): Promise<LiquiditySummary> {
    const equivalent = String(params.equivalent || '').trim() || undefined
    return requestJson<LiquiditySummary>(
      buildQuery('/api/v1/admin/liquidity/summary', { equivalent }),
      { admin: true, schema: LiquiditySummarySchema },
    )
  },

  async freezeParticipant(pid: string, reason: string): Promise<{ pid: string; status: string }> {
    const r = await requestJson<AdminParticipantActionResponse>(
      `/api/v1/admin/participants/${encodeURIComponent(pid)}/freeze`,
      {
        method: 'POST',
        body: { reason },
        admin: true,
        schema: AdminParticipantActionResponseSchema,
      },
    )

    return { pid: r.pid, status: normalizeAdminStatusToUi(r.status) }
  },

  async unfreezeParticipant(pid: string, reason: string): Promise<{ pid: string; status: string }> {
    const r = await requestJson<AdminParticipantActionResponse>(
      `/api/v1/admin/participants/${encodeURIComponent(pid)}/unfreeze`,
      {
        method: 'POST',
        body: { reason },
        admin: true,
        schema: AdminParticipantActionResponseSchema,
      },
    )

    return { pid: r.pid, status: normalizeAdminStatusToUi(r.status) }
  },

  async listTrustlines(params: {
    page?: number
    per_page?: number
    equivalent?: string
    creditor?: string
    debtor?: string
    status?: string
  }): Promise<Paginated<Trustline>> {
    const page = params.page ?? 1
    const per_page = params.per_page ?? 20

    const payload = await requestJson<Paginated<Trustline>>(
      buildQuery('/api/v1/admin/trustlines', { ...params, page, per_page }),
      { admin: true, schema: TrustlinesListSchema },
    )
    return {
      items: payload.items,
      page: payload.page,
      per_page: payload.per_page,
      total: payload.total,
    }
  },

  async listAuditLog(params: {
    page?: number
    per_page?: number
    q?: string
    action?: string
    object_type?: string
    object_id?: string
  }): Promise<Paginated<AuditLogEntry>> {
    const page = params.page ?? 1
    const per_page = params.per_page ?? 50
    const payload = await requestJson<Paginated<AuditLogEntry>>(
      buildQuery('/api/v1/admin/audit-log', { ...params, page, per_page }),
      { admin: true, schema: AuditLogListSchema },
    )
    return {
      items: payload.items,
      page: payload.page,
      per_page: payload.per_page,
      total: payload.total,
    }
  },

  async listEquivalents(params: { include_inactive?: boolean }): Promise<{ items: Equivalent[] }> {
    const payload = await requestJson<{ items: Equivalent[] }>(
      buildQuery('/api/v1/admin/equivalents', {
        include_inactive: params.include_inactive ? true : undefined,
      }),
      { admin: true, schema: EquivalentsListSchema },
    )
    return { items: payload.items }
  },

  async createEquivalent(input: {
    code: string
    precision: number
    description: string
    is_active?: boolean
  }): Promise<{ created: Equivalent }> {
    const created = await requestJson<Equivalent>('/api/v1/admin/equivalents', {
      method: 'POST',
      body: {
        code: input.code,
        precision: input.precision,
        description: input.description,
        is_active: input.is_active ?? true,
      },
      admin: true,
      schema: AdminEquivalentMutationResponseSchema,
    })
    return { created }
  },

  async updateEquivalent(
    code: string,
    patch: Partial<{ precision: number; description: string }>,
  ): Promise<{ updated: Equivalent }> {
    const updated = await requestJson<Equivalent>(`/api/v1/admin/equivalents/${encodeURIComponent(code)}`, {
      method: 'PATCH',
      body: patch,
      admin: true,
      schema: AdminEquivalentMutationResponseSchema,
    })
    return { updated }
  },

  async setEquivalentActive(code: string, isActive: boolean, reason: string): Promise<{ updated: Equivalent }> {
    const updated = await requestJson<Equivalent>(`/api/v1/admin/equivalents/${encodeURIComponent(code)}`, {
      method: 'PATCH',
      body: { is_active: isActive, reason },
      admin: true,
      schema: AdminEquivalentMutationResponseSchema,
    })
    return { updated }
  },

  getEquivalentUsage(code: string): Promise<AdminEquivalentUsageResponse> {
    return requestJson(`/api/v1/admin/equivalents/${encodeURIComponent(code)}/usage`, {
      admin: true,
      schema: AdminEquivalentUsageResponseSchema,
    })
  },

  deleteEquivalent(code: string, reason: string): Promise<AdminEquivalentDeleteResponse> {
    return requestJson(`/api/v1/admin/equivalents/${encodeURIComponent(code)}`, {
      method: 'DELETE',
      body: { reason },
      admin: true,
      schema: AdminEquivalentDeleteResponseSchema,
    })
  },

  graphSnapshot(params?: { equivalent?: string; include?: string[] }): Promise<GraphSnapshot> {
    const equivalent = String(params?.equivalent || '').trim().toUpperCase()
    const include = normalizeGraphInclude(params?.include)
    const url = buildQuery('/api/v1/admin/graph/snapshot', {
      equivalent: equivalent || undefined,
      include: include || undefined,
    })
    return requestJson<GraphSnapshot>(url, { admin: true, schema: GraphSnapshotSchema }).then((s) => {
      const participants = (s.participants || []).map((p) => ({
        ...p,
        status: normalizeAdminStatusToUi(p.status),
      }))
      return { ...s, participants }
    })
  },

  graphEgo(params: { pid: string; depth?: 1 | 2; equivalent?: string; status?: string[]; include?: string[] }): Promise<GraphSnapshot> {
    const pid = String(params?.pid || '').trim()
    const depth = params?.depth ?? 1
    const equivalent = String(params?.equivalent || '').trim()
    const status = (params?.status || []).map((s) => String(s || '').trim()).filter(Boolean)
    const include = normalizeGraphInclude(params?.include)
    return requestJson<GraphSnapshot>(buildQuery('/api/v1/admin/graph/ego', { pid, depth, equivalent, status, include: include || undefined }), {
      admin: true,
      schema: GraphSnapshotSchema,
    }).then((s) => {
      const participants = (s.participants || []).map((p) => ({
        ...p,
        status: normalizeAdminStatusToUi(p.status),
      }))
      return { ...s, participants }
    })
  },

  async participantMetrics(pid: string, params?: { equivalent?: string | null }): Promise<ParticipantMetrics> {
    const eq = params?.equivalent ? String(params.equivalent) : undefined
    const pathname = `/api/v1/admin/participants/${encodeURIComponent(pid)}/metrics`
    return await requestJson<ParticipantMetrics>(buildQuery(pathname, { equivalent: eq }), {
      admin: true,
      schema: ParticipantMetricsSchema,
    })
  },
}
