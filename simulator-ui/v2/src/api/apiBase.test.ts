import { describe, expect, it } from 'vitest'
import { normalizeApiBase } from './apiBase'

describe('api/apiBase', () => {
  it('uses fallback when empty', () => {
    expect(normalizeApiBase('')).toBe('/api/v1')
    expect(normalizeApiBase('   ')).toBe('/api/v1')
  })

  it('normalizes backslashes to slashes', () => {
    expect(normalizeApiBase('/api\\v1')).toBe('/api/v1')
    expect(normalizeApiBase('http://127.0.0.1:8000\\api\\v1')).toBe('http://127.0.0.1:8000/api/v1')
  })

  it('adds /api/v1 when given only an origin', () => {
    expect(normalizeApiBase('http://127.0.0.1:8000')).toBe('http://127.0.0.1:8000/api/v1')
    expect(normalizeApiBase('http://127.0.0.1:8000/')).toBe('http://127.0.0.1:8000/api/v1')
  })

  it('expands /api to /api/v1', () => {
    expect(normalizeApiBase('/api')).toBe('/api/v1')
    expect(normalizeApiBase('http://127.0.0.1:8000/api')).toBe('http://127.0.0.1:8000/api/v1')
  })

  it('adds leading slash for path-like inputs', () => {
    expect(normalizeApiBase('api/v1')).toBe('/api/v1')
    expect(normalizeApiBase('simulator')).toBe('/simulator')
  })

  it('keeps regular paths and urls', () => {
    expect(normalizeApiBase('/api/v1')).toBe('/api/v1')
    expect(normalizeApiBase('http://127.0.0.1:8000/api/v1')).toBe('http://127.0.0.1:8000/api/v1')
    expect(normalizeApiBase('http://127.0.0.1:8000/api/v1/')).toBe('http://127.0.0.1:8000/api/v1')
  })
})
