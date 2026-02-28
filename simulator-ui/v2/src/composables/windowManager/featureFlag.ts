import { getCurrentInstance, inject, provide } from 'vue'

const WM_ENABLED_KEY = Symbol('geo:wm-enabled')

/**
 * Single source of truth for the runtime WM feature flag.
 *
 * MVP: enabled via query string `?wm=1`.
 *
 * Important: components/composables should NOT read `window.location` directly.
 * Read it once at the app root and propagate via provide/inject.
 */
export function readWindowManagerEnabledFromUrl(): boolean {
  try {
    return new URLSearchParams(window.location.search).get('wm') === '1'
  } catch {
    return false
  }
}

/** Provide the runtime WM flag for the current app instance. */
export function provideWindowManagerEnabled(enabled: boolean): void {
  provide(WM_ENABLED_KEY, enabled)
}

/** Inject the runtime WM flag. Defaults to false (WM disabled). */
export function useWindowManagerEnabled(): boolean {
  // Some pure unit tests call composables outside of a Vue setup/injection context.
  // In that case we must NOT call `inject()` (Vue would warn on stderr).
  if (!getCurrentInstance()) return false
  return inject<boolean>(WM_ENABLED_KEY, false)
}

