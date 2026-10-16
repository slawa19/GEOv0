/**
 * Whether the dev-only demo tools (`ui=demo`: the Enter/Exit Demo toggle) exist for this page: a dev build
 * outside automated runs, or a build made with `VITE_ALLOW_DEMO_UI=true`. ONE rule for the toggle (`BottomBar.vue`)
 * and for the eager run bootstrap behind them (`useSimulatorApp.ts`): what has no toggle starts no run
 * (034 S8; owner's decision 2026-10-08 - `ui=demo` is dev-only).
 */
export function demoToolsAllowed(page: { isWebDriver: boolean; isTestMode: boolean; isE2eScreenshots: boolean }): boolean {
  if (import.meta.env.VITE_ALLOW_DEMO_UI === 'true') return true
  return import.meta.env.DEV && !page.isWebDriver && !page.isTestMode && !page.isE2eScreenshots
}
