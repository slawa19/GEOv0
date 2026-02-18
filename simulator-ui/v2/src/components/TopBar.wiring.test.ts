import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'

function readHere(rel: string): string {
  return readFileSync(new URL(rel, import.meta.url), 'utf8')
}

describe('TopBar wiring smoke', () => {
  it('emits kebab-case update events (HTML-safe)', () => {
    const sfc = readHere('./TopBar.vue')

    // Emits (child)
    expect(sfc).toContain("emit('update:selected-scenario-id'")
    expect(sfc).toContain("emit('update:desired-mode'")
    expect(sfc).toContain("emit('update:intensity-percent'")

    // Optional: ensure we did not regress to camelCase
    expect(sfc).not.toContain("emit('update:selectedScenarioId'")
    expect(sfc).not.toContain("emit('update:desiredMode'")
    expect(sfc).not.toContain("emit('update:intensityPercent'")
  })

  it('listens to the same kebab-case update events in parent template', () => {
    const parent = readHere('./SimulatorAppRoot.vue')

    expect(parent).toContain('@update:selected-scenario-id="realActions.setSelectedScenarioId"')
    expect(parent).toContain('@update:desired-mode="realActions.setDesiredMode"')
    expect(parent).toContain('@update:intensity-percent="realActions.setIntensityPercent"')
  })
})
