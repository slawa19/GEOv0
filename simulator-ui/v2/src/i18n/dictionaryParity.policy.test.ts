/**
 * POLICY GUARD (046 decision 5, AGENTS.md §11): the two dictionaries of the interface are one dictionary in two
 * languages.
 *
 * What it holds, on `en.ts` and `ru.ts` as shipped:
 *   1. the same keys, with leaves of the same kind (template / plural) - the build already refuses a missing or extra
 *      key in `ru` (`satisfies Messages<typeof en>`); this says it again at run time and names the key;
 *   2. no Cyrillic in an English text;
 *   3. no Latin word in a Russian text (placeholders removed) unless it is on `RU_LATIN_ALLOWED` - names and codes that
 *      are the same in both languages. A text with no letters at all (`{amount} {unit}`, `→`) passes;
 *   4. the same placeholders under a key in both languages (union over the forms of a plural);
 *   5. a plural message has exactly the categories its language distinguishes (`Intl.PluralRules`: en one/other,
 *      ru one/few/many/other).
 *
 * What it does NOT see: whether a translation is right, reads well or fits its control (ru is 20-35 % longer: the
 * width probes of 045 / `top-bar-long.qa.ts` decide that); a literal left in a component instead of a key (waves 1-3
 * move them; the e2e "no Cyrillic under ?lang=en" / "no Latin under ?lang=ru" reproducers of the spec judge the screen).
 * What decides a red here: read the named key in both files; fix the text, or - only for a name or a code - add the
 * word to `RU_LATIN_ALLOWED` with a reason.
 *
 * Counter-check (§9 anti-vacuum): every rule is shown to fail on a mutated copy, and the walk is shown to reach leaves.
 */
import { describe, expect, it } from 'vitest'

import { en } from './en'
import { isPluralMessage, PLURAL, placeholdersOf, plural, pluralCategoriesOf } from './messages'
import { ru } from './ru'

/** Latin words a Russian text may carry: names and codes, the same in both languages. Each with its reason. */
const RU_LATIN_ALLOWED: ReadonlySet<string> = new Set([
  'GEO', // the name of the protocol and the product
])

type Leaf = { kind: 'text'; texts: string[] } | { kind: 'plural'; texts: string[]; categories: string[] }

