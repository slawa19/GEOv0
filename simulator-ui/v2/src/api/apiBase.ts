const DEFAULT_API_PREFIX = '/api/v1'

function stripTrailingSlashes(v: string): string {
  return v.replace(/\/+$/g, '')
}

export function normalizeApiBase(input: string, fallback = DEFAULT_API_PREFIX): string {
  const raw = String(input ?? '').trim().replace(/\\/g, '/')
  if (!raw) return fallback

  const v = stripTrailingSlashes(raw)

  // If user passed a full origin (http://host:port) without a path, default to /api/v1.
  if (v.startsWith('http://') || v.startsWith('https://')) {
    try {
      const u = new URL(v)
      const path = stripTrailingSlashes(u.pathname || '')
      if (!path || path === '/') {
        u.pathname = DEFAULT_API_PREFIX
        return stripTrailingSlashes(u.toString())
      }
      if (path === '/api') {
        u.pathname = DEFAULT_API_PREFIX
        return stripTrailingSlashes(u.toString())
      }
      return stripTrailingSlashes(u.toString())
    } catch {
      return v
    }
  }

  // Relative paths: normalize some common user inputs.
  if (v === '/') return DEFAULT_API_PREFIX
  if (v === '/api') return DEFAULT_API_PREFIX
  if (v === 'api') return DEFAULT_API_PREFIX
  if (v === 'api/v1') return DEFAULT_API_PREFIX

  // Ensure a leading slash for path-like inputs.
  if (!v.startsWith('/')) return `/${v}`
  return v
}
