import { describe, expect, it } from 'vitest'

import { apiModeFromEnv, resolveApiMode } from './index'

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
})
