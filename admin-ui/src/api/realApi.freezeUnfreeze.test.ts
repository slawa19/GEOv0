import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ApiEnvelope } from './envelope'
import { normalizeAdminStatusToUi } from './statusMapping'
import { realApi } from './realApi'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('realApi.freeze/unfreeze', () => {
  it('does not mask success:false envelopes (freeze)', async () => {
    ;(import.meta as any).env.VITE_API_BASE_URL = ''

    const env: ApiEnvelope<{ pid: string; status: string }> = {
      success: false,
      error: { code: 'VALIDATION_ERROR', message: 'reason required' },
    }

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify(env), { status: 200, statusText: 'OK' })) as any,
    )

    const res = await realApi.freezeParticipant('PID_X', '')
    expect(res).toEqual(env)
  })

  it('normalizes status on success:true (unfreeze)', async () => {
    ;(import.meta as any).env.VITE_API_BASE_URL = ''

    const env: ApiEnvelope<{ pid: string; status: string }> = {
      success: true,
      data: { pid: 'PID_X', status: 'suspended' },
    }

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify(env), { status: 200, statusText: 'OK' })) as any,
    )

    const res = await realApi.unfreezeParticipant('PID_X', 'because')
    expect(res.success).toBe(true)
    if (res.success) {
      expect(res.data.pid).toBe('PID_X')
      expect(res.data.status).toBe(normalizeAdminStatusToUi('suspended'))
    }
  })
})
