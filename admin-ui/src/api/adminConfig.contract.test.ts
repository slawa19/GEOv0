import { afterEach, describe, expect, it, vi } from 'vitest'

import { __resetApiErrorToastForTests } from './errorToast'
import { realApi } from './realApi'

// 032 S4 (2026-10-07): the feature-flag client methods and the mock-client cases of this file were
// removed with `/admin/feature-flags` and the mock client. What the mock cases asserted about the server
// (partial patch, refusal of unknown/start-only/wrong-type keys) is held by the backend:
// tests/unit/test_admin_config_patch_atomicity.py and tests/integration/test_p029_s1_operations.py.

vi.mock('element-plus', () => ({
  ElMessage: {
    error: vi.fn(),
  },
}))

function jsonResponse(data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status: 200,
    statusText: 'OK',
    headers: { 'Content-Type': 'application/json' },
  })
}

function useRealApiEnv() {
  const meta = import.meta as unknown as { env: Record<string, unknown> }
  meta.env.VITE_API_BASE_URL = ''
  meta.env.VITE_ADMIN_TOKEN = 'test-token'
}

function runtimeConfig(overrides: Record<string, unknown> = {}) {
  return {
    RATE_LIMIT_ENABLED: true,
    ROUTING_MAX_HOPS: 6,
    ROUTING_MAX_PATHS: 3,
    FEATURE_FLAGS_MULTIPATH_ENABLED: true,
    FEATURE_FLAGS_FULL_MULTIPATH_ENABLED: false,
    CLEARING_ENABLED: true,
    ...overrides,
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  __resetApiErrorToastForTests()
})

describe('Admin config contracts', () => {
  it('flattens the wire response to the keys the backend lets an admin change', async () => {
    useRealApiEnv()
    const items = Object.entries<unknown>(runtimeConfig()).map(([key, value]) => ({ key, value, mutable: true }))
    // 029 F-029-4: a key read only at start comes with `mutable: false` and is not offered for editing.
    items.push({ key: 'LOG_LEVEL', value: 'INFO', mutable: false }, { key: 'RECOVERY_ENABLED', value: true, mutable: false })
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ items })) as unknown as typeof fetch)

    await expect(realApi.getConfig()).resolves.toEqual(runtimeConfig())
  })

  it.each([
    {
      name: 'config GET',
      data: { items: [{ key: 'routing.max_hops', value: 6 }] },
      call: () => realApi.getConfig(),
    },
    {
      name: 'config PATCH',
      data: { updated: 'routing.max_hops' },
      call: () => realApi.patchConfig({ ROUTING_MAX_HOPS: 6 }),
    },
  ])('rejects malformed real $name 2xx data with INVALID_RESPONSE', async ({ data, call }) => {
    useRealApiEnv()
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(data)) as unknown as typeof fetch)

    await expect(call()).rejects.toMatchObject({
      name: 'ApiException',
      code: 'INVALID_RESPONSE',
    })
  })
})
