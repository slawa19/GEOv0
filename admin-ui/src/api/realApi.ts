import { assertSuccess, type ApiEnvelope, ApiException } from './envelope'
import { mapUiStatusToAdmin, normalizeAdminStatusToUi } from './statusMapping'

const DEFAULT_BASE = ''
const DEFAULT_DEV_ADMIN_TOKEN = 'dev-admin-token-change-me'

function baseUrl(): string {
  // When using Vite proxy, keep base empty and call relative paths.
  return (import.meta.env.VITE_API_BASE_URL || DEFAULT_BASE).toString().replace(/\/$/, '')
}

function adminToken(): string | null {
  const key = 'admin-ui.adminToken'

  // Prefer explicit env-configured token (useful for teams / non-default backend config).
  const envTok = (import.meta.env.VITE_ADMIN_TOKEN || '').toString().trim()
  if (envTok) return envTok

  try {
    const v = (localStorage.getItem(key) || '').trim()
    if (v) return v

    // Dev ergonomics: if no token is set yet, seed the default backend token.
    // This avoids the UI spamming 403s on first run.
    if (import.meta.env.DEV) {
      try {
        localStorage.setItem(key, DEFAULT_DEV_ADMIN_TOKEN)
      } catch {
        // ignore
      }
      return DEFAULT_DEV_ADMIN_TOKEN
    }

    return null
  } catch {
    return import.meta.env.DEV ? DEFAULT_DEV_ADMIN_TOKEN : null
  }
}

async function requestJson<T>(
  pathname: string,
  opts?: {
    method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
    body?: unknown
    headers?: Record<string, string>
    admin?: boolean
  },
): Promise<ApiEnvelope<T>> {
  const url = `${baseUrl()}${pathname}`

  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(opts?.body ? { 'Content-Type': 'application/json' } : {}),
    ...(opts?.headers || {}),
  }

  if (opts?.admin) {
    const tok = adminToken()
    if (tok) headers['X-Admin-Token'] = tok
  }

  const res = await fetch(url, {
    method: opts?.method || 'GET',
    headers,
    body: opts?.body ? JSON.stringify(opts.body) : undefined,
  })

  // The backend might already return ApiEnvelope. If not, adapt here.
  const text = await res.text()
  let parsed: unknown = null
  try {
    parsed = text ? JSON.parse(text) : null
  } catch {
    parsed = null
  }

  if (res.ok && parsed && typeof parsed === 'object' && 'success' in (parsed as any)) {
    return parsed as ApiEnvelope<T>
  }

  if (!res.ok) {
    const msg = (parsed as any)?.error?.message || (parsed as any)?.message || `HTTP ${res.status}`
    const code = (parsed as any)?.error?.code || (parsed as any)?.code || 'HTTP_ERROR'
    const details = (parsed as any)?.error?.details || (parsed as any)?.details
    throw new ApiException({ status: res.status, code, message: msg, details })
  }

  // If backend returns raw payload (non-envelope), wrap it.
  return { success: true, data: parsed as T }
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

