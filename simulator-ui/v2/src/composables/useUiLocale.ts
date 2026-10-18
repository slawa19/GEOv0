import { setUiLocale, uiLocaleRef } from '../i18n/locale'
import { t, tn } from '../i18n/t'

/**
 * 046 wave 0 (T4601): the language of the interface for a component - the reactive language, the switch and the two
 * text functions. One state for the whole app (`i18n/locale.ts`), not one per caller: the language is a property of the
 * page, as `<html lang>` is. Unlike `useUiTheme`, nothing here is wired by the root: the module listens to `popstate`
 * itself and resolved the language on its first import.
 */
export function useUiLocale() {
  return { locale: uiLocaleRef, setUiLocale, t, tn }
}
