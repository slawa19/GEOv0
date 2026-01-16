import { mockApi } from './mockApi'
import { realApi } from './realApi'

export type ApiMode = 'mock' | 'real'

export function resolveApiMode(raw: unknown): ApiMode {
  const val = (raw ?? 'mock').toString().toLowerCase()
  return val === 'real' ? 'real' : 'mock'
}

export function apiModeFromEnv(env: unknown = import.meta.env): ApiMode {
  const rec = (env && typeof env === 'object') ? (env as Record<string, unknown>) : {}
  return resolveApiMode(rec.VITE_API_MODE)
}

export const api = apiModeFromEnv() === 'real' ? realApi : mockApi
