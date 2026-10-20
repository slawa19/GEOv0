/**
 * 046 wave 0 (T4601), decision 2 - which language the interface takes, and what the choice writes back.
 * Priority: `?lang=` -> stored `geo.sim.v2.lang` -> the FIRST entry of `navigator.languages` (ru/uk/be -> ru, anything
 * else -> en) -> en. A link never writes storage; a switch does. A parameter in another spelling is rewritten by
 * `history.replaceState` (no push, no reload), an unsupported one is removed and the next step decides. The module is
 * the one writer of `<html lang>`; `uiLocale()` of `paymentRefusalText.ts` reads it.
 *
 * Real happy-dom `window.location`, `localStorage` and `navigator` (whose languages are replaced per test).
 * LIMITS: no mounted application (the switch's own mount is `components/LanguageSwitch.test.ts`); which text a
 * component shows in each language is waves 1-3.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { uiLocale } from '../utils/paymentRefusalText'
import {
  documentUiLocale,
  localeFromNavigatorLanguages,
  normalizeUiLocale,
  onUiLocalePopState,
  resolveUiLocale,
  setUiLocale,
  UI_LOCALE_STORAGE_KEY as KEY,
  uiLocaleRef,
} from './locale'
import { t } from './t'

function at(search: string) {
  window.history.replaceState(null, '', `/${search}`)
}
const langParam = () => new URLSearchParams(window.location.search).get('lang')

function browserSpeaks(languages: string[]) {
  Object.defineProperty(window.navigator, 'languages', { configurable: true, get: () => languages })
}

let replaceSpy: ReturnType<typeof vi.spyOn>
let pushSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  window.localStorage.clear()
  at('?mode=real')
  browserSpeaks(['en-US', 'en'])
  replaceSpy = vi.spyOn(window.history, 'replaceState')
  pushSpy = vi.spyOn(window.history, 'pushState')
})

afterEach(() => {
  replaceSpy.mockRestore()
  pushSpy.mockRestore()
  delete (window.navigator as unknown as Record<string, unknown>).languages
  window.localStorage.clear()
  document.documentElement.lang = 'en'
})

/** Resolve as on a load, counting only what the module does from here. */
function resolve() {
  replaceSpy.mockClear()
  pushSpy.mockClear()
  return resolveUiLocale()
}

