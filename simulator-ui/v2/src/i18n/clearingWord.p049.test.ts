/**
 * 049 closing polish (advisor 2026-10-21): a clearing cycle has ONE word per language on the visitor's screen - «круг» in
 * Russian, «cycle» in English. Before, the clearing window and the own history line said «цикл» («Погашено циклов»,
 * «Клиринг: 1 цикл, −49745.90 UAH») and the confirm step said «замкнутые цепочки» / «loop», while the feed, the hint and
 * the onboarding said «круг» / «cycle».
 *
 * A policy guard over the dictionaries (every template and every plural form). What it does NOT see: a literal left in a
 * component instead of a key (the e2e «no Cyrillic under ?lang=en» / «no Latin under ?lang=ru» of 046 judge the screen,
 * and not this word), a synonym it does not list, prose of the documents. It checks the words, not whether a sentence is
 * right; what decides is reading the named key in both dictionaries.
 */
import { describe, expect, it } from 'vitest'

import { en } from './en'
import { ru } from './ru'
import { PLURAL, isPluralMessage } from './messages'

function leaves(tree: object, path = ''): Array<{ key: string; text: string }> {
  const out: Array<{ key: string; text: string }> = []
  for (const [k, v] of Object.entries(tree)) {
    const key = path ? `${path}.${k}` : k
    if (typeof v === 'string') out.push({ key, text: v })
    else if (isPluralMessage(v)) for (const [form, text] of Object.entries(v[PLURAL])) out.push({ key: `${key}[${form}]`, text })
    else if (v && typeof v === 'object') out.push(...leaves(v, key))
  }
  return out
}

const CASES = [
  { locale: 'ru', dict: ru, word: /круг/i, others: /цикл|замкнут\S*\s+цепоч/i },
  { locale: 'en', dict: en, word: /\bcycles?\b/i, others: /\bloops?\b|\bcircles?\b/i },
] as const

describe('049 closing polish: one word for a clearing cycle per language', () => {
  for (const c of CASES) {
    it(`[${c.locale}] no other word for a clearing cycle in the dictionary`, () => {
      const all = leaves(c.dict)
      // Anti-vacuum: the dictionary is read, and the word itself is there (the feed, the window, the hint).
      expect(all.length, 'counter-check: the dictionary is read').toBeGreaterThan(300)
      expect(all.filter((l) => c.word.test(l.text)).length, 'counter-check: the one word is used').toBeGreaterThanOrEqual(5)
      expect(all.filter((l) => c.others.test(l.text)), `[${c.locale}] texts naming a clearing cycle with another word. `
        + 'This guard checks the dictionary words only (not literals outside it, not documents). Say it with the one word, '
        + 'in both dictionaries.').toEqual([])
    })
  }
})
