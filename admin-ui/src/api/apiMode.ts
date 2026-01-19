export type ApiMode = 'mock' | 'real'

export const API_MODE_OVERRIDE_KEY = 'admin-ui.apiModeOverride'

export function resolveApiMode(raw: unknown): ApiMode {
  const val = (raw ?? 'mock').toString().toLowerCase()
  return val === 'real' ? 'real' : 'mock'
}

export function apiModeFromEnv(env: unknown = import.meta.env): ApiMode {
  const rec = (env && typeof env === 'object') ? (env as Record<string, unknown>) : {}
  return resolveApiMode(rec.VITE_API_MODE)
}

export function apiModeFromOverride(storage: Pick<Storage, 'getItem'> | null | undefined = undefined): ApiMode | null {
  try {
    const st = storage ?? globalThis.localStorage
    if (!st) return null
    const raw = st.getItem(API_MODE_OVERRIDE_KEY)
    if (!raw) return null
    return resolveApiMode(raw)
  } catch {
    return null
  }
}

export function effectiveApiMode(env: unknown = import.meta.env): ApiMode {
  return apiModeFromOverride() ?? apiModeFromEnv(env)
}

export function setApiModeOverride(mode: ApiMode | null): void {
  try {
    if (mode === null) {
      globalThis.localStorage?.removeItem(API_MODE_OVERRIDE_KEY)
      return
    }
    globalThis.localStorage?.setItem(API_MODE_OVERRIDE_KEY, mode)
  } catch {
    // ignore
  }
}