function leaves(tree: unknown, prefix = '', out = new Map<string, Leaf>()): Map<string, Leaf> {
  for (const [k, v] of Object.entries(tree as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${k}` : k
    if (typeof v === 'string') out.set(path, { kind: 'text', texts: [v] })
    else if (isPluralMessage(v)) {
      const forms = v[PLURAL] as Record<string, string>
      out.set(path, { kind: 'plural', texts: Object.values(forms), categories: Object.keys(forms).sort() })
    } else if (typeof v === 'object' && v !== null) leaves(v, path, out)
    else out.set(path, { kind: 'text', texts: [String(v)] }) // a non-text leaf: kept, so the kinds differ and it is named
  }
  return out
}

const CYRILLIC = /[Ѐ-ӿ]/
const LATIN_WORD = /[A-Za-z]+/g
const withoutPlaceholders = (s: string) => s.replace(/\{\w+\}/g, ' ')

function dictionaryProblems(enTree: unknown, ruTree: unknown, allowed: ReadonlySet<string> = RU_LATIN_ALLOWED): string[] {
  const problems: string[] = []
  const e = leaves(enTree)
  const r = leaves(ruTree)
  for (const key of e.keys()) if (!r.has(key)) problems.push(`ru lacks ${key}`)
  for (const key of r.keys()) if (!e.has(key)) problems.push(`ru has ${key}, en does not`)
  for (const [key, leaf] of e) {
    for (const text of leaf.texts) if (CYRILLIC.test(text)) problems.push(`en ${key}: Cyrillic in "${text}"`)
    if (leaf.kind === 'plural') {
      const want = [...pluralCategoriesOf('en')].sort()
      if (leaf.categories.join() !== want.join()) problems.push(`en ${key}: plural categories ${leaf.categories} != ${want}`)
    }
  }
  for (const [key, leaf] of r) {
    for (const text of leaf.texts) {
      const latin = withoutPlaceholders(text).match(LATIN_WORD) ?? []
      const foreign = latin.filter((w) => !allowed.has(w))
      if (foreign.length > 0) problems.push(`ru ${key}: Latin ${JSON.stringify(foreign)} in "${text}"`)
    }
    if (leaf.kind === 'plural') {
      const want = [...pluralCategoriesOf('ru')].sort()
      if (leaf.categories.join() !== want.join()) problems.push(`ru ${key}: plural categories ${leaf.categories} != ${want}`)
    }
    const other = e.get(key)
    if (!other) continue
    if (other.kind !== leaf.kind) problems.push(`${key}: en is ${other.kind}, ru is ${leaf.kind}`)
    const names = (l: Leaf) => [...new Set(l.texts.flatMap(placeholdersOf))].sort().join()
    if (names(other) !== names(leaf)) problems.push(`${key}: placeholders en {${names(other)}} != ru {${names(leaf)}}`)
  }
  return problems
}

describe('046 policy guard - the dictionary parity of en.ts and ru.ts', () => {
  it('the shipped dictionaries have no problem', () => {
    expect(dictionaryProblems(en, ru)).toEqual([])
  })

  it('anti-vacuum: the walk reaches the leaves of both (a guard over nothing would pass too)', () => {
    const e = leaves(en)
    expect(e.size).toBeGreaterThan(0)
    expect([...e.keys()]).toContain('locale.switcherLabel')
    expect([...leaves(ru).keys()]).toEqual([...e.keys()])
  })
})

describe('046 policy guard - every rule fails on a mutated copy', () => {
  const clone = <T>(v: T): T => structuredClone(v)

  it('a key removed from ru', () => {
    const r = clone(ru) as { locale: Record<string, string> }
    delete r.locale.switcherAria
    expect(dictionaryProblems(en, r)).toEqual(['ru lacks locale.switcherAria'])
  })

  it('a key only ru has', () => {
    const r = clone(ru) as { locale: Record<string, string> }
    r.locale.extra = 'Лишний'
    expect(dictionaryProblems(en, r)).toEqual(['ru has locale.extra, en does not'])
  })

  it('Cyrillic in an English text', () => {
    const e = clone(en) as { locale: Record<string, string> }
    e.locale.switcherLabel = 'Язык'
    expect(dictionaryProblems(e, ru)).toEqual(['en locale.switcherLabel: Cyrillic in "Язык"'])
  })

  it('an English word in a Russian text - alone, or mixed into a Russian one', () => {
    const r = clone(ru) as { locale: Record<string, string> }
    r.locale.switcherLabel = 'Language'
    r.locale.switcherAria = 'Язык (Language)'
    expect(dictionaryProblems(en, r)).toEqual([
      'ru locale.switcherLabel: Latin ["Language"] in "Language"',
      'ru locale.switcherAria: Latin ["Language"] in "Язык (Language)"',
    ])
  })

  it('a name on the allow-list passes; placeholders and a text without letters pass', () => {
    const e = { a: 'GEO network', b: '{amount} {unit}', c: '→' }
    const r = { a: 'Сеть GEO', b: '{amount} {unit}', c: '→' }
    expect(dictionaryProblems(e, r)).toEqual([])
    expect(dictionaryProblems(e, r, new Set())).toEqual(['ru a: Latin ["GEO"] in "Сеть GEO"'])
  })

  it('placeholders that differ', () => {
    expect(dictionaryProblems({ a: '{amount} {unit}' }, { a: '{amount}' })).toEqual(['a: placeholders en {amount,unit} != ru {amount}'])
  })

  it('a template in one language and a plural in the other', () => {
    expect(dictionaryProblems({ a: '{count} steps' }, { a: plural({ one: '{count} шаг', few: '{count} шага', many: '{count} шагов', other: '{count} шага' }) }))
      .toEqual(['a: en is text, ru is plural'])
  })

  it('a plural without a category its language needs, or with one it has not', () => {
    const e = { a: plural({ one: '{count} step', other: '{count} steps' }) }
    expect(dictionaryProblems(e, { a: plural({ one: '{count} шаг', other: '{count} шага' }) }))
      .toEqual(['ru a: plural categories one,other != few,many,one,other'])
    expect(dictionaryProblems({ a: plural({ one: '{count} step', few: '{count} steps', other: '{count} steps' }) },
      { a: plural({ one: '{count} шаг', few: '{count} шага', many: '{count} шагов', other: '{count} шага' }) }))
      .toEqual(['en a: plural categories few,one,other != one,other'])
  })
})
