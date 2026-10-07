import { afterEach, describe, expect, it } from 'vitest'

import { setLocale } from '../i18n'
import { ApiException } from './apiException'
import { describeError } from './describeError'

afterEach(() => {
  setLocale('en')
})

function apiError(over: Partial<ConstructorParameters<typeof ApiException>[0]> = {}) {
  return new ApiException({
    status: 500,
    code: 'E010',
    message: 'GET /api/v1/admin/x -> 500: Internal error',
    details: { url: '/api/v1/admin/x' },
    requestId: 'req-42',
    ...over,
  })
}

describe('describeError (032 S6, E-1, E-3, D-11, E-18)', () => {
  it('shows the request id of an API error to the operator', () => {
    const d = describeError(apiError())
    expect(d.requestId).toBe('req-42')
    expect(d.text).toContain('Internal error')
    expect(d.text).toContain('(ref: req-42)')
  })

  it('has no ref when the error has no request id', () => {
    const d = describeError(apiError({ requestId: null }))
    expect(d.requestId).toBeNull()
    expect(d.text).not.toContain('ref')
  })

  it('reads the message of any Error and of a thrown string; the id is null', () => {
    expect(describeError(new Error('boom'))).toEqual({ text: 'boom', requestId: null })
    expect(describeError('plain').text).toBe('plain')
  })

  it('uses the fallback key only when there is no message to show', () => {
    expect(describeError(new Error(''), 'auditLog.loadFailed').text).toBe('Failed to load audit log')
    expect(describeError(new Error('real message'), 'auditLog.loadFailed').text).toBe('real message')
    expect(describeError(undefined).text).toBe('Unknown error')
  })

  it('adds the actionable hint of an authorization failure, in both languages', () => {
    const e = apiError({ status: 403, code: 'FORBIDDEN', message: 'GET /x -> 403: Forbidden', requestId: null })
    expect(describeError(e).text).toMatch(/not authorized/i)
    setLocale('ru')
    expect(describeError(e).text).toMatch(/Нет доступа/)
  })

  it('explains a network failure in the language of the interface (it was an English literal)', () => {
    const e = new TypeError('Failed to fetch')
    expect(describeError(e).text).toMatch(/failed to reach backend/i)
    setLocale('ru')
    expect(describeError(e).text).toMatch(/не удалось связаться/i)
  })

  it('tells a missing endpoint from a request that went to the dev server', () => {
    const toDevServer = apiError({ status: 404, message: 'GET /api/v1/x -> 404', details: { url: '/api/v1/x' }, requestId: null })
    expect(describeError(toDevServer).text).toMatch(/Vite/)
    const toBackend = apiError({
      status: 404,
      message: 'GET http://127.0.0.1:18000/api/v1/x -> 404',
      details: { url: 'http://127.0.0.1:18000/api/v1/x' },
      requestId: null,
    })
    expect(describeError(toBackend).text).toMatch(/Endpoint not found/)
  })
})
