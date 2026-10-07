import { describe, expect, it } from 'vitest'

import { normalizeEquivalentCode } from './equivalent'

describe('normalizeEquivalentCode', () => {
  it.each([
    ['UAH', 'UAH'],
    ['uah', 'UAH'],
    ['  Uah\n', 'UAH'],
    ['EUR_2', 'EUR_2'],
  ])('%j -> %j', (input, expected) => {
    expect(normalizeEquivalentCode(input)).toBe(expected)
  })

  it('maps an absent value to the empty code, never to the text "UNDEFINED" or "NULL"', () => {
    expect(normalizeEquivalentCode(undefined)).toBe('')
    expect(normalizeEquivalentCode(null)).toBe('')
    expect(normalizeEquivalentCode('')).toBe('')
  })
})
