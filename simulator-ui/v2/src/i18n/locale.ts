import { readonly, shallowRef, type Ref } from 'vue'

import { en } from './en'
import { ru } from './ru'

/**
 * 046 wave 0 (T4601), decision 2: the language of the interface. ONE module chooses it and is the ONLY writer of
 * `<html lang>` in product code; everything else reads. 046 closing (2026-10-20): it is also the only writer of the
 * browser tab's title (`document.title`, the dictionary's `locale.documentTitle`), at the same moments as `<html lang>`
 * - the first import, a switch, `popstate`; the static `<title>` of `index.html` is the English text for the instant
 * before this module runs.
 *
 * Priority, on load and on `popstate`:
 *   1. `?lang=` of the address - a link chooses for this visit and is NEVER written into storage;
 *   2. the stored choice `geo.sim.v2.lang` - written only by a switch (`setUiLocale`);
 *   3. the FIRST entry of `navigator.languages` only: `ru`, `uk`, `be` -> ru, anything else -> en (advisor's decision
 *      2026-10-18: a reader whose first language is German is not given Russian because Russian is second);
 *   4. `en`.
 * A parameter that is a supported language in another spelling (`ru-RU`, `RU`, `uk`) is rewritten to its canonical form
 * through `history.replaceState` (no reload, no new history entry, other parameters and the hash kept), as
 * `useUiTheme.ts` does. A parameter that names no supported language is no choice: it is removed the same way and the
 * next step decides. An empty `?lang=` is the absence of the parameter. A stored value this module did not write
 * (unknown) is removed.
 *
 * A switch stores the new language; if the address carries `?lang=`, it is rewritten to the new language too (else the
 * next load would follow the link, not the switch). An address without the parameter is left alone.
 *
 * The module initialises itself when it is first imported (`initUiLocale` below): `<html lang>` is right before the app
 * mounts. `uiLocale()` of `utils/paymentRefusalText.ts` reads `<html lang>` through `documentUiLocale`.
 *
 * NOT judged here: whether a component shows its text in this language - that is the dictionary's and the components'
 * (waves 1-3); a tab-to-tab change of the stored choice (`storage` event) is not followed (BACKLOG 046-1).
 */

export type UiLocale = 'en' | 'ru'

export const UI_LOCALES: readonly UiLocale[] = Object.freeze(['en', 'ru'] as const)
export const DEFAULT_UI_LOCALE: UiLocale = 'en'
export const UI_LOCALE_STORAGE_KEY = 'geo.sim.v2.lang'
export const UI_LOCALE_URL_PARAM = 'lang'

/** Each language named in itself - the same text in both interfaces (names, not dictionary text). */
export const UI_LOCALE_ENDONYMS: Readonly<Record<UiLocale, string>> = Object.freeze({ en: 'English', ru: 'Русский' })

/** Languages whose readers are served the Russian interface (spec 046 decision 2). */
const RUSSIAN_FAMILY = new Set(['ru', 'uk', 'be'])

function primarySubtag(tag: string): string {
  return tag.trim().toLowerCase().split(/[-_]/)[0] ?? ''
}

/** A language tag (`ru`, `ru-RU`, `RU`, `uk`, `en-GB`) -> the interface language that serves it, else null. */
export function normalizeUiLocale(raw: unknown): UiLocale | null {
  if (typeof raw !== 'string') return null
  const primary = primarySubtag(raw)
  if (primary === 'en') return 'en'
  if (RUSSIAN_FAMILY.has(primary)) return 'ru'
  return null
}

/** Step 3: the FIRST browser language alone decides - ru/uk/be -> ru, anything else -> en; null for an empty list. */
export function localeFromNavigatorLanguages(languages: readonly string[]): UiLocale | null {
  if (languages.length === 0) return null
  return normalizeUiLocale(languages[0]) === 'ru' ? 'ru' : 'en'
}

function browserLanguages(): readonly string[] {
  try {
    if (typeof navigator === 'undefined') return []
    if (navigator.languages && navigator.languages.length > 0) return navigator.languages
    return navigator.language ? [navigator.language] : []
  } catch {
    return []
  }
}

