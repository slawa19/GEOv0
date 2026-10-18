/**
 * 044 stage 2 (decision 3): the 409 of the global limit names the number of places, read from the answer's
 * `details.max_active_runs` (never hard-coded); without a usable number the earlier text stays.
 */
import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/http'
import { startRunErrorText } from './runErrorClassification'

const limit409 = (details: Record<string, unknown>) =>
  new ApiError('HTTP 409 Conflict for /simulator/runs', {
    status: 409,
    bodyText: JSON.stringify({ error: { code: 'E008', message: 'Global active runs limit reached: all 10 places are taken', details } }),
  })
const raw = (e: unknown) => String((e as Error).message)

describe('044 stage 2: the text of an exhausted global limit names the number', () => {
  it('10 places (the demo default): the number from the answer, Russian plural', () => {
    const e = limit409({ conflict_kind: 'global_active_limit', max_active_runs: 10, active_runs: 10 })
    expect(startRunErrorText(e, raw, 'ru')).toBe('Сейчас заняты все 10 мест: демо смотрят 10 человек. Попробуйте через минуту — Start в «Подробнее».')
    expect(startRunErrorText(e, raw, 'en')).toBe('All 10 places are taken right now: 10 people are watching the demo. Try again in a minute - Start under "Подробнее".')
  })

  it('the number is the server\'s, not a constant: 3, 21 and 1', () => {
    const t = (n: number) => startRunErrorText(limit409({ conflict_kind: 'global_active_limit', max_active_runs: n, active_runs: n }), raw, 'ru')
    expect(t(3)).toBe('Сейчас заняты все 3 места: демо смотрят 3 человека. Попробуйте через минуту — Start в «Подробнее».')
    expect(t(21)).toBe('Сейчас заняты все 21 место: демо смотрит 21 человек. Попробуйте через минуту — Start в «Подробнее».')
    expect(t(1)).toBe('Сейчас занято единственное место: демо смотрит 1 человек. Попробуйте через минуту — Start в «Подробнее».')
  })

  it('counter-check: no usable number keeps the earlier text; another conflict kind keeps its own message', () => {
    for (const bad of [undefined, 0, -2, 2.5, '10']) {
      expect(startRunErrorText(limit409({ conflict_kind: 'global_active_limit', max_active_runs: bad }), raw, 'ru')).toBe(
        'Демо уже кто-то смотрит на этом сервере; попробуйте позже. Запустить снова — Start в «Подробнее».',
      )
    }
    expect(startRunErrorText(limit409({ conflict_kind: 'owner_active_exists', max_active_runs: 10 }), raw, 'ru')).toBe(
      'HTTP 409 Conflict for /simulator/runs',
    )
  })
})
