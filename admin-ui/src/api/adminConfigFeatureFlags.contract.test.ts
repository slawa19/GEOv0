import { afterEach, describe, expect, it, vi } from 'vitest'

import { assertSuccess } from './envelope'
import { __resetMockApiForTests, mockApi } from './mockApi'
import { realApi } from './realApi'

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

function useMockApiEnv(fixtures: { config?: unknown; featureFlags?: unknown }) {
  const url = new URL('http://localhost/?scenario=happy')
  vi.stubGlobal('window', { ...window, location: url } as unknown as Window)

  const scenario = { name: 'happy', latency_ms: { min: 0, max: 0 } }
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const requestUrl = String(input)
    if (requestUrl.includes('/admin-fixtures/v1/scenarios/happy.json')) return jsonResponse(scenario)
    if (requestUrl.includes('/admin-fixtures/v1/datasets/config.json')) return jsonResponse(fixtures.config)
    if (requestUrl.includes('/admin-fixtures/v1/datasets/feature-flags.json')) {
      return jsonResponse(fixtures.featureFlags)
    }
    return new Response('Not Found', { status: 404, statusText: 'Not Found' })
  })
  vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
}

afterEach(() => {
  vi.unstubAllGlobals()
  __resetMockApiForTests()
})

describe('Admin config and feature-flag contracts', () => {
  it('preserves the real config flattening facade after validating the wire response', async () => {
    useRealApiEnv()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          success: true,
          data: {
            items: [
              { key: 'routing.max_hops', value: 6, mutable: true },
              { key: 'clearing.enabled', value: false, mutable: false },
            ],
          },
        }),
      ) as unknown as typeof fetch,
    )

    await expect(realApi.getConfig()).resolves.toEqual({
      success: true,
      data: { 'routing.max_hops': 6, 'clearing.enabled': false },
    })
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
      call: () => realApi.patchConfig({ 'routing.max_hops': 6 }),
    },
    {
      name: 'feature flags GET',
      data: { multipath_enabled: true, clearing_enabled: true },
      call: () => realApi.getFeatureFlags(),
    },
    {
      name: 'feature flags PATCH',
      data: {
        multipath_enabled: true,
        full_multipath_enabled: false,
        clearing_enabled: true,
        audit_log_enabled: true,
      },
      call: () => realApi.patchFeatureFlags({ multipath_enabled: true }),
    },
  ])('rejects malformed real $name 2xx data with INVALID_RESPONSE', async ({ data, call }) => {
    useRealApiEnv()
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ success: true, data })) as unknown as typeof fetch)

    await expect(call()).rejects.toMatchObject({
      name: 'ApiException',
      code: 'INVALID_RESPONSE',
    })
  })

  it('validates mock outputs and returns the canonical full feature-flag response', async () => {
    useMockApiEnv({
      config: { routing: { max_hops: 6 } },
      featureFlags: {
        multipath_enabled: true,
        full_multipath_enabled: false,
        clearing_enabled: true,
      },
    })

    expect(assertSuccess(await mockApi.getConfig())).toEqual({ routing: { max_hops: 6 } })
    expect(assertSuccess(await mockApi.patchConfig({ clearing: { max_cycle_len: 6 } }))).toEqual({
      updated: ['clearing'],
    })

    expect(assertSuccess(await mockApi.getFeatureFlags())).toEqual({
      multipath_enabled: true,
      full_multipath_enabled: false,
      clearing_enabled: true,
    })
    expect(
      assertSuccess(
        await mockApi.patchFeatureFlags({
          clearing_enabled: false,
          reason: 'operator change',
          audit_log_enabled: true,
        }),
      ),
    ).toEqual({
      multipath_enabled: true,
      full_multipath_enabled: false,
      clearing_enabled: false,
    })
  })

  it.each([
    {
      name: 'config fixture',
      fixtures: { config: [], featureFlags: undefined },
      call: () => mockApi.getConfig(),
    },
    {
      name: 'feature-flag fixture',
      fixtures: {
        config: undefined,
        featureFlags: { multipath_enabled: true, clearing_enabled: true },
      },
      call: () => mockApi.getFeatureFlags(),
    },
  ])('rejects malformed mock $name with INVALID_RESPONSE', async ({ fixtures, call }) => {
    useMockApiEnv(fixtures)

    await expect(call()).rejects.toMatchObject({
      name: 'ApiException',
      code: 'INVALID_RESPONSE',
    })
  })
})