function storage(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null
  } catch {
    return null
  }
}

function readStored(): UiLocale | null {
  try {
    const s = storage()
    const raw = s?.getItem(UI_LOCALE_STORAGE_KEY) ?? null
    if (raw === null) return null
    const l = raw === 'en' || raw === 'ru' ? raw : null
    if (l === null) s?.removeItem(UI_LOCALE_STORAGE_KEY)
    return l
  } catch {
    return null
  }
}

function writeStored(l: UiLocale): void {
  try {
    storage()?.setItem(UI_LOCALE_STORAGE_KEY, l)
  } catch {
    // ignore: the choice holds for this page; it is not remembered.
  }
}

function urlParam(): string | null {
  try {
    const v = new URLSearchParams(window.location.search).get(UI_LOCALE_URL_PARAM)
    return v === null || v.trim() === '' ? null : v
  } catch {
    return null
  }
}

function replaceUrlParam(l: UiLocale | null): void {
  try {
    const u = new URL(window.location.href)
    if (l === null) u.searchParams.delete(UI_LOCALE_URL_PARAM)
    else u.searchParams.set(UI_LOCALE_URL_PARAM, l)
    window.history.replaceState(window.history.state, '', u.toString())
  } catch {
    // ignore
  }
}

/** The language the address, the storage and the browser choose now (priority in the header); normalises the URL. */
export function resolveUiLocale(): UiLocale {
  // Storage is read first whatever the URL says: an unknown stored value is removed on the first load, not later.
  const stored = readStored()
  const raw = urlParam()
  if (raw !== null) {
    const fromUrl = normalizeUiLocale(raw)
    if (fromUrl !== null) {
      if (raw !== fromUrl) replaceUrlParam(fromUrl)
      return fromUrl
    }
    replaceUrlParam(null)
  }
  return stored ?? localeFromNavigatorLanguages(browserLanguages()) ?? DEFAULT_UI_LOCALE
}

const current = shallowRef<UiLocale>(DEFAULT_UI_LOCALE)

/** The language of the interface, reactive: a template that reads it renders again on a switch. */
export const uiLocaleRef: Readonly<Ref<UiLocale>> = readonly(current)

/** The tab title in each language: read from the dictionaries directly (`t.ts` imports this module, not back). */
const DOCUMENT_TITLE: Readonly<Record<UiLocale, string>> = Object.freeze({ en: en.locale.documentTitle, ru: ru.locale.documentTitle })

function apply(l: UiLocale): void {
  current.value = l
  if (typeof document !== 'undefined') {
    document.documentElement.lang = l
    document.title = DOCUMENT_TITLE[l]
  }
}

let initialised = false

/** Resolve and apply the language once; listen to `popstate`. Idempotent. Runs on the first import of this module. */
export function initUiLocale(): UiLocale {
  if (!initialised) {
    initialised = true
    apply(resolveUiLocale())
    if (typeof window !== 'undefined') window.addEventListener('popstate', onUiLocalePopState)
  }
  return current.value
}

/** The entry the browser moved to decides again, by the same priority. */
export function onUiLocalePopState(): void {
  apply(resolveUiLocale())
}

/** A switch: show, write `<html lang>`, store; a `?lang=` of the address follows (see the header). */
export function setUiLocale(next: UiLocale): void {
  const l = normalizeUiLocale(next) ?? DEFAULT_UI_LOCALE
  apply(l)
  writeStored(l)
  if (urlParam() !== null) replaceUrlParam(l)
}

/**
 * The reader of `<html lang>` (written only by this module in product code). `uiLocale()` of
 * `utils/paymentRefusalText.ts` is this. An empty or other attribute reads as English.
 */
export function documentUiLocale(): UiLocale {
  const lang = typeof document === 'undefined' ? '' : document.documentElement.lang
  return lang.toLowerCase().startsWith('ru') ? 'ru' : 'en'
}

initUiLocale()
