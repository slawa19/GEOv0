import { ApiException } from './envelope'

function safeString(v: unknown): string {
  if (v === null || v === undefined) return ''
  try {
    return String(v)
  } catch {
    return ''
  }
}

function getDetail(obj: unknown, key: string): unknown {
  if (!obj || typeof obj !== 'object') return undefined
  return (obj as any)[key]
}

export function formatApiError(e: unknown): { title: string; hint?: string } {
  // ApiException is thrown by our API clients for HTTP errors.
  if (e instanceof ApiException) {
    const url = safeString(getDetail(e.details, 'url'))
    const status = e.status

    // Default title is already decorated in realApi.ts (method + url + status + server msg)
    let title = e.message || `HTTP ${status}`

    // Provide a more actionable hint for common dev misconfigs.
    let hint: string | undefined

    if (status === 404) {
      if (url.includes('localhost:5173') || url.startsWith('/api/')) {
        hint =
          'Looks like the request went to the Admin UI dev server (Vite) instead of the backend. Check admin-ui/.env.local: VITE_API_MODE=real and VITE_API_BASE_URL=http://127.0.0.1:18000, then restart the UI.'
      } else {
        hint = 'Endpoint not found on backend. Check backend version and the API base URL.'
      }
    }

    if (status === 401 || status === 403) {
      hint =
        'Not authorized. Ensure the admin token is set (localStorage key "admin-ui.adminToken" or VITE_ADMIN_TOKEN) and matches backend config.'
    }

    return hint ? { title, hint } : { title }
  }

  // Fetch/network errors (browser throws TypeError: Failed to fetch)
  if (e instanceof Error) {
    const msg = (e.message || '').trim()
    if (msg.toLowerCase().includes('failed to fetch')) {
      return {
        title: 'Network error: failed to reach backend',
        hint: 'Ensure backend is running and reachable at VITE_API_BASE_URL. If running locally, scripts/run_local.ps1 should expose http://127.0.0.1:18000.',
      }
    }
    return { title: msg || 'Unknown error' }
  }

  const fallback = safeString(e).trim()
  return { title: fallback || 'Unknown error' }
}
