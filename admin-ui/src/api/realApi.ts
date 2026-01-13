import { assertSuccess, type ApiEnvelope, ApiException } from './envelope'

const DEFAULT_BASE = ''

function baseUrl(): string {
  // When using Vite proxy, keep base empty and call relative paths.
  return (import.meta.env.VITE_API_BASE_URL || DEFAULT_BASE).toString().replace(/\/$/, '')
}

function adminToken(): string | null {
  const key = 'admin-ui.adminToken'
  try {
    const v = (localStorage.getItem(key) || '').trim()
    return v || null
  } catch {
    return null
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

function normalizeAdminStatusToUi(status: string | null | undefined): string {
  const v = String(status || '').trim().toLowerCase()
  if (v === 'suspended') return 'frozen'
  if (v === 'deleted') return 'banned'
  return v || ''
}

function mapUiStatusToAdmin(status: string | null | undefined): string | null {
  const v = String(status || '').trim().toLowerCase()
  if (!v) return null
  if (v === 'frozen') return 'suspended'
  if (v === 'banned') return 'deleted'
  return v
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
    u.searchParams.set(k, String(v))
  }
  return u.pathname + u.search
}

function notImplemented(message: string): never {
  throw new ApiException({ status: 501, code: 'NOT_IMPLEMENTED', message })
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
    return requestJson('/api/v1/integrity/status')
  },

  integrityVerify(): Promise<ApiEnvelope<Record<string, unknown>>> {
    return requestJson('/api/v1/integrity/verify', { method: 'POST', body: {} })
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

    const payload = await requestJson<{ items: any[] }>(
      buildQuery('/api/v1/admin/participants', { ...params, status: status || undefined, page, per_page }),
      { admin: true },
    )

    const items = (assertSuccess(payload).items || []).map((p: any) => ({
      ...p,
      status: normalizeAdminStatusToUi(p.status),
    }))

    return {
      success: true,
      data: {
        items,
        page,
        per_page,
        total: bestEffortTotal(page, per_page, items.length),
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

    const payload = await requestJson<{ items: any[] }>(
      buildQuery('/api/v1/admin/trustlines', { ...params, page, per_page }),
      { admin: true },
    )
    const items = assertSuccess(payload).items || []
    return {
      success: true,
      data: {
        items,
        page,
        per_page,
        total: bestEffortTotal(page, per_page, items.length),
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
    const payload = await requestJson<{ items: any[] }>(
      buildQuery('/api/v1/admin/audit-log', { ...params, page, per_page }),
      { admin: true },
    )
    const items = assertSuccess(payload).items || []
    return {
      success: true,
      data: {
        items,
        page,
        per_page,
        total: bestEffortTotal(page, per_page, items.length),
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
    void params
    notImplemented('Incidents are fixtures-only for now; backend /api/v1/admin/incidents is not available.')
  },

  abortTx(txId: string, reason: string): Promise<ApiEnvelope<{ tx_id: string; status: 'aborted' }>> {
    void txId
    void reason
    notImplemented('Abort transaction is not available in backend yet.')
  },
}
