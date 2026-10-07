import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiException } from './apiException'
import { normalizeAdminStatusToUi } from './statusMapping'
import { realApi } from './realApi'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('realApi.freeze/unfreeze', () => {
  it('turns a refused freeze into ApiException with the server code, not a value', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''

    const body = { error: { code: 'E009', message: 'reason required' } }
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status: 400, statusText: 'Bad Request' }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    const failure = realApi.freezeParticipant('PID_X', '')
    await expect(failure).rejects.toBeInstanceOf(ApiException)
    await expect(failure).rejects.toMatchObject({ status: 400, code: 'E009' })
  })

  it('normalizes the status of the returned participant (unfreeze)', async () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''

    const body = { pid: 'PID_X', status: 'suspended' }
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status: 200, statusText: 'OK' }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    await expect(realApi.unfreezeParticipant('PID_X', 'because')).resolves.toEqual({
      pid: 'PID_X',
      status: normalizeAdminStatusToUi('suspended'),
    })
  })
})
