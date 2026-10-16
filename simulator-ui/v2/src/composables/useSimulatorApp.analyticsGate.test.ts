import { effectScope, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getActiveRun, listScenarios } from '../api/simulatorApi'
import { __analyticsPanelVisibilityPolicy, useSimulatorApp } from './useSimulatorApp'

// The transport of the booted app, mocked at the module boundary: nothing here is about requests.
vi.mock('../api/simulatorApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulatorApi')>()
  return {
    ...actual,
    listScenarios: vi.fn(),
    getActiveRun: vi.fn(),
    getRun: vi.fn(async () => { throw new Error('no run in this test') }),
    getSnapshot: vi.fn(async () => { throw new Error('no snapshot in this test') }),
    getScenarioPreview: vi.fn(async () => { throw new Error('no preview in this test') }),
    ensureSession: vi.fn(async () => ({ actor_kind: 'anonymous', owner_id: 'o1' })),
  }
})
vi.mock('../api/equivalentsApi', () => ({ fetchEquivalentPrecisions: vi.fn(async () => []) }))

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
  vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
  vi.mocked(listScenarios).mockResolvedValue({ api_version: 'simulator-api/1', items: [] } as never)
  vi.mocked(getActiveRun).mockResolvedValue({} as never)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  window.localStorage.clear()
})

/**
 * The fourth position of the registered `useSimulatorApp` blind spot (spec 008).
 *
 * `useSimulatorApp` is instantiated exactly once, inside `SimulatorAppRoot`, and the only test
 * that mounts that component mocks the composable away — so nothing executes its body. The first
 * two positions were closed with a source-text guard over what the app PASSES to a composable.
 * This one is different: the half that had no defence was not a dependency being passed, it was
 * the real-mode half of a condition being COMPUTED. Measured before this change: deleting
 * `isRealMode.value &&` from `isAnalyticsPanelVisible` left all 849 tests green, plus `typecheck`
 * and `lint`, while the analytics panel would open over fixtures and poll two endpoints that have
 * no metric store behind them.
 *
 * It survived because the test that looked like it covered it (`SimulatorAppRoot.interact.test.ts`,
 * "never shows over fixtures") re-stated the rule inside its own mock — a test that re-states the
 * rule proves the restatement. The rule is therefore a named export, exercised here directly, and
 * that same export is what the mock now calls.
 */
describe('the analytics surface gate (spec 007, T705)', () => {
  it('is closed over fixtures however the toggle is set, and open only when both halves are', () => {
    // Fixtures have no metric store: this row is the whole point of the rule.
    expect(__analyticsPanelVisibilityPolicy(false, true)).toBe(false)

    expect(__analyticsPanelVisibilityPolicy(true, true)).toBe(true)
    expect(__analyticsPanelVisibilityPolicy(true, false)).toBe(false)
    expect(__analyticsPanelVisibilityPolicy(false, false)).toBe(false)
  })

  /**
   * And the app really computes it that way (F-041-9, converted in 043 S1 from a guard that sliced the source of
   * `useSimulatorApp.ts` and looked for `isRealMode.value` and `isAnalyticsPanelOpen` in the text of the definition).
   *
   * Observed instead: the REAL composable is booted (its transport mocked at the module boundary, as in
   * `useSimulatorApp.runMode.p042.test.ts`) in real mode and over fixtures, with the panel toggle on and off and
   * flipped while it runs, and what it publishes - `analytics.isVisible` - is read. Deleting the real-mode half of
   * the condition makes the fixtures rows true; feeding the policy anything but the live toggle breaks the flip rows.
   */
  it('is fed with real mode and the live panel toggle: the booted app publishes exactly the policy', async () => {
    const rows: string[] = []
    for (const mode of ['real', 'fixtures'] as const) {
      for (const initiallyOpen of [false, true]) {
        window.history.replaceState({}, '', `/?mode=${mode}`)
        const open = ref(initiallyOpen)
        const scope = effectScope()
        const app = scope.run(() => useSimulatorApp({ isAnalyticsPanelOpen: () => open.value }))!
        try {
          await nextTick()
          rows.push(`${mode} open=${open.value} -> ${app.analytics.isVisible.value}`)
          open.value = !open.value
          await nextTick()
          rows.push(`${mode} open=${open.value} -> ${app.analytics.isVisible.value}`)
        } finally {
          scope.stop()
        }
      }
    }
    window.history.replaceState({}, '', '/')
    expect(rows).toEqual([
      'real open=false -> false', 'real open=true -> true',
      'real open=true -> true', 'real open=false -> false',
      'fixtures open=false -> false', 'fixtures open=true -> false',
      'fixtures open=true -> false', 'fixtures open=false -> false',
    ])
  })

  it('without the getter (no such surface) it is closed in real mode', async () => {
    window.history.replaceState({}, '', '/?mode=real')
    const scope = effectScope()
    const app = scope.run(() => useSimulatorApp())!
    try {
      expect(app.analytics.isVisible.value).toBe(false)
    } finally {
      scope.stop()
      window.history.replaceState({}, '', '/')
    }
  })
})
