/**
 * 054-B-7b fix-delta (§15 Codex R3, P3): the run's error line keeps the code and the time whole; only the server's message
 * is shortened to fit the top bar. Before: the bar cut the whole sentence at 160 characters, and a regular message - the
 * money phase that did not commit (`REAL_MODE_MONEY_CONFLICT_UNRESOLVED`, `app/core/simulator/tick.py`) - lost the time
 * (215 characters in EN, 219 in RU); the title had no time at all.
 * Counter-check: a short message is drawn whole. TopBar mounted with the line as the root wires it (text, title).
 */
import { computed, createApp, h, nextTick, ref } from 'vue'
import { describe, expect, it } from 'vitest'

import { provideTopBarContext, type TopBarContext } from '../composables/useTopBarContext'
import { setUiLocale } from '../i18n/locale'
import {
  RUN_ERROR_LINE_MAX_CHARS,
  renderRunErrorLine,
  runEndedByErrorLine,
  runErrorLine,
  runErrorLineTitle,
  type RunErrorLine,
} from '../utils/runErrorClassification'
import TopBar from './TopBar.vue'

const CODE = 'REAL_MODE_MONEY_CONFLICT_UNRESOLVED'
const MESSAGE = "The tick's money phase did not commit: 40001 (DBAPIError). Consecutive ticks without money progress: 1."
// The form `datetime.isoformat()` gives the server's `at` (`rr._utc_now().isoformat()`).
const AT = '2026-10-30T12:34:56.123456+00:00'

function drawn(line: RunErrorLine, locale: 'ru' | 'en') {
  setUiLocale(locale)
  const ctx = {
    apiMode: ref('real'), isTestMode: ref(false), uiTheme: ref('saas'),
    loadingScenarios: ref(false), scenarios: ref([]), selectedScenarioId: ref('sc1'), intensityPercent: ref(30), runId: ref('r1'),
    runStatus: ref({ run_id: 'r1', scenario_id: 'sc1', state: 'error' }), sseState: ref('closed'),
    lastError: computed(() => renderRunErrorLine(line, locale)),
    lastErrorTitle: computed(() => runErrorLineTitle(line)),
    runStats: ref({ startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} }),
    accessToken: ref(null),
  } as unknown as TopBarContext
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ setup() { provideTopBarContext(ctx); return () => h(TopBar) } })
  app.mount(host)
  return {
    read: async () => {
      await nextTick()
      const el = host.querySelector('[data-testid="error-text"]')!
      return { text: el.textContent ?? '', title: el.getAttribute('title') ?? '' }
    },
    done: () => { app.unmount(); host.remove() },
  }
}

describe('054-B-7b fix-delta: the error line keeps the code and the time whole', () => {
  for (const [what, make] of [['ended', runEndedByErrorLine], ['going on', runErrorLine]] as const) {
    for (const locale of ['en', 'ru'] as const) {
      it(`a run ${what}, the money conflict message [${locale}]: code and time drawn, the message shortened`, async () => {
        const line = make({ code: CODE, message: MESSAGE, at: AT })
        const d = drawn(line, locale)
        const { text, title } = await d.read()
        d.done()
        expect(text, 'the code').toContain(CODE)
        expect(text, 'the time').toContain(AT)
        expect(text.length, 'the line fits the bar').toBeLessThanOrEqual(RUN_ERROR_LINE_MAX_CHARS)
        expect(text, 'only the message is shortened').toContain(`${CODE} — The tick`)
        expect(title, 'the title keeps the whole message').toContain(MESSAGE)
        expect(title, 'and the time').toContain(AT)
      })
    }
  }

  it('counter-check: a short message is drawn whole', async () => {
    const line = runEndedByErrorLine({ code: 'INTERNAL_ERROR', message: 'boom', at: AT })
    const d = drawn(line, 'en')
    const { text } = await d.read()
    d.done()
    expect(text).toBe(`The run stopped: INTERNAL_ERROR — boom (${AT}). Restart the run.`)
  })
})
