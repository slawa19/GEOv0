import { ref } from 'vue'

import { normalizeUiThemeId, type UiThemeId } from '../types/uiPrefs'

type ThemeStorage = {
  readUiTheme(): UiThemeId | null
  writeUiTheme(theme: UiThemeId): void
}

/**
 * The UI theme of the app: read from the URL and the stored choice, written back on a switch.
 * Extracted from `SimulatorAppRoot.vue` unchanged (039 B3); the root wires `onPopState` to `popstate`.
 */
export function useUiTheme(storage: ThemeStorage) {
  function readThemeFromUrl(): UiThemeId {
    try {
      return normalizeUiThemeId(new URLSearchParams(window.location.search).get('theme'))
    } catch {
      return 'hud'
    }
  }

  function pickInitialTheme(): UiThemeId {
    const url = readThemeFromUrl()
    if (url !== 'hud') return url
    const stored = storage.readUiTheme()
    return stored ?? 'hud'
  }

  const uiTheme = ref<UiThemeId>(pickInitialTheme())

  function onPopState() {
    uiTheme.value = readThemeFromUrl()
  }

  function setUiTheme(next: UiThemeId) {
    const theme = normalizeUiThemeId(next)
    uiTheme.value = theme
    storage.writeUiTheme(theme)

    try {
      const u = new URL(window.location.href)
      if (theme === 'hud') u.searchParams.delete('theme')
      else u.searchParams.set('theme', theme)
      window.history.replaceState({}, '', u.toString())
    } catch {
      // ignore
    }
  }

  return { uiTheme, setUiTheme, onPopState }
}
