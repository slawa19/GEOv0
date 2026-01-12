import { mockApi } from './mockApi'
import { realApi } from './realApi'

export type ApiMode = 'mock' | 'real'

function apiMode(): ApiMode {
  const raw = (import.meta.env.VITE_API_MODE || 'mock').toString().toLowerCase()
  return raw === 'real' ? 'real' : 'mock'
}

export const api = apiMode() === 'real' ? realApi : mockApi
