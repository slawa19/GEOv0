import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/http'
import { extractErrorMessage } from './errorMessage'

describe('extractErrorMessage and the request id (034 F-034-15)', () => {
  it('appends (ref: <id>) once for an error that carries a request id', () => {
    const e = new ApiError('HTTP 500  for /x', { status: 500, requestId: 'req-1' })
    expect(extractErrorMessage(e)).toBe('HTTP 500  for /x (ref: req-1)')
  })

  it('does not append the reference a second time when the message already shows it', () => {
    const e = new ApiError('HTTP 500 (ref: req-1)', { status: 500, requestId: 'req-1' })
    const text = extractErrorMessage(e)
    expect(text).toBe('HTTP 500 (ref: req-1)')
    expect(text.match(/\(ref:/g)).toHaveLength(1)
  })

  it('works for a plain object error that carries requestId (an Interact action error)', () => {
    expect(extractErrorMessage({ message: 'refused', requestId: 'req-2' })).toBe('refused (ref: req-2)')
  })

  it('anti-vacuum: no id, an empty id or a non-string id add nothing', () => {
    expect(extractErrorMessage(new ApiError('boom', { status: 500 }))).toBe('boom')
    expect(extractErrorMessage({ message: 'boom', requestId: '  ' })).toBe('boom')
    expect(extractErrorMessage({ message: 'boom', requestId: 42 })).toBe('boom')
    expect(extractErrorMessage(new Error('plain'))).toBe('plain')
    expect(extractErrorMessage('text')).toBe('text')
  })
})
