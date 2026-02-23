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

  it('uses events (not callback-props) for TopBar actions', () => {
    const sfc = readHere('./TopBar.vue')

    // These should be events now (defineEmits), not function props.
    expect(sfc).toContain("emit('go-sandbox'")
    expect(sfc).toContain("emit('go-auto-run'")
    expect(sfc).toContain("emit('go-interact'")
    expect(sfc).toContain("emit('set-ui-theme'")

    expect(sfc).toContain("emit('refresh-scenarios'")
    expect(sfc).toContain("emit('start-run'")
    expect(sfc).toContain("emit('pause'")
    expect(sfc).toContain("emit('resume'")
    expect(sfc).toContain("emit('stop'")
    expect(sfc).toContain("emit('apply-intensity'")

    expect(sfc).toContain("emit('admin-get-runs'")
    expect(sfc).toContain("emit('admin-stop-runs'")
    expect(sfc).toContain("emit('admin-attach-run'")
    expect(sfc).toContain("emit('admin-stop-run'")
  })

  it('listens to the same kebab-case update events in parent template', () => {
    const parent = readHere('./SimulatorAppRoot.vue')

    expect(parent).toContain('@update:selected-scenario-id="realActions.setSelectedScenarioId"')
    expect(parent).toContain('@update:desired-mode="realActions.setDesiredMode"')
    expect(parent).toContain('@update:intensity-percent="realActions.setIntensityPercent"')
  })

  it('listens to TopBar events in parent template (instead of callback-props)', () => {
    const parent = readHere('./SimulatorAppRoot.vue')

    expect(parent).toContain('@go-sandbox="goSandbox"')
    expect(parent).toContain('@go-auto-run="goAutoRun"')
    expect(parent).toContain('@go-interact="goInteract"')

    expect(parent).toContain('@set-ui-theme="setUiTheme"')

    expect(parent).toContain('@refresh-scenarios="realActions.refreshScenarios"')
    expect(parent).toContain('@start-run="realActions.startRun"')
    expect(parent).toContain('@pause="realActions.pause"')
    expect(parent).toContain('@resume="realActions.resume"')
    expect(parent).toContain('@stop="realActions.stop"')
    expect(parent).toContain('@apply-intensity="realActions.applyIntensity"')

    expect(parent).toContain('@admin-get-runs="admin.getRuns"')
    expect(parent).toContain('@admin-stop-runs="admin.stopRuns"')
    expect(parent).toContain('@admin-attach-run="admin.attachRun"')
    expect(parent).toContain('@admin-stop-run="admin.stopRun"')
  })
})
