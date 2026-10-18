/**
 * 046 wave 1 (T4602): the start the global limit of runs refused - recognised by the STRUCTURED answer only
 * (`details.conflict_kind`, `details.max_active_runs`; `run_lifecycle.py` sends both since 043 S2, unchanged by 044
 * stage 2 / PR #299), told in the language of the interface from the dictionary, without the old pointer to Start under
 * "Подробнее" (the line offers "Try again" itself).
 */
import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/http'
import { isGlobalLimitRefusal, noScenarioText, startRetryOffered, startRunErrorText } from './runErrorClassification'

const answer = (status: number, body: unknown) =>
  new ApiError(`HTTP ${status} for /simulator/runs`, { status, bodyText: typeof body === 'string' ? body : JSON.stringify(body) })
const limit = (details: Record<string, unknown>) =>
  answer(409, { error: { code: 'E008', message: 'Global active runs limit reached', details: { conflict_kind: 'global_active_limit', ...details } } })
const raw = (e: unknown) => String((e as Error).message)

describe('046 wave 1 - the limit of runs: recognised by its details, never by its message', () => {
  it('a 409 with conflict_kind global_active_limit is the refusal', () => {
    expect(isGlobalLimitRefusal(limit({ max_active_runs: 10, active_runs: 10 }))).toBe(true)
    expect(isGlobalLimitRefusal(limit({}))).toBe(true)
  })

  it('counter-check: the message alone, another kind, another status, a body that is not the envelope - not the refusal', () => {
    expect(isGlobalLimitRefusal(answer(409, { error: { message: 'Global active runs limit reached: all 10 places are taken' } })), 'message only').toBe(false)
    expect(isGlobalLimitRefusal(answer(409, { error: { details: { conflict_kind: 'owner_active_exists' } } }))).toBe(false)
    expect(isGlobalLimitRefusal(answer(500, { error: { details: { conflict_kind: 'global_active_limit' } } }))).toBe(false)
    expect(isGlobalLimitRefusal(answer(409, 'not json'))).toBe(false)
    expect(isGlobalLimitRefusal(answer(409, { error: { details: ['global_active_limit'] } }))).toBe(false)
    expect(isGlobalLimitRefusal(new Error('global_active_limit'))).toBe(false)
  })
})

describe('046 wave 1 - the text of the refusal names the server\'s number of places', () => {
  it('en and ru, the number from the answer; Russian plural by Intl.PluralRules', () => {
    const at = (n: number, locale: 'en' | 'ru') => startRunErrorText(limit({ max_active_runs: n, active_runs: n }), raw, locale)
    expect(at(10, 'en')).toBe('All 10 places of the demo are taken right now: 10 people are watching it. Try again in a minute.')
    expect(at(10, 'ru')).toBe('Сейчас заняты все 10 мест: демо смотрят 10 человек. Попробуйте через минуту.')
    expect(at(3, 'ru')).toBe('Сейчас заняты все 3 места: демо смотрят 3 человека. Попробуйте через минуту.')
    expect(at(21, 'ru')).toBe('Сейчас заняты все 21 место: демо смотрит 21 человек. Попробуйте через минуту.')
    expect(at(1, 'ru')).toBe('Сейчас занято единственное место: демо смотрит 1 человек. Попробуйте через минуту.')
    expect(at(1, 'en')).toBe('The only place of the demo is taken right now: 1 person is watching it. Try again in a minute.')
  })

  it('no usable number: the text names none', () => {
    for (const bad of [undefined, 0, -2, 2.5, '10', null]) {
      expect(startRunErrorText(limit({ max_active_runs: bad }), raw, 'ru')).toBe('Демо уже кто-то смотрит на этом сервере; попробуйте позже.')
      expect(startRunErrorText(limit({ max_active_runs: bad }), raw, 'en')).toBe('Someone is already watching the demo on this server; try again later.')
    }
  })

  it('no text points at Start under "Подробнее" any more (the line offers its own retry), and none mixes the languages', () => {
    const texts = [1, 2, 10].flatMap((n) => (['en', 'ru'] as const).map((l) => startRunErrorText(limit({ max_active_runs: n }), raw, l)))
    texts.push(startRunErrorText(limit({}), raw, 'en'), startRunErrorText(limit({}), raw, 'ru'), noScenarioText('en'), noScenarioText('ru'))
    for (const s of texts) expect(s).not.toMatch(/Start|Подробнее/)
    for (const s of texts.filter((_, i) => i % 2 === 0)) expect(s, s).not.toMatch(/[Ѐ-ӿ]/)
  })

  it('counter-check: another error keeps the fallback text', () => {
    expect(startRunErrorText(answer(409, { error: { details: { conflict_kind: 'owner_active_exists' } } }), raw, 'ru')).toBe('HTTP 409 for /simulator/runs')
  })
})

describe('046 wave 1 - "Try again" stands only beside the refusal it would fix', () => {
  it('offered while the line is the refused text; withdrawn when another error replaces it, or nothing was refused', () => {
    const refused = 'All 10 places of the demo are taken right now: 10 people are watching it. Try again in a minute.'
    expect(startRetryOffered(refused, refused)).toBe(true)
    expect(startRetryOffered('SSE: connection lost', refused), 'a later error replaced the line').toBe(false)
    expect(startRetryOffered('', refused), 'the line was cleared').toBe(false)
    expect(startRetryOffered(refused, ''), 'nothing refusable recorded').toBe(false)
    expect(startRetryOffered('', ''), 'no line, nothing recorded: no offer').toBe(false)
    expect(startRetryOffered(null, undefined)).toBe(false)
  })
})