function buildQuery(pathname: string, params: Record<string, unknown>): string {
  const u = new URL(`${baseUrl()}${pathname}`)
  for (const [k, v] of Object.entries(params || {})) {
    if (v === undefined || v === null || v === '') continue
    if (Array.isArray(v)) {
      for (const item of v) {
        if (item === undefined || item === null || item === '') continue
        u.searchParams.append(k, String(item))
      }
      continue
    }
    u.searchParams.set(k, String(v))
  }
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
    const current = assertSuccess(await requestJson<Record<string, unknown>>('/api/v1/admin/feature-flags', { admin: true })) || {}
    const body = { ...current, ...patch }
    return requestJson('/api/v1/admin/feature-flags', { method: 'PATCH', body, admin: true })
  },

  integrityStatus(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return requestJson('/api/v1/integrity/status', { admin: true })
  },

  integrityVerify(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return requestJson('/api/v1/integrity/verify', { method: 'POST', body: {}, admin: true })
  },

  // The endpoints below should be aligned to OpenAPI; adjust pathname/query as backend stabilizes.
  async listParticipants(params: {
    page?: number
    per_page?: number
    status?: string
    type?: string
    q?: string
  }): Promise<ApiEnvelope<{ items: any[]; page: number; per_page: number; total: number }>> {
    const page = params.page ?? 1
    const per_page = params.per_page ?? 20
    const status = mapUiStatusToAdmin(params.status)

    const payload = await requestJson<{ items: any[]; page: number; per_page: number; total: number }>(
      buildQuery('/api/v1/admin/participants', { ...params, status: status || undefined, page, per_page }),
      { admin: true },
    )

    const backend = assertSuccess(payload)
    const items = (backend.items || []).map((p: any) => ({
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

  freezeParticipant(pid: string, reason: string): Promise<ApiEnvelope<{ pid: string; status: string }>> {
    return requestJson(`/api/v1/admin/participants/${encodeURIComponent(pid)}/freeze`, {
      method: 'POST',
      body: { reason },
      admin: true,
    }).then((r) => ({
      success: true,
      data: { pid: (r as any).data?.pid || pid, status: normalizeAdminStatusToUi((r as any).data?.status) },
    }))
  },

  unfreezeParticipant(pid: string, reason: string): Promise<ApiEnvelope<{ pid: string; status: string }>> {
    return requestJson(`/api/v1/admin/participants/${encodeURIComponent(pid)}/unfreeze`, {
      method: 'POST',
      body: { reason },
      admin: true,
    }).then((r) => ({
      success: true,
      data: { pid: (r as any).data?.pid || pid, status: normalizeAdminStatusToUi((r as any).data?.status) },
    }))
  },

  async listTrustlines(params: {
    page?: number
    per_page?: number
    equivalent?: string
    creditor?: string
    debtor?: string
    status?: string
  }): Promise<ApiEnvelope<{ items: any[]; page: number; per_page: number; total: number }>> {
    const page = params.page ?? 1
    const per_page = params.per_page ?? 20

    const payload = await requestJson<{ items: any[]; page: number; per_page: number; total: number }>(
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
  }): Promise<ApiEnvelope<{ items: any[]; page: number; per_page: number; total: number }>> {
    const page = params.page ?? 1
    const per_page = params.per_page ?? 50
    const payload = await requestJson<{ items: any[]; page: number; per_page: number; total: number }>(
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

  async listEquivalents(params: { include_inactive?: boolean }): Promise<ApiEnvelope<{ items: any[] }>> {
    const payload = await requestJson<{ items: any[] }>('/api/v1/admin/equivalents', { admin: true })
    const all = assertSuccess(payload).items || []
    const items = params.include_inactive ? all : all.filter((e: any) => e?.is_active)
    return { success: true, data: { items } }
  },

  async createEquivalent(input: {
    code: string
    precision: number
    description: string
    is_active?: boolean
  }): Promise<ApiEnvelope<{ created: any }>> {
    const created = await requestJson<any>('/api/v1/admin/equivalents', {
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
  ): Promise<ApiEnvelope<{ updated: any }>> {
    const updated = await requestJson<any>(`/api/v1/admin/equivalents/${encodeURIComponent(code)}`, {
      method: 'PATCH',
      body: patch,
      admin: true,
    })
    return { success: true, data: { updated: assertSuccess(updated) } }
  },

  async setEquivalentActive(code: string, isActive: boolean, reason: string): Promise<ApiEnvelope<{ updated: any }>> {
    const updated = await requestJson<any>(`/api/v1/admin/equivalents/${encodeURIComponent(code)}`, {
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
  }): Promise<ApiEnvelope<{ items: any[]; page: number; per_page: number; total: number }>> {
    const page = params.page ?? 1
    const per_page = params.per_page ?? 20
    return requestJson<{ items: any[]; page: number; per_page: number; total: number }>(
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

  graphSnapshot(): Promise<
    ApiEnvelope<{
      participants: any[]
      trustlines: any[]
      incidents: any[]
      equivalents: any[]
      debts: any[]
      audit_log: any[]
      transactions: any[]
    }>
  > {
    return requestJson('/api/v1/admin/graph/snapshot', { admin: true }).then((r) => {
      const s = assertSuccess(r) as any
      const participants = (s.participants || []).map((p: any) => ({
        ...p,
        status: normalizeAdminStatusToUi(p.status),
      }))
      return { success: true, data: { ...s, participants } }
    })
  },

  graphEgo(params: { pid: string; depth?: 1 | 2; equivalent?: string; status?: string[] }): Promise<
    ApiEnvelope<{
      participants: any[]
      trustlines: any[]
      incidents: any[]
      equivalents: any[]
      debts: any[]
      audit_log: any[]
      transactions: any[]
    }>
  > {
    const pid = String(params?.pid || '').trim()
    const depth = params?.depth ?? 1
    const equivalent = String(params?.equivalent || '').trim()
    const status = (params?.status || []).map((s) => String(s || '').trim()).filter(Boolean)
    return requestJson(buildQuery('/api/v1/admin/graph/ego', { pid, depth, equivalent, status }), { admin: true }).then((r) => {
      const s = assertSuccess(r) as any
      const participants = (s.participants || []).map((p: any) => ({
        ...p,
        status: normalizeAdminStatusToUi(p.status),
      }))
      return { success: true, data: { ...s, participants } }
    })
  },

  clearingCycles(params?: { participant_pid?: string }): Promise<ApiEnvelope<any>> {
    const participant_pid = String(params?.participant_pid || '').trim()
    return requestJson(buildQuery('/api/v1/admin/clearing/cycles', { participant_pid }), { admin: true })
  },

  async participantMetrics(pid: string, params?: { equivalent?: string | null; threshold?: string | number | null }) {
    const eq = params?.equivalent ? String(params.equivalent) : undefined
    const thrRaw = params?.threshold
    const threshold = thrRaw === null || thrRaw === undefined || thrRaw === '' ? undefined : Number(thrRaw)

    const pathname = `/api/v1/admin/participants/${encodeURIComponent(pid)}/metrics`
    const url = buildQuery(pathname, { equivalent: eq, threshold: Number.isFinite(threshold) ? threshold : undefined })
    return await requestJson(url, { admin: true })
  },
}
