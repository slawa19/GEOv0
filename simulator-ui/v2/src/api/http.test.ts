import { describe, expect, it } from 'vitest'
import { applyAuthHeaders, authHeaders, httpUrl } from './http'
import { normalizeApiBase } from './apiBase'

describe('api/http authHeaders', () => {
  it('routes JWT-like tokens to Authorization header', () => {
    const h = authHeaders('a.b.c')
    expect(h).toEqual({ Authorization: 'Bearer a.b.c' })

    const headers = new Headers()
    applyAuthHeaders(headers, 'a.b.c')
    expect(headers.get('Authorization')).toBe('Bearer a.b.c')
    expect(headers.get('X-Admin-Token')).toBeNull()
  })

  it('routes non-JWT tokens to X-Admin-Token header', () => {
    const h = authHeaders('dev-admin-token-change-me')
    expect(h).toEqual({ 'X-Admin-Token': 'dev-admin-token-change-me' })

    const headers = new Headers()
    applyAuthHeaders(headers, 'dev-admin-token-change-me')
    expect(headers.get('X-Admin-Token')).toBe('dev-admin-token-change-me')
    expect(headers.get('Authorization')).toBeNull()
  })

  it('ignores empty tokens', () => {
    expect(authHeaders('')).toEqual({})

    const headers = new Headers()
    applyAuthHeaders(headers, '')
    expect(headers.get('Authorization')).toBeNull()
    expect(headers.get('X-Admin-Token')).toBeNull()
  })
})

describe('api/http httpUrl', () => {
  it('normalizes backslashes in apiBase', () => {
    const url = httpUrl({ apiBase: '/api\\v1' }, '/simulator/scenarios')
    expect(url).toBe('/api/v1/simulator/scenarios')
  })

  it('works with normalized origin apiBase', () => {
    const apiBase = normalizeApiBase('http://127.0.0.1:8000')
    const url = httpUrl({ apiBase }, '/simulator/scenarios')
    expect(url).toBe('http://127.0.0.1:8000/api/v1/simulator/scenarios')
  })
})
