import { afterEach, describe, expect, it } from 'vitest'

import { setLocale } from '../i18n'
import { ApiException } from './apiException'
import { describeError } from './describeError'

/**
 * 054-D-16: the server's `ErrorEnvelope.code` (`E008`, `E010`, ...) reached `ApiException.code` and was shown nowhere:
 * the operator saw the message and the ref, but not the code the API reference and the log are indexed by. It is
 * written next to the ref, once, in `describeError` - so every caller shows it.
 */

afterEach(() => setLocale('en'))

describe('054-D-16 describeError shows the error code', () => {
  it('message, code and ref, in that order', () => {
    const e = new ApiException({
      status: 409,
      code: 'E008',
      message: 'DELETE /api/v1/admin/equivalents/UAH -> 409 Conflict: Equivalent in use',
      details: { url: '/api/v1/admin/equivalents/UAH' },
      requestId: 'req-7',
    })
    expect(describeError(e).text).toBe('DELETE /api/v1/admin/equivalents/UAH -> 409 Conflict: Equivalent in use [E008] (ref: req-7)')
  })

  it('code without a ref; the hint stays before the code; same in Russian', () => {
    const e = new ApiException({ status: 403, code: 'E002', message: 'GET /x -> 403: Forbidden', requestId: null })
    expect(describeError(e).text).toMatch(/^GET \/x -> 403: Forbidden — .+ \[E002\]$/)
    setLocale('ru')
    expect(describeError(e).text).toMatch(/Нет доступа.* \[E002\]$/)
  })

  it('a client-side code (no answer from the server) is shown the same way', () => {
    const e = new ApiException({ status: 0, code: 'TIMEOUT', message: 'GET /x timed out', requestId: null })
    expect(describeError(e).text).toBe('GET /x timed out [TIMEOUT]')
  })

  it('an empty code adds nothing', () => {
    const e = new ApiException({ status: 500, code: '', message: 'boom', requestId: 'r1' })
    expect(describeError(e).text).toBe('boom (ref: r1)')
  })

  it('a plain Error has no code', () => {
    expect(describeError(new Error('boom')).text).toBe('boom')
  })
})
