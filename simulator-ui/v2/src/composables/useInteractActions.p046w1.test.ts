/**
 * 046 wave 1 (orchestrator, 2026-10-18): 044 stage 2 (PR #299) answers a refused trust line create with HTTP 409 and the
 * flat body `{"code":"CONFLICT","message":...,"details":{"reason":...}}` - `equivalent_inactive`,
 * `equivalent_integrity_hold` or `participant_suspended` (with `details.participants`). Before #299 the same refusals
 * answered 500 (`MissingGreenlet`). The trust line window tells the reason in the language of the interface
 * (`actionReason.*`); `participant_suspended` had no text and fell through to the CONFLICT text "the system is busy".
 *
 * The REAL `useInteractActions` / `simulatorApi` / `http` run; only `fetch` is a stub. LIMITS: the participant is not
 * named in the text (the error text has no name lookup; `details.participants` stays for correlation in the answer).
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'

import { setUiLocale } from '../i18n/locale'
import { t } from '../i18n/t'
import { useInteractActions } from './useInteractActions'
import { useInteractMode } from './useInteractMode'

afterEach(() => {
  vi.unstubAllGlobals()
  setUiLocale('en')
})

const refused = (details: Record<string, unknown>) => vi.fn(async () =>
  new Response(JSON.stringify({ code: 'CONFLICT', message: 'server words', details }), { status: 409, headers: { 'Content-Type': 'application/json' } }))

async function createRefused(details: Record<string, unknown>): Promise<string | null> {
  vi.stubGlobal('fetch', refused(details))
  const actions = useInteractActions({ httpConfig: ref({ apiBase: '/api/v1' }), runId: ref('run-1') })
  const mode = useInteractMode({ actions, runId: computed(() => 'run-1'), equivalent: computed(() => 'UAH'), snapshot: ref(null) })
  mode.startTrustlineFlow()
  mode.selectNode('alice')
  mode.selectNode('bob')
  await mode.confirmTrustlineCreate('10')
  return mode.state.error
}

describe('046 wave 1 - a trust line create the server refuses with CONFLICT and a reason (044 stage 2)', () => {
  it('participant_suspended: its own text, ru and en - not "the system is busy"', async () => {
    setUiLocale('ru')
    expect(await createRefused({ reason: 'participant_suspended', participants: ['bob'] })).toBe('Участник приостановлен — операции с этим участником недоступны.')
    setUiLocale('en')
    expect(await createRefused({ reason: 'participant_suspended', participants: ['bob'] })).toBe('The participant is suspended — operations with this participant are unavailable.')
  })

  it('the two reasons of the equivalent, as on the clearing path', async () => {
    setUiLocale('ru')
    expect(await createRefused({ reason: 'equivalent_inactive', equivalents: ['UAH'] })).toBe(t('actionReason.equivalent_inactive', {}, 'ru'))
    expect(await createRefused({ reason: 'equivalent_integrity_hold', equivalents: ['UAH'] })).toBe(t('actionReason.equivalent_integrity_hold', {}, 'ru'))
  })

  it('counter-check: a CONFLICT without such a reason is still the CONFLICT text', async () => {
    setUiLocale('en')
    expect(await createRefused({ reason: 'retryable' })).toBe(t('actionError.CONFLICT', {}, 'en'))
  })
})
