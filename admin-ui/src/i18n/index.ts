import { createI18n } from 'vue-i18n'
import { ref } from 'vue'

import { EN } from './en'
import { RU } from './ru'

export type Locale = 'en' | 'ru'

const LOCALE_KEY = 'admin-ui.locale'

function loadLocale(): Locale {
  try {
    const raw = (localStorage.getItem(LOCALE_KEY) || '').toLowerCase().trim()
    return raw === 'ru' ? 'ru' : 'en'
  } catch {
    return 'en'
  }
}

export const locale = ref<Locale>(loadLocale())

export const i18n = createI18n({
  legacy: false,
  globalInjection: false,
  locale: locale.value,
  fallbackLocale: 'en',
  messages: {
    en: EN,
    ru: RU,
  },
})

export function setLocale(next: Locale) {
  const val: Locale = next === 'ru' ? 'ru' : 'en'
  locale.value = val
  i18n.global.locale.value = val
  try {
    localStorage.setItem(LOCALE_KEY, val)
  } catch {
    // ignore
  }
}

export function t(key: string, params?: Record<string, string | number>): string {
  // Ensure Vue re-renders templates that call t() when locale changes.
  void locale.value
  return i18n.global.t(key, params ?? {}) as string
}
