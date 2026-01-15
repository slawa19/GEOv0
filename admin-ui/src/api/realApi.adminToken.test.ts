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
    ;(import.meta as any).env.VITE_API_BASE_URL = ''
    const prevForceProd = (globalThis as any).__GEO_ADMINUI_FORCE_PROD__
    ;(globalThis as any).__GEO_ADMINUI_FORCE_PROD__ = true

    vi.spyOn(console, 'warn').mockImplementation(() => {})

    localStorage.setItem('admin-ui.adminToken', 'dev-admin-token-change-me')

    const fetchSpy = vi.fn(async () => new Response('{}', { status: 200, statusText: 'OK' }))
    vi.stubGlobal('fetch', fetchSpy as any)

    try {
      await expect(requestJson('/api/v1/admin/feature-flags', { admin: true })).rejects.toThrow(
        /DEFAULT_DEV_ADMIN_TOKEN/i,
      )
      expect(fetchSpy).not.toHaveBeenCalled()
    } finally {
      ;(globalThis as any).__GEO_ADMINUI_FORCE_PROD__ = prevForceProd
    }
  })
})
