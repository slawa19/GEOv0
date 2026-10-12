/**
 * 039 slice A (R11): `sameEquivalent` - the one comparison of two equivalents, with the semantics of the backend's
 * `canonical_code()` (`app/core/equivalents.py`: `strip().upper()`).
 *
 * What this file does NOT prove: that every site of the product calls the function (the sites are walked in
 * `composables/sameEquivalent.sites.p039.test.ts` and `p039-repro/r11-*.test.ts`), nor that a code is valid.
 */
import { describe, expect, it } from 'vitest'

import { EQUIVALENT_CODE_RE, canonicalEquivalent, sameEquivalent } from './equivalents'

describe('canonicalEquivalent - trim + upper case, nothing else', () => {
  it('trims and upper-cases; a missing value is the empty string', () => {
    expect(canonicalEquivalent(' uah ')).toBe('UAH')
    expect(canonicalEquivalent('\tHour\n')).toBe('HOUR')
    expect(canonicalEquivalent(null)).toBe('')
    expect(canonicalEquivalent(undefined)).toBe('')
    expect(canonicalEquivalent('   ')).toBe('')
  })

  it('removes no character and maps no alias: inner blanks, "_" and digits stay', () => {
    expect(canonicalEquivalent('AA_B')).toBe('AA_B')
    expect(canonicalEquivalent('a 1')).toBe('A 1')
    expect(canonicalEquivalent('UAH1')).toBe('UAH1')
  })
})

describe('sameEquivalent', () => {
  it('whitespace and case on EITHER side do not matter', () => {
    for (const [a, b] of [
      ['UAH', 'UAH'], ['UAH ', 'UAH'], ['UAH', ' UAH'], [' UAH ', '\tUAH\n'], ['uah', 'UAH'], ['UAH', 'Uah'], [' uah', 'UAH '],
      ['aa_b', 'AA_B'], ['hour', ' HOUR'],
    ] as const) {
      expect(sameEquivalent(a, b), `"${a}" vs "${b}"`).toBe(true)
      expect(sameEquivalent(b, a), `"${b}" vs "${a}"`).toBe(true)
    }
  })

  it('INVARIANT: two different valid codes are never equal - also when they differ only by a character a sloppy normaliser would strip', () => {
    const pairs = [['AA_B', 'AAB'], ['A1', 'A_1'], ['UAH', 'UAH1'], ['UAH', 'USD'], ['A', 'AA'], ['A_', 'A'], ['_A', 'A'], ['0', 'O']] as const
    for (const [a, b] of pairs) {
      // Counter-check of the test itself: both sides ARE valid codes, so the pair is a real one.
      expect(EQUIVALENT_CODE_RE.test(a) && EQUIVALENT_CODE_RE.test(b), `"${a}" and "${b}" are valid codes`).toBe(true)
      expect(sameEquivalent(a, b), `"${a}" vs "${b}"`).toBe(false)
      expect(sameEquivalent(b, a), `"${b}" vs "${a}"`).toBe(false)
      expect(sameEquivalent(` ${a.toLowerCase()} `, b), `" ${a.toLowerCase()} " vs "${b}"`).toBe(false)
    }
  })

  it('exhaustive over a small alphabet: equal iff the trimmed upper-cased spellings are equal', () => {
    const codes = ['A', 'B', 'AB', 'A_B', 'AB_', 'A1', 'A_1', '1A']
    const spell = (c: string) => [c, c.toLowerCase(), ` ${c}`, `${c} `, `\t${c.toLowerCase()}\n`]
    let equal = 0
    let different = 0
    for (const x of codes) for (const y of codes) for (const sx of spell(x)) for (const sy of spell(y)) {
      expect(sameEquivalent(sx, sy), `"${sx}" vs "${sy}"`).toBe(x === y)
      if (x === y) equal += 1
      else different += 1
    }
    // Anti-vacuum: both outcomes were really produced.
    expect({ equal, different }).toEqual({ equal: 8 * 25, different: 56 * 25 })
  })

  it('EMPTY IS NOT AN EQUIVALENT: a missing or blank side equals nothing - not a code, and not another missing side', () => {
    const empties = ['', '   ', '\t', null, undefined]
    for (const e of empties) {
      expect(sameEquivalent(e, 'UAH'), `${JSON.stringify(e)} vs "UAH"`).toBe(false)
      expect(sameEquivalent('UAH', e), `"UAH" vs ${JSON.stringify(e)}`).toBe(false)
      for (const f of empties) expect(sameEquivalent(e, f), `${JSON.stringify(e)} vs ${JSON.stringify(f)}`).toBe(false)
    }
  })

  it('there is no fallback and no coercion to a code: the strings "null" and "undefined" are spellings, not missing values', () => {
    expect(sameEquivalent(null, 'NULL')).toBe(false)
    expect(sameEquivalent(undefined, 'UNDEFINED')).toBe(false)
    expect(sameEquivalent('null', 'NULL')).toBe(true)
  })
})
