import { describe, expect, it, vi, afterEach } from 'vitest'

import { __resetApiErrorToastForTests, toastApiError } from './errorToast'
import { ApiException } from './envelope'

vi.mock('element-plus', () => {
  return {
    ElMessage: {
      error: vi.fn(),
    },
  }
})

afterEach(() => {
  __resetApiErrorToastForTests()
  vi.clearAllMocks()
})

describe('toastApiError', () => {
  it('shows a formatted ApiException message via Element Plus', async () => {
    const { ElMessage } = await import('element-plus')

    const e = new ApiException({
      status: 403,
      code: 'FORBIDDEN',
      message: 'GET /api/v1/admin/x -> 403: Forbidden',
      details: { url: '/api/v1/admin/x' },
    })

    await toastApiError(e)
    expect(ElMessage.error).toHaveBeenCalledTimes(1)
    expect(ElMessage.error).toHaveBeenCalledWith(expect.stringMatching(/not authorized/i))
  })

  it('dedupes repeated messages within a short window', async () => {
    const { ElMessage } = await import('element-plus')

    vi.spyOn(Date, 'now').mockReturnValue(1000)
    await toastApiError(new Error('boom'))

    vi.spyOn(Date, 'now').mockReturnValue(1500)
    await toastApiError(new Error('boom'))

    expect(ElMessage.error).toHaveBeenCalledTimes(1)
  })
})
