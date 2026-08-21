import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

import { __analyticsPanelVisibilityPolicy } from './useSimulatorApp'

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
   * And the app really computes it that way.
   *
   * The policy alone is not enough: a correct rule nobody calls with the right arguments is the
   * same regression. `vue-tsc` now refuses a call missing an argument, and this refuses a call fed
   * from somewhere other than real mode and the panel toggle.
   */
  it('is fed with real mode and the panel toggle, at the definition and not merely at the seam', () => {
    const here = dirname(fileURLToPath(import.meta.url))
    const source = readFileSync(resolve(here, './useSimulatorApp.ts'), 'utf8')

    const start = source.indexOf('const isAnalyticsPanelVisible = ')
    expect(start).toBeGreaterThan(-1)
    const end = source.indexOf('\n  )', start)
    expect(end).toBeGreaterThan(start)

    // Comments removed: prose describing a dependency is not the dependency. Without this the
    // guard can be satisfied by the paragraph that explains the rule.
    const definition = source
      .slice(start, end)
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .split('\n')
      .filter((line) => !line.trim().startsWith('//'))
      .join('\n')

    // Counter-probe: the slice is the definition and nothing else.
    expect(definition.startsWith('const isAnalyticsPanelVisible = ')).toBe(true)
    expect(definition).not.toContain('useMetricsPolling')

    expect(definition).toContain('__analyticsPanelVisibilityPolicy(')
    expect(definition).toMatch(/\bisRealMode\.value\b/)
    expect(definition).toMatch(/\bisAnalyticsPanelOpen\b/)
  })
})
