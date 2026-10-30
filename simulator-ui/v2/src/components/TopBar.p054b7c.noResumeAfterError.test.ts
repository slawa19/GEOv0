/**
 * 054-B-7c (the owner's decision of 2026-10-30): a run the server stopped by one of its guards (`REAL_MODE_TOO_MANY_ERRORS`,
 * `REAL_MODE_TOO_MANY_TIMEOUTS`, `REAL_MODE_TICK_FAILED_REPEATED`, `REAL_MODE_MONEY_NO_PROGRESS`, `INTEGRITY_VIOLATION`;
 * the server refuses their resume with 409) offers no "Продолжить": the one entry of resume on the screen is "Поток" of the
 * first row (`onFlowToggle` -> `@resume` -> `SimulatorAppRoot.vue` `realActions.resume`; no hotkey, no dev hook), rendered
 * for `running`/`paused` only - absent for a run in `error`, whatever its code (kept and held here). What it has instead:
 * "Перезапустить" (a new run of the scenario - `start-run`) for every addressee in the first row, and "Остановить"
 * (`stop`) in the administrator's run row. Counter-check: a paused run keeps "Поток" and it resumes; a living run has no
 * "Перезапустить".
 * TopBar mounted with its context, each control used the way a person uses it.
 */
import { createApp, h, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type { RunStatus } from '../api/simulatorTypes'
import { provideTopBarContext, type TopBarContext } from '../composables/useTopBarContext'
import TopBar from './TopBar.vue'

const GUARD_CODES = [
  'REAL_MODE_TOO_MANY_ERRORS',
  'REAL_MODE_TOO_MANY_TIMEOUTS',
  'REAL_MODE_TICK_FAILED_REPEATED',
  'REAL_MODE_MONEY_NO_PROGRESS',
  'INTEGRITY_VIOLATION',
] as const

function mount(runStatus: RunStatus, accessToken: string | null) {
  const ctx = {
    apiMode: ref('real'), isTestMode: ref(false), uiTheme: ref('saas'),
    loadingScenarios: ref(false), scenarios: ref([{ scenario_id: 'sc-1', label: 'Riverside town' }]),
    selectedScenarioId: ref('sc-1'), intensityPercent: ref(30), runId: ref('run-1'), runStatus: ref(runStatus), sseState: ref('closed'),
    lastError: ref('stand line'),
    runStats: ref({ startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} }),
    accessToken: ref(accessToken),
  } as unknown as TopBarContext
  const host = document.createElement('div')
  document.body.appendChild(host)
  const spies = { resume: vi.fn(), startRun: vi.fn(), stop: vi.fn() }
  const app = createApp({
    setup() {
      provideTopBarContext(ctx)
      return () => h(TopBar, { onResume: spies.resume, onStartRun: spies.startRun, onStop: spies.stop, detailsOpen: true })
    },
  })
  app.mount(host)
  return { host, spies, done: () => { app.unmount(); host.remove() } }
}

const statusOf = (state: string, code: string | null) =>
  ({ run_id: 'run-1', scenario_id: 'sc-1', state, last_error: code ? { code, message: 'boom', at: '2026-10-30T00:00:00Z' } : null }) as RunStatus

describe('054-B-7c: no resume after a stop by the guards; restart and stop instead', () => {
  for (const code of GUARD_CODES) {
    for (const [who, token] of [['visitor', null], ['administrator', 'admin-token']] as const) {
      it(`${code}, ${who}: no "Поток"; "Перезапустить" starts a new run; nothing of the bar resumes it`, async () => {
        const { host, spies, done } = mount(statusOf('error', code), token)
        await nextTick()
        expect(host.querySelector('[data-testid="flow-toggle"]'), 'no resume control').toBeNull()
        const restart = host.querySelector<HTMLButtonElement>('[data-testid="run-restart"]')
        expect(restart, '"Перезапустить" is on the first row').not.toBeNull()
        restart!.click()
        expect(spies.startRun, 'it starts a new run').toHaveBeenCalledTimes(1)
        if (who === 'administrator') {
          const stop = host.querySelector<HTMLButtonElement>('[data-testid="run-stop"]')
          expect(stop, '"Остановить" is in the run row').not.toBeNull()
          stop!.click()
          expect(spies.stop).toHaveBeenCalledTimes(1)
        }
        for (const b of host.querySelectorAll<HTMLButtonElement>('button')) b.click()
        expect(spies.resume, 'no button of the bar resumes it').not.toHaveBeenCalled()
        done()
      })
    }
  }

  it('counter-check: a paused run keeps "Поток", and it resumes; a living run has no "Перезапустить"', async () => {
    for (const state of ['paused', 'running'] as const) {
      const { host, spies, done } = mount(statusOf(state, null), null)
      await nextTick()
      expect(host.querySelector('[data-testid="run-restart"]'), `${state}: no restart`).toBeNull()
      if (state === 'paused') {
        host.querySelector<HTMLButtonElement>('[data-testid="flow-toggle"]')!.click()
        expect(spies.resume).toHaveBeenCalledTimes(1)
      }
      done()
    }
  })
})
