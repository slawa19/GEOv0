import { createApp, h, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { provideTopBarContext, type TopBarContext } from '../composables/useTopBarContext'
import TopBar from './TopBar.vue'

function createTopBarContext(overrides: Partial<TopBarContext> = {}): TopBarContext {
  return {
    apiMode: ref('real'),
    activeSegment: ref('auto'),
    isInteractUi: ref(false),
    isTestMode: ref(false),
    uiTheme: ref('hud'),
    loadingScenarios: ref(false),
    scenarios: ref([{ scenario_id: 'scenario-1', label: 'Scenario 1' }]),
    selectedScenarioId: ref('scenario-1'),
    desiredMode: ref('real'),
    intensityPercent: ref(50),
    runId: ref('run-1'),
    runStatus: ref(null),
    sseState: ref('open'),
    lastError: ref(null),
    runStats: ref({
      startedAtMs: 0,
      attempts: 0,
      committed: 0,
      rejected: 0,
      errors: 0,
      timeouts: 0,
      rejectedByCode: {},
      errorsByCode: {},
    }),
    accessToken: ref(null),
    adminRuns: ref(null),
    adminRunsLoading: ref(false),
    adminLastError: ref(undefined),
    adminCanGetRuns: ref(false),
    adminCanStopRuns: ref(false),
    adminCanAttachRun: ref(false),
    adminCanStopRun: ref(false),
    ...overrides,
  }
}

async function mountTopBar(overrides: Partial<TopBarContext> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const listeners = {
    'onGo-sandbox': vi.fn(),
    'onGo-auto-run': vi.fn(),
    'onGo-interact': vi.fn(),
    'onSet-ui-theme': vi.fn(),
    'onRefresh-scenarios': vi.fn(),
    'onStart-run': vi.fn(),
    'onPause': vi.fn(),
    'onResume': vi.fn(),
    'onStop': vi.fn(),
    'onApply-intensity': vi.fn(),
    'onUpdate:selected-scenario-id': vi.fn(),
    'onUpdate:desired-mode': vi.fn(),
    'onUpdate:intensity-percent': vi.fn(),
  }

  const app = createApp({
    setup() {
      provideTopBarContext(createTopBarContext(overrides))
      return () => h(TopBar, listeners)
    },
  })

  app.mount(host)
  await nextTick()

  return { host, app }
}

describe('TopBar dropdown focus contract', () => {
  it('moves keyboard-opened focus inside Advanced and traps Tab until Escape closes it', async () => {
    const { app, host } = await mountTopBar()

    const details = host.querySelector('details[aria-label="Advanced"]') as HTMLDetailsElement
    const summary = host.querySelector('[aria-label="Advanced settings"]') as HTMLElement
    const dropdown = host.querySelector('[aria-label="Advanced dropdown"]') as HTMLElement
    const intensityInput = host.querySelector('input[aria-label="Intensity percent"]') as HTMLInputElement
    const applyButton = Array.from(dropdown.querySelectorAll('button')).find(
      (candidate) => candidate.textContent?.trim() === 'Apply',
    ) as HTMLButtonElement | undefined

    expect(details).toBeTruthy()
    expect(summary).toBeTruthy()
    expect(dropdown).toBeTruthy()
    expect(intensityInput).toBeTruthy()
    expect(applyButton).toBeTruthy()

    summary.focus()
    summary.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    details.open = true
    details.dispatchEvent(new Event('toggle'))
    await nextTick()

    expect(document.activeElement).toBe(intensityInput)

    applyButton?.focus()
    applyButton?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(intensityInput)

    intensityInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(applyButton)

    applyButton?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await nextTick()

    expect(details.open).toBe(false)
    expect(document.activeElement).toBe(summary)

    app.unmount()
    host.remove()
  })

  it('moves keyboard-opened focus inside Admin and restores summary focus on Escape', async () => {
    const { app, host } = await mountTopBar({
      accessToken: ref('admin-token'),
      adminRuns: ref([
        {
          run_id: 'run-1',
          state: 'running',
          scenario_id: 'scenario-1',
          owner_id: 'owner-1',
          owner_kind: 'participant',
        },
      ]),
      adminCanGetRuns: ref(true),
      adminCanStopRuns: ref(true),
      adminCanAttachRun: ref(true),
      adminCanStopRun: ref(true),
    })

    const details = host.querySelector('details[aria-label="Admin controls"]') as HTMLDetailsElement
    const summary = host.querySelector('[aria-label="Admin"]') as HTMLElement
    const refreshButton = host.querySelector('button[aria-label="Refresh runs list"]') as HTMLButtonElement

    expect(details).toBeTruthy()
    expect(summary).toBeTruthy()
    expect(refreshButton).toBeTruthy()

    summary.focus()
    summary.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    details.open = true
    details.dispatchEvent(new Event('toggle'))
    await nextTick()

    expect(document.activeElement).toBe(refreshButton)

    const stopButtons = Array.from(details.querySelectorAll('button')).filter(
      (candidate) => candidate.textContent?.trim() === 'Stop',
    )
    const lastButton = stopButtons[stopButtons.length - 1] as HTMLButtonElement | undefined
    expect(lastButton).toBeTruthy()

    lastButton?.focus()
    lastButton?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(refreshButton)

    refreshButton.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await nextTick()

    expect(details.open).toBe(false)
    expect(document.activeElement).toBe(summary)

    app.unmount()
    host.remove()
  })
})