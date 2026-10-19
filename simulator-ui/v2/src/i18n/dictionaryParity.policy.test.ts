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
 *      ru one/few/many/other);
 *   6. no code of an equivalent (`EQUIVALENT_CODES`) as a literal word in either language: a code is data and enters a
 *      text ONLY through a placeholder (`{unit}`, `{eq}`). Advisor's choice 2026-10-18, between "allow-list the codes"
 *      and "placeholders only": a literal `UAH` in a dictionary is wrong the moment the equivalent is `EUR`, and an
 *      allow-listed code would let exactly that through rule 3; a placeholder is also what spec 046 decision 1 asks.
 *      The list is the codes of `seeds/` (and `USD`); a new equivalent code is added to it, not to `RU_LATIN_ALLOWED`;
 *   7. no empty text (046 wave 1, full, 2026-10-19): a leaf that is empty or only spaces would show nothing where the
 *      interface promised a word - in either language, and in every form of a plural.
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

/**
 * Latin words a Russian text may carry: names and fixed technical labels, the same in both languages. Each with its
 * date and reason; it grows explicitly, never by pattern. NOT here: codes of equivalents (rule 6 - placeholders only).
 */
const RU_LATIN_ALLOWED: ReadonlySet<string> = new Set([
  'GEO', // 2026-10-18: the name of the protocol and the product
  'PID', // 2026-10-18: the participant identifier as the server names it; a label, not a value (named by the advisor for wave 1)
  'Esc', // 2026-10-18 (wave 2): the name of the key printed on the keyboard, the same in both languages
])

/** Codes of equivalents (`seeds/communities/*`, plus USD): data, never a literal word of a dictionary (rule 6). */
const EQUIVALENT_CODES: ReadonlySet<string> = new Set(['UAH', 'EUR', 'HOUR', 'KWH', 'USD'])

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
  for (const [lang, tree] of [['en', e], ['ru', r]] as const) {
    for (const [key, leaf] of tree) {
      for (const text of leaf.texts) if (text.trim() === '') problems.push(`${lang} ${key}: empty text`)
      for (const text of leaf.texts) {
        const codes = (withoutPlaceholders(text).match(LATIN_WORD) ?? []).filter((w) => EQUIVALENT_CODES.has(w.toUpperCase()))
        if (codes.length > 0) problems.push(`${lang} ${key}: equivalent code ${JSON.stringify(codes)} as a literal in "${text}" - use a placeholder`)
      }
    }
  }
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
  // A deep copy that keeps plural leaves as they are: `structuredClone` drops their symbol brand (wave 2: the shipped
  // dictionaries hold plurals now, and a copy without them would be a different dictionary, not a mutated one).
  const clone = <T>(v: T): T => {
    if (typeof v !== 'object' || v === null || isPluralMessage(v)) return v
    return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, clone(x)])) as T
  }

  it('a key removed from ru', () => {
    const r = clone(ru) as { locale: Record<string, string> }
    delete r.locale.switcherAria
    expect(dictionaryProblems(en, r)).toEqual(['ru lacks locale.switcherAria'])
  })

  it('a key removed from en (the reference): ru then carries a key en does not', () => {
    const e = clone(en) as { locale: Record<string, string> }
    delete e.locale.switcherAria
    expect(dictionaryProblems(e, ru)).toEqual(['ru has locale.switcherAria, en does not'])
  })

  it('046 wave 1, full: a key of the first screen removed from either file is named', () => {
    const r = clone(ru) as { actions: Record<string, string> }
    delete r.actions.noRun
    expect(dictionaryProblems(en, r)).toEqual(['ru lacks actions.noRun'])
    const e = clone(en) as { topBar: { state: Record<string, string> } }
    delete e.topBar.state.running
    expect(dictionaryProblems(e, ru)).toEqual(['ru has topBar.state.running, en does not'])
  })

  it('an empty text, in either language and in a form of a plural', () => {
    const r = clone(ru) as { onboarding: Record<string, string> }
    r.onboarding.protocol = '  '
    expect(dictionaryProblems(en, r)).toEqual(['ru onboarding.protocol: empty text'])
    const e = clone(en) as unknown as { topBar: Record<string, string> }
    e.topBar.details = ''
    expect(dictionaryProblems(e, ru)).toEqual(['en topBar.details: empty text'])
    expect(dictionaryProblems({ a: plural({ one: '{count} step', other: '' }) }, { a: plural({ one: '{count} шаг', few: '{count} шага', many: '{count} шагов', other: '{count} шага' }) }))
      .toEqual(['en a: empty text'])
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

  it('an equivalent code enters only through a placeholder: a literal code fails in either language, {unit} passes', () => {
    expect(dictionaryProblems({ a: 'at most {max} {unit}' }, { a: 'не больше {max} {unit}' })).toEqual([])
    expect(dictionaryProblems({ a: 'at most {max} UAH' }, { a: 'не больше {max} UAH' })).toEqual([
      'en a: equivalent code ["UAH"] as a literal in "at most {max} UAH" - use a placeholder',
      'ru a: equivalent code ["UAH"] as a literal in "не больше {max} UAH" - use a placeholder',
      'ru a: Latin ["UAH"] in "не больше {max} UAH"',
    ])
    // Allow-listing a code would not let it through: rule 6 does not read RU_LATIN_ALLOWED.
    expect(dictionaryProblems({ a: '{n} HOUR' }, { a: '{n} HOUR' }, new Set(['HOUR']))).toEqual([
      'en a: equivalent code ["HOUR"] as a literal in "{n} HOUR" - use a placeholder',
      'ru a: equivalent code ["HOUR"] as a literal in "{n} HOUR" - use a placeholder',
    ])
  })

  it('PID is on the allow-list (a label, not a value), and only a listed word passes', () => {
    expect(dictionaryProblems({ a: 'Participant PID' }, { a: 'PID участника' })).toEqual([])
    expect(dictionaryProblems({ a: 'Participant PID' }, { a: 'PID участника' }, new Set())).toEqual(['ru a: Latin ["PID"] in "PID участника"'])
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
