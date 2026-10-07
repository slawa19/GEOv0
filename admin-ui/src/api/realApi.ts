import { ApiException } from './apiException'
import { toastApiError } from './errorToast'
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
  type AdminConfigResponse,
  type AdminEquivalentDeleteResponse,
  type AdminEquivalentUsageResponse,
  type AdminParticipantActionResponse,
  type IntegrityStatusResponse,
  type IntegritySummaryResponse,
  type IntegrityVerifyResponse,
} from './adminContracts'
import { z, type ZodTypeAny } from 'zod'
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

// Backend may serialize Decimal-like values as strings (preferred) but some environments
// might emit numbers. Normalize to string to keep the UI stable.
const DecimalString = z.union([z.string(), z.number()]).transform((v) => String(v))

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
        z.string().regex(/^debt-[0-8]$/),
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
    policy: z.record(z.string(), z.unknown()).nullable().optional(),
    close_requested_at: z.string().nullable().optional(),
  })
  .passthrough()

const EquivalentSchema = z
  .object({
    code: z.string(),
    precision: z.number(),
    description: z.string().nullable().optional(),
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
    actor_id: z.string().nullable().optional(),
    actor_role: z.string().nullable().optional(),
    action: z.string(),
    object_type: z.string().nullable().optional(),
    object_id: z.string().nullable().optional(),
    reason: z.string().nullable().optional(),
    before_state: z.unknown().optional(),
    after_state: z.unknown().optional(),
    request_id: z.string().nullable().optional(),
    ip_address: z.string().nullable().optional(),
    user_agent: z.string().nullable().optional(),
  })
  .passthrough()

// F-013-1 / T1302. The shape below is AdminGraphTransactionItem (api/openapi.yaml) and what the
// projection `_graph_fetch_transactions` actually emits - nothing more.
//
// It used to require `payload`, which neither the canon declares nor the producer sends. That was
// invisible only because the client never asked for `include=transactions`, so the array was always
// empty and no row was ever validated. The first response that carried a transaction would have been
// rejected whole as INVALID_RESPONSE and blanked the graph page - which is why the include, the
// schema and the consumer had to land as one change and not as a series with a broken middle.
//
// Required here is exactly what the canon marks required. `equivalent` and `error` are nullable and
// absence-tolerant because the canon says so: `equivalent` is payload.get("equivalent") and is null
// on a row whose payload has no such key, `error` is null on every row that did not abort.
// `.passthrough()` keeps the tail open - the mock fixture still carries `payload`/`signatures` -
// while the schema stays strict about every key it does declare.
const TransactionSchema = z
  .object({
    tx_id: z.string(),
    type: z.string(),
    state: z.string(),
    initiator_pid: z.string().nullable(), // null on a CLEARING (028 F-028-45)
    created_at: z.string(),
    updated_at: z.string(),
    equivalent: z.string().nullable().optional(),
    error: z.record(z.string(), z.unknown()).nullable().optional(),
    // ATTRIBUTION FIELDS, declared 2026-09-10 after the internal adversarial review pointed out
    // that the canon gained them and this schema did not - so they reached the consumer only
    // through `.passthrough()`, and the consumer had to launder every row through a cast. The rule
    // written above this schema ("strict about every key it does declare") had been applied to the
    // key removed and not to the three added.
    //
    // Present per type and never both: `from`/`to` on a PAYMENT, `edges` on a CLEARING, and absent
    // when the internal payload does not carry them - which is why none of them is required.
    from: z.string().optional(),
    to: z.string().optional(),
    edges: z
      .array(z.object({ debtor: z.string(), creditor: z.string() }).passthrough())
      .optional(),
  })
  .passthrough()

