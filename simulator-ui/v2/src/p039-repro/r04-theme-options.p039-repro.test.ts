/**
 * 039 T3901 - R4 (themes), COMPONENT tier: the options of the theme switcher. Contrast of the candidate themes is the
 * browser tier's (axe on the surfaces of the stand), not this file's.
 *
 * Owner's decision R1 (2026-10-12): the switcher offers the default theme and `hud`; `shadcn` is frozen - out of the
 * switcher, tokens kept.
 *
 * LIMITS. It mounts `TopBar` alone: a theme control that moves into another component (variant A puts it into a "..."
 * menu) is not found here and the test must move with it. It does not judge which theme is the default.
 */
import { createApp, h, nextTick, ref } from 'vue'
import { describe, expect, it } from 'vitest'

import { provideTopBarContext, type TopBarContext } from '../composables/useTopBarContext'
import TopBar from '../components/TopBar.vue'
import { normalizeUiThemeId } from '../types/uiPrefs'
import { measured, red } from './p039'

function context(): TopBarContext {
  return {
    apiMode: ref('real'), activeSegment: ref('auto'), isInteractUi: ref(false), isTestMode: ref(false), uiTheme: ref('hud'),
    loadingScenarios: ref(false), scenarios: ref([{ scenario_id: 'scenario-1', label: 'Scenario 1' }]), selectedScenarioId: ref('scenario-1'),
    desiredMode: ref('real'), intensityPercent: ref(50), runId: ref('run-1'), runStatus: ref(null), sseState: ref('open'), lastError: ref(null),
    runStats: ref({ startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} }),
    accessToken: ref(null), adminRuns: ref(null), adminRunsLoading: ref(false), adminLastError: ref(undefined),
    adminCanGetRuns: ref(false), adminCanStopRuns: ref(false), adminCanAttachRun: ref(false), adminCanStopRun: ref(false),
  } as TopBarContext
}

async function themeOptions(): Promise<Array<{ value: string; text: string }>> {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ setup() { provideTopBarContext(context()); return () => h(TopBar) } })
  app.mount(host)
  await nextTick()
  const select = host.querySelector<HTMLSelectElement>('select[aria-label="UI theme"]')
  const out = select ? [...select.querySelectorAll('option')].map((o) => ({ value: o.value, text: (o.textContent ?? '').trim() })) : null
  app.unmount()
  host.remove()
  if (!out) throw new Error('039 R4: no theme switcher (select[aria-label="UI theme"]) in TopBar - the test has nothing to judge')
  return out
}

describe('039 R4 - the theme switcher', () => {
  it('counter-check: the switcher is found and offers `hud`', async () => {
    const options = await themeOptions()
    measured('R4 theme options', options)
    expect(options.map((o) => o.value)).toContain('hud')
  })

  red('offers exactly two themes, and `shadcn` is not one of them (today: four, shadcn first)', async () => {
    const values = (await themeOptions()).map((o) => o.value)
    expect(values, 'options of the theme switcher').toHaveLength(2)
    expect(values).not.toContain('shadcn')
  })

  it('measurement (P-6: a theme taken out of the switcher but still named by a URL or by storage): what the normalizer does today', () => {
    const row = Object.fromEntries(['shadcn', 'saas', 'library', 'hud', 'nonsense', ''].map((v) => [v || '(empty)', normalizeUiThemeId(v)]))
    measured('R4 normalizeUiThemeId', row)
    expect(row.hud).toBe('hud')
  })
})
