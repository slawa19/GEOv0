export type HttpConfig = {
  apiBase: string
  accessToken?: string | null
}

function isJwtLike(token: string): boolean {
  // Very small heuristic: JWT is typically three base64url segments separated by dots.
  // We use it to route auth to either Authorization: Bearer <jwt> or X-Admin-Token: <token>.
  const t = token.trim()
  return t.split('.').length === 3
}

export function applyAuthHeaders(headers: Headers, token?: string | null): void {
  const t = String(token ?? '').trim()
  if (!t) return

  if (isJwtLike(t)) {
    headers.set('Authorization', `Bearer ${t}`)
    return
  }

  headers.set('X-Admin-Token', t)
}

export function authHeaders(token?: string | null): Record<string, string> {
  const t = String(token ?? '').trim()
  if (!t) return {}
  return isJwtLike(t) ? { Authorization: `Bearer ${t}` } : { 'X-Admin-Token': t }
}

export class ApiError extends Error {
  status: number
  bodyText?: string

  constructor(message: string, opts: { status: number; bodyText?: string }) {
    super(message)
    this.name = 'ApiError'
    this.status = opts.status
    this.bodyText = opts.bodyText
  }
}

function joinUrl(base: string, path: string): string {
  const b = base.replace(/\\/g, '/').replace(/\/+$/, '')
  const p = path.startsWith('/') ? path : `/${path}`
  return `${b}${p}`
}

export async function httpJson<T>(cfg: HttpConfig, path: string, init?: RequestInit): Promise<T> {
  const url = joinUrl(cfg.apiBase, path)

  const headers = new Headers(init?.headers)
  headers.set('Accept', 'application/json')
  if (init?.body != null) headers.set('Content-Type', 'application/json')
  applyAuthHeaders(headers, cfg.accessToken)

  // credentials: 'include' ensures cookies (e.g. geo_sim_sid) are sent with every request.
  // This enables anonymous-visitor cookie-auth when no accessToken is provided.
  const res = await fetch(url, { credentials: 'include', ...init, headers })

  if (!res.ok) {
    let bodyText: string | undefined
    try {
      bodyText = await res.text()
    } catch {
      bodyText = undefined
    }
    throw new ApiError(`HTTP ${res.status} ${res.statusText} for ${path}`, { status: res.status, bodyText })
  }

  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export async function httpText(cfg: HttpConfig, path: string, init?: RequestInit): Promise<string> {
  const url = joinUrl(cfg.apiBase, path)

  const headers = new Headers(init?.headers)
  headers.set('Accept', init?.headers && new Headers(init.headers).get('Accept') ? (new Headers(init.headers).get('Accept') as string) : '*/*')
  applyAuthHeaders(headers, cfg.accessToken)

  // credentials: 'include' ensures cookies (e.g. geo_sim_sid) are sent with every request.
  const res = await fetch(url, { credentials: 'include', ...init, headers })

  if (!res.ok) {
    let bodyText: string | undefined
    try {
      bodyText = await res.text()
    } catch {
      bodyText = undefined
    }
    throw new ApiError(`HTTP ${res.status} ${res.statusText} for ${path}`, { status: res.status, bodyText })
  }

  return await res.text()
}

export function httpUrl(cfg: HttpConfig, path: string): string {
  return joinUrl(cfg.apiBase, path)
}
