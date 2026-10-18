/**
 * 046 wave 0 (T4601), decision 1 - the text of a key in the interface language: placeholders, plural forms by
 * `Intl.PluralRules`, and numbers printed without `Intl` (amounts keep their dot in both languages, decision 4).
 * LIMITS: the shipped dictionaries hold no plural key yet (wave 0), so plural selection is held on a message built here
 * with the same `plural()` the dictionaries use; their own categories are the parity guard's.
 */
import { afterEach, describe, expect, it } from 'vitest'

import { setUiLocale, uiLocaleRef } from './locale'
import { fillTemplate, formatPlural, plural } from './messages'
import { t } from './t'

afterEach(() => setUiLocale('en'))

describe('046 T4601 - t()', () => {
  it('follows the language of the interface; an explicit locale wins', () => {
    setUiLocale('en')
    expect(t('locale.switcherLabel')).toBe('Language')
    setUiLocale('ru')
    expect(uiLocaleRef.value).toBe('ru')
    expect(t('locale.switcherLabel')).toBe('Язык')
    expect(t('locale.switcherLabel', {}, 'en')).toBe('Language')
  })

  it('a key no dictionary has shows itself, not a text of another language', () => {
    expect(t('locale.nope' as never)).toBe('locale.nope')
  })
})

describe('046 T4601 - placeholders', () => {
  it('fills named placeholders; an unknown one stays visible', () => {
    expect(fillTemplate('{amount} {unit} from {from}', { amount: '12.50', unit: 'UAH' })).toBe('12.50 UAH from {from}')
  })

  it('an amount keeps its dot and its digits in both languages: a number is String(n), never Intl', () => {
    setUiLocale('ru')
    expect(fillTemplate('{a} | {b}', { a: '1234567.50', b: 1234567.5 })).toBe('1234567.50 | 1234567.5')
  })
})

describe('046 T4601 - plural forms', () => {
  const steps = {
    en: plural({ one: '{count} step', other: '{count} steps' }),
    ru: plural({ one: '{count} шаг', few: '{count} шага', many: '{count} шагов', other: '{count} шага' }),
  }

  it.each([
    [1, '1 step'], [2, '2 steps'], [0, '0 steps'], [21, '21 steps'],
  ])('en %d -> %s', (n, text) => {
    expect(formatPlural(steps.en, 'en', n)).toBe(text)
  })

  it.each([
    [1, '1 шаг'], [21, '21 шаг'], [2, '2 шага'], [4, '4 шага'], [5, '5 шагов'], [11, '11 шагов'], [12, '12 шагов'], [0, '0 шагов'], [1.5, '1.5 шага'],
  ])('ru %d -> %s', (n, text) => {
    expect(formatPlural(steps.ru, 'ru', n)).toBe(text)
  })

  it('a form the message lacks falls back to other', () => {
    expect(formatPlural(plural({ other: '{count} x' }), 'ru', 5)).toBe('5 x')
  })
})
