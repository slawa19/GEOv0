import { describe, expect, it } from 'vitest'

import { apiModeFromEnv, effectiveApiMode, resolveApiMode } from './index'
import { API_MODE_OVERRIDE_KEY } from './apiMode'

describe('api mode selection', () => {
  it('defaults to mock', () => {
    expect(resolveApiMode(undefined)).toBe('mock')
    expect(resolveApiMode(null)).toBe('mock')
    expect(resolveApiMode('')).toBe('mock')
    expect(apiModeFromEnv({})).toBe('mock')
  })

  it('accepts real mode case-insensitively', () => {
    expect(resolveApiMode('real')).toBe('real')
    expect(resolveApiMode('REAL')).toBe('real')
    expect(resolveApiMode('ReAl')).toBe('real')
    expect(apiModeFromEnv({ VITE_API_MODE: 'real' })).toBe('real')
  })

  it('treats unknown values as mock', () => {
    expect(resolveApiMode('mock')).toBe('mock')
    expect(resolveApiMode('fixtures')).toBe('mock')
    expect(resolveApiMode(123)).toBe('mock')
    expect(apiModeFromEnv({ VITE_API_MODE: 'nope' })).toBe('mock')
  })

  it('supports localStorage override (effectiveApiMode)', () => {
    localStorage.removeItem(API_MODE_OVERRIDE_KEY)
    expect(effectiveApiMode({ VITE_API_MODE: 'mock' })).toBe('mock')

    localStorage.setItem(API_MODE_OVERRIDE_KEY, 'real')
    expect(effectiveApiMode({ VITE_API_MODE: 'mock' })).toBe('real')

    localStorage.removeItem(API_MODE_OVERRIDE_KEY)
    expect(effectiveApiMode({ VITE_API_MODE: 'real' })).toBe('real')
  })
})