const GraphSnapshotSchema = z
  .object({
    participants: z.array(ParticipantSchema),
    trustlines: z.array(TrustlineSchema),
    equivalents: z.array(EquivalentSchema),
    debts: z.array(DebtSchema),
    audit_log: z.array(AuditLogEntrySchema),
    transactions: z.array(TransactionSchema),
    // F-013-1 / T1302. Which optional collections the body actually carries, and which of them hit
    // the include limit. Optional here on purpose: the canon does not list them under `required`,
    // and demanding a field the canon does not declare is the exact defect this task removes from
    // TransactionSchema above. Absent means "this server says nothing about them", which the
    // consumer must treat as "not asked" - never as "asked, and there are none".
    // The names are a CLOSED set, and the canon says so (`api/openapi.yaml`,
    // `AdminGraphSnapshotResponse.included`). Declared as an enum after external review found the
    // canon narrower than both implementations: `z.string()` here and `list[str]` on the server
    // would have accepted a fourth name silently, on a field whose entire purpose is to be trusted
    // when a consumer decides whether it may draw a conclusion.
    // 032 S5 (A-4): `incidents` left the set with the incidents surface (always empty since 019 stage 4).
    included: z.array(z.enum(['audit_log', 'transactions'])).optional(),
    truncated: z.array(z.enum(['audit_log', 'transactions'])).optional(),
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
    // 032 S5 (F-1): the balance rows are the whole answer; the participant analytics were removed.
    balance_rows: z.array(BalanceRowSchema),
  })
  .passthrough()

const ParticipantsStatsSchema = z
  .object({
    participants_by_status: z.record(z.string(), z.number()),
    participants_by_type: z.record(z.string(), z.number()),
    total_participants: z.number(),
  })
  .passthrough()

// 032 S5 (F-2, F-3): the Dashboard's row of one equivalent - its active lines and their money.
const LiquiditySummarySchema = z
  .object({
    equivalent: z.string().nullable(),
    updated_at: z.string(),
    active_trustlines: z.number(),
    // 028 F-028-37: без эквивалента сервер не суммирует деньги — `null`, а не сумма разных единиц.
    total_limit: DecimalString.nullable(),
    total_used: DecimalString.nullable(),
    total_available: DecimalString.nullable(),
  })
  .passthrough()

function paginatedSchema(itemSchema: ZodTypeAny) {
  return z
    .object({
      items: z.array(itemSchema),
      page: z.number().int().min(1),
      per_page: z.number().int().min(1).max(200),
      total: z.number().int().min(0),
    })
    .passthrough()
}

const ParticipantsListSchema = paginatedSchema(ParticipantSchema)
const TrustlinesListSchema = paginatedSchema(TrustlineSchema)
const AuditLogListSchema = paginatedSchema(AuditLogEntrySchema)
const EquivalentsListSchema = z.object({ items: z.array(EquivalentSchema) }).passthrough()

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
): Promise<T> {
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
      return undefined as T
    }

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
      })
    }

    const schema = opts?.schema
    if (!schema) return parsed as T
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
      })
    }
    return validated.data as T
  } catch (err) {
    if (opts?.toast !== false) {
      void toastApiError(err, { fallbackTitle: `${method} ${pathname} failed` })
    }
    throw err
  }
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
  health(): Promise<Record<string, unknown>> {
    return requestJson('/api/v1/health')
  },

  healthDb(): Promise<Record<string, unknown>> {
    return requestJson('/api/v1/health/db')
  },

  migrations(): Promise<Record<string, unknown>> {
    return requestJson('/api/v1/admin/migrations', { admin: true })
  },

  async getConfig(): Promise<Record<string, unknown>> {
    // Backend returns { items: [{ key, value, mutable }] }. The UI works with a flat object of the mutable keys.
    const raw = await requestJson<AdminConfigResponse>('/api/v1/admin/config', {
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
  // as text, so no generic toast is raised here.
  clearIntegrityHold(code: string, reason: string): Promise<Equivalent> {
    return requestJson<Equivalent>(`/api/v1/admin/equivalents/${encodeURIComponent(code)}/integrity-hold/clear`, {
      method: 'POST',
      body: { reason },
      admin: true,
      schema: AdminEquivalentMutationResponseSchema,
      toast: false,
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