describe('046 T4601 - the language on a load', () => {
  it('nothing chosen, an English browser: en; nothing is written', () => {
    expect(resolve()).toBe('en')
    expect(window.localStorage.getItem(KEY)).toBeNull()
    expect(replaceSpy).not.toHaveBeenCalled()
  })

  it.each([
    [['ru-RU', 'en'], 'ru'],
    [['uk-UA'], 'ru'],
    [['be'], 'ru'],
    [['en-GB', 'ru'], 'en'],
    [['de-DE', 'ru'], 'en'],
    [['de-DE', 'en-GB', 'ru'], 'en'],
    [['de-DE', 'fr'], 'en'],
    [[], 'en'],
  ])('browser %j -> %s (only the FIRST language decides: ru/uk/be -> ru, anything else -> en)', (languages, expected) => {
    browserSpeaks(languages)
    expect(resolve()).toBe(expected)
  })

  it('the stored choice wins over the browser', () => {
    browserSpeaks(['ru-RU'])
    window.localStorage.setItem(KEY, 'en')
    expect(resolve()).toBe('en')
  })

  it('?lang=ru wins over a stored en and a browser in English, and the stored choice stays en', () => {
    at('?mode=real&lang=ru')
    window.localStorage.setItem(KEY, 'en')
    expect(resolve()).toBe('ru')
    expect(window.localStorage.getItem(KEY), 'a link is never written into storage').toBe('en')
    expect(replaceSpy, 'a canonical parameter is left as it is').not.toHaveBeenCalled()
  })

  it('?lang=en wins over a stored ru and a Russian browser', () => {
    browserSpeaks(['ru'])
    at('?lang=en')
    window.localStorage.setItem(KEY, 'ru')
    expect(resolve()).toBe('en')
  })

  it.each(['RU', 'ru-RU', 'uk'])('?lang=%s: ru, the URL is rewritten to lang=ru by replaceState, other parameters and the hash kept', (raw) => {
    window.history.replaceState(null, '', `/?mode=real&lang=${raw}&ui=interact#x`)
    expect(resolve()).toBe('ru')
    expect(replaceSpy).toHaveBeenCalledTimes(1)
    expect(pushSpy).not.toHaveBeenCalled()
    expect(langParam()).toBe('ru')
    expect(new URLSearchParams(window.location.search).get('mode')).toBe('real')
    expect(new URLSearchParams(window.location.search).get('ui')).toBe('interact')
    expect(window.location.hash).toBe('#x')
  })

  it('?lang=de names no supported language: it is removed, and the next step (stored ru) decides', () => {
    at('?mode=real&lang=de')
    window.localStorage.setItem(KEY, 'ru')
    expect(resolve()).toBe('ru')
    expect(langParam()).toBeNull()
    expect(new URLSearchParams(window.location.search).get('mode')).toBe('real')
    expect(pushSpy).not.toHaveBeenCalled()
  })

  it('an empty ?lang= is the absence of the parameter: the stored choice holds, the URL is not touched', () => {
    at('?lang=')
    window.localStorage.setItem(KEY, 'ru')
    expect(resolve()).toBe('ru')
    expect(replaceSpy).not.toHaveBeenCalled()
  })

  it('a stored value this module did not write is removed, and the browser decides', () => {
    browserSpeaks(['ru'])
    window.localStorage.setItem(KEY, 'de')
    expect(resolve()).toBe('ru')
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('a storage that throws (blocked site data) is no choice: the browser decides', () => {
    browserSpeaks(['ru'])
    window.localStorage.setItem(KEY, 'en') // were the throw not reached, this en would decide
    // Blocked site data: the very access to `localStorage` throws (a SecurityError in a browser).
    const own = Object.getOwnPropertyDescriptor(window, 'localStorage')
    Object.defineProperty(window, 'localStorage', { configurable: true, get: () => { throw new Error('blocked') } })
    try {
      expect(resolve()).toBe('ru')
      expect(() => setUiLocale('ru'), 'a switch does not throw either').not.toThrow()
    } finally {
      if (own) Object.defineProperty(window, 'localStorage', own)
      else delete (window as unknown as Record<string, unknown>).localStorage
    }
    expect(window.localStorage.getItem(KEY), 'the real storage is back').toBe('en')
  })
})

describe('046 T4601 - a switch and popstate', () => {
  it('setUiLocale(ru): the state, <html lang> and the stored choice; an address without ?lang= is not touched', () => {
    setUiLocale('ru')
    expect([uiLocaleRef.value, document.documentElement.lang, window.localStorage.getItem(KEY)]).toEqual(['ru', 'ru', 'ru'])
    expect(langParam()).toBeNull()
    expect(pushSpy).not.toHaveBeenCalled()
    setUiLocale('en')
    expect([uiLocaleRef.value, document.documentElement.lang, window.localStorage.getItem(KEY)]).toEqual(['en', 'en', 'en'])
  })

  it('a switch while the address carries ?lang= rewrites it too, so a reload shows the switch, not the link', () => {
    at('?mode=real&lang=ru')
    setUiLocale('en')
    expect(langParam()).toBe('en')
    expect(resolve()).toBe('en')
  })

  it('popstate decides again by the same priority (URL, then the stored choice), and writes <html lang>', () => {
    setUiLocale('ru') // stored ru
    at('?lang=en')
    onUiLocalePopState()
    expect([uiLocaleRef.value, document.documentElement.lang]).toEqual(['en', 'en'])
    at('?mode=real')
    onUiLocalePopState()
    expect([uiLocaleRef.value, document.documentElement.lang], 'no parameter: the stored ru, not the last URL').toEqual(['ru', 'ru'])
  })

  it('setUiLocale of an unknown value (a stale caller) shows and stores en, never the value', () => {
    setUiLocale('ru')
    setUiLocale('de' as never)
    expect([uiLocaleRef.value, window.localStorage.getItem(KEY)]).toEqual(['en', 'en'])
  })
})

describe('046 T4601 - the reader of <html lang> and the first import', () => {
  it('uiLocale() of paymentRefusalText follows what the module writes', () => {
    setUiLocale('ru')
    expect([documentUiLocale(), uiLocale()]).toEqual(['ru', 'ru'])
    setUiLocale('en')
    expect([documentUiLocale(), uiLocale()]).toEqual(['en', 'en'])
  })

  it('the first import resolves and writes <html lang> before anything reads it (here: ?lang=ru)', async () => {
    vi.resetModules()
    document.documentElement.lang = ''
    at('?lang=ru')
    const fresh = await import('./locale')
    expect([fresh.uiLocaleRef.value, document.documentElement.lang]).toEqual(['ru', 'ru'])
    expect(window.localStorage.getItem(KEY), 'the link is not stored on the first load either').toBeNull()
  })

  it('a unit test that sets nothing runs in English: the first import in happy-dom gives en', async () => {
    vi.resetModules()
    delete (window.navigator as unknown as Record<string, unknown>).languages // happy-dom's own: en-US
    document.documentElement.lang = ''
    const fresh = await import('./locale')
    expect([fresh.uiLocaleRef.value, document.documentElement.lang]).toEqual(['en', 'en'])
  })

  it.each([
    ['ru', 'ru'], ['ru-RU', 'ru'], ['UK', 'ru'], ['be-BY', 'ru'], ['en', 'en'], ['en_GB', 'en'], ['de', null], ['', null], [null, null],
  ])('normalizeUiLocale(%j) -> %j', (raw, expected) => {
    expect(normalizeUiLocale(raw)).toBe(expected)
  })

  // Advisor's decision 2026-10-18: a person whose first language is German must not get Russian because Russian is
  // second in the list. Only the first entry is read.
  it('localeFromNavigatorLanguages reads only the first entry; an empty list says null', () => {
    expect(localeFromNavigatorLanguages(['de', 'ru'])).toBe('en')
    expect(localeFromNavigatorLanguages(['fr', 'uk'])).toBe('en')
    expect(localeFromNavigatorLanguages(['uk-UA', 'en'])).toBe('ru')
    expect(localeFromNavigatorLanguages(['en-US', 'ru'])).toBe('en')
    expect(localeFromNavigatorLanguages([])).toBeNull()
  })
})

// 046 closing review, finding 1 (2026-10-20): the browser tab's title was the static «GEO Simulator — Demo Fast Mock v2»
// of `index.html` in both languages, and nothing wrote `document.title`. The module that writes `<html lang>` writes the
// title from the dictionary at the same moment: the first import, a switch, popstate.
describe('046 closing - the browser tab title follows the language', () => {
  const title = (l: 'en' | 'ru') => t('locale.documentTitle', undefined, l)

  it('the two titles are dictionary texts and differ (the checks below would pass over one shared text otherwise)', () => {
    expect(title('en')).not.toBe('locale.documentTitle')
    expect(title('ru')).not.toBe('locale.documentTitle')
    expect(title('ru')).not.toBe(title('en'))
  })

  it('a switch writes the title of the new language, and back', () => {
    document.title = 'stale'
    setUiLocale('ru')
    expect(document.title).toBe(title('ru'))
    setUiLocale('en')
    expect(document.title).toBe(title('en'))
  })

  it('popstate writes the title of the language it decides', () => {
    at('?lang=ru')
    onUiLocalePopState()
    expect(document.title).toBe(title('ru'))
    at('?lang=en')
    onUiLocalePopState()
    expect(document.title).toBe(title('en'))
  })

  it('the first import writes the title before the app mounts (here: ?lang=ru)', async () => {
    vi.resetModules()
    document.title = 'GEO'
    at('?lang=ru')
    await import('./locale')
    expect(document.title).toBe(title('ru'))
  })
})
