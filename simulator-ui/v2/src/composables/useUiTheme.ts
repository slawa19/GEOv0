import { ref } from 'vue'

import { DEFAULT_UI_THEME, normalizeUiThemeId, type UiThemeId } from '../types/uiPrefs'

type ThemeStorage = {
  /** The stored choice, already normalised (an old or unknown value is rewritten there once); `null` when absent. */
  readUiTheme(): UiThemeId | null
  writeUiTheme(theme: UiThemeId): void
}

/**
 * The UI theme of the app (039 slice B, decision 6; owner's decision R1): `saas` (default) or `hud`.
 *
 * Priority: an explicit `?theme=` -> the stored choice -> `saas`. An explicit parameter that names a removed theme
 * (`shadcn`, `library`) or nothing known is still explicit: it shows `saas`, and the URL is rewritten to `?theme=saas`
 * through `history.replaceState` (no reload, no new history entry). An empty or absent parameter is no choice: it
 * never overwrites the stored one. A URL is never written into storage; only a switch (`setUiTheme`) is.
 * `onPopState` applies the same rules to the entry the browser moved to; the root wires it to `popstate`.
 */
export function useUiTheme(storage: ThemeStorage) {
  function urlParam(): string | null {
    try {
      const v = new URLSearchParams(window.location.search).get('theme')
      return v === null || v.trim() === '' ? null : v
    } catch {
      return null
    }
  }

  function replaceThemeParam(theme: UiThemeId | null) {
    try {
      const u = new URL(window.location.href)
      if (theme === null) u.searchParams.delete('theme')
      else u.searchParams.set('theme', theme)
      window.history.replaceState(window.history.state, '', u.toString())
    } catch {
      // ignore
    }
  }

  function fromLocation(): UiThemeId {
    // Read storage first, whatever the URL says: an old stored value is rewritten on the first load, not later.
    const stored = storage.readUiTheme()
    const raw = urlParam()
    if (raw === null) return stored ?? DEFAULT_UI_THEME
    const theme = normalizeUiThemeId(raw)
    if (raw !== theme) replaceThemeParam(theme)
    return theme
  }

  const uiTheme = ref<UiThemeId>(fromLocation())

  function onPopState() {
    uiTheme.value = fromLocation()
  }

  function setUiTheme(next: UiThemeId) {
    const theme = normalizeUiThemeId(next)
    uiTheme.value = theme
    storage.writeUiTheme(theme)
    // The default needs no parameter; the stored choice (just written) holds it.
    replaceThemeParam(theme === DEFAULT_UI_THEME ? null : theme)
  }

  return { uiTheme, setUiTheme, onPopState }
}
