import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiException } from './envelope'
import { requestJson } from './realApi'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('realApi.requestJson', () => {
  it('throws ApiException(INVALID_JSON) when res.ok but body is not valid JSON', async () => {
    ;(import.meta as any).env.VITE_API_BASE_URL = ''

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('not-json', { status: 200, statusText: 'OK' })) as any,
    )

    await expect(requestJson('/api/v1/health')).rejects.toMatchObject<ApiException>({
      name: 'ApiException',
      code: 'INVALID_JSON',
    })
  })

  it('throws ApiException(INVALID_JSON) when res.ok but body is empty', async () => {
    ;(import.meta as any).env.VITE_API_BASE_URL = ''

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('', { status: 200, statusText: 'OK' })) as any,
    )

    await expect(requestJson('/api/v1/health')).rejects.toMatchObject<ApiException>({
      name: 'ApiException',
      code: 'INVALID_JSON',
    })
  })
})
