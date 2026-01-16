import { afterEach, describe, expect, it, vi } from 'vitest'

import { requestJson } from './realApi'

afterEach(() => {
  vi.unstubAllGlobals()
  // cleanup token side effects
  try {
    localStorage.removeItem('admin-ui.adminToken')
  } catch {
    // ignore
  }
})

describe('realApi admin token safety', () => {
  it('refuses DEFAULT_DEV_ADMIN_TOKEN in PROD builds (from localStorage) before fetch', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''

    const g = globalThis as unknown as { __GEO_ADMINUI_FORCE_PROD__?: unknown }
    const prevForceProd = g.__GEO_ADMINUI_FORCE_PROD__
    g.__GEO_ADMINUI_FORCE_PROD__ = true

    vi.spyOn(console, 'warn').mockImplementation(() => {})

    localStorage.setItem('admin-ui.adminToken', 'dev-admin-token-change-me')

    const fetchSpy = vi.fn(async () => new Response('{}', { status: 200, statusText: 'OK' }))
    vi.stubGlobal('fetch', fetchSpy as unknown as typeof fetch)

    try {
      await expect(requestJson('/api/v1/admin/feature-flags', { admin: true })).rejects.toThrow(
        /DEFAULT_DEV_ADMIN_TOKEN/i,
      )
      expect(fetchSpy).not.toHaveBeenCalled()
    } finally {
      g.__GEO_ADMINUI_FORCE_PROD__ = prevForceProd
    }
  })
})
