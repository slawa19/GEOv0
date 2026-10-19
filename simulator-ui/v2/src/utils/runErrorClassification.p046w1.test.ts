/**
 * 046 wave 1 (T4602): the start the global limit of runs refused - recognised by the STRUCTURED answer only
 * (`details.conflict_kind`, `details.max_active_runs`; `run_lifecycle.py` sends both since 043 S2, unchanged by 044
 * stage 2 / PR #299), told in the language of the interface from the dictionary, without the old pointer to Start under
 * "Подробнее" (the line offers "Try again" itself).
 */
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/http'
import {
  isGlobalLimitRefusal,
  NO_SCENARIO_LINE,
  noScenarioText,
  rawRunErrorLine,
  renderRunErrorLine,
  runErrorLine,
  runErrorLineTitle,
  runStatusErrorLine,
  startRetryOffered,
  startRunErrorLine,
  startRunErrorText,
  writeRunErrorLine,
  ZERO_INTENSITY_LINE,
  type RunErrorLine,
} from './runErrorClassification'

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
  it('offered while the line IS the refused line (the same value); never by an equal text', () => {
    const refused = startRunErrorLine(limit({ max_active_runs: 10 }))!
    expect(startRetryOffered(refused, refused)).toBe(true)
    // The same text made again is another value: §15 Codex fix-delta P3 - identity is never recovered from the text.
    const sameText = startRunErrorLine(limit({ max_active_runs: 10 }))!
    expect(renderRunErrorLine(sameText, 'en')).toBe(renderRunErrorLine(refused, 'en'))
    expect(startRetryOffered(sameText, refused), 'an equal text is not the refusal').toBe(false)
    expect(startRetryOffered(rawRunErrorLine('SSE: connection lost'), refused), 'a later error replaced the line').toBe(false)
    expect(startRetryOffered(null, refused), 'the line was cleared').toBe(false)
    expect(startRetryOffered(refused, null), 'nothing refusable recorded').toBe(false)
    expect(startRetryOffered(null, undefined)).toBe(false)
  })
})

describe('046 wave 1 (§15 Codex) - every line of the run error line is a value rendered in either language', () => {
  it('no scenario, the clearing step refusal of a run, the idle stop, tempo 0, the limit refusal (count, one, none)', () => {
    const lines: RunErrorLine[] = [
      NO_SCENARIO_LINE,
      runErrorLine({ code: 'CLEARING_REFUSED', message: 'refused', at: '2026-10-18T00:00:00Z' }),
      runStatusErrorLine({ state: 'stopped', stop_reason: 'idle_no_subscribers' }, () => false)!,
      ZERO_INTENSITY_LINE,
      startRunErrorLine(limit({ max_active_runs: 4 }))!,
      startRunErrorLine(limit({ max_active_runs: 1 }))!,
      startRunErrorLine(limit({}))!,
    ]
    for (const line of lines) {
      expect(line.kind, JSON.stringify(line)).not.toBe('raw')
      const en = renderRunErrorLine(line, 'en')
      const ru = renderRunErrorLine(line, 'ru')
      expect(en, JSON.stringify(line)).not.toMatch(/[Ѐ-ӿ]/)
      expect(ru, JSON.stringify(line)).toMatch(/[Ѐ-ӿ]/)
    }
    expect(renderRunErrorLine(startRunErrorLine(limit({ max_active_runs: 4 }))!, 'ru')).toBe('Сейчас заняты все 4 места: демо смотрят 4 человека. Попробуйте через минуту.')
    // 048 S3 (decision 3), rewritten: the code of a run that goes on is no longer a raw `CODE: message` line (English prose
    // on a Russian screen) - a sentence of the language with the code; the server's text is the detail (title, console).
    const running = runErrorLine({ code: 'PAYMENT_TIMEOUT', message: 'timed out', at: '2026-10-18T00:00:00Z' })
    expect(running).toEqual({ kind: 'code', key: 'runLine.runningError', code: 'PAYMENT_TIMEOUT', detail: 'PAYMENT_TIMEOUT: timed out' })
    expect(renderRunErrorLine(running, 'ru')).toMatch(/[Ѐ-ӿ]/)
    expect(renderRunErrorLine(running, 'en')).not.toContain('timed out')
  })

  it('writeRunErrorLine writes the text of the language, then the value', () => {
    const holder: { lastError: string; lastErrorLine?: RunErrorLine | null } = { lastError: 'old' }
    writeRunErrorLine(holder, ZERO_INTENSITY_LINE, 'ru')
    expect(holder).toEqual({ lastError: 'Темп 0 — прогон без платежей: укажите темп от 1 до 100. Остановить платежи — пауза.', lastErrorLine: ZERO_INTENSITY_LINE })
    writeRunErrorLine(holder, null, 'ru')
    expect(holder).toEqual({ lastError: '', lastErrorLine: null })
  })
})

/**
 * 046 wave 1, full (048 S2 hand-over P2-2, 2026-10-19): since 048 S2 a visitor has no Start, and a run that ENDED left them
 * stuck without a word of what to do - only the idle stop said "reload the page". The line of the run now says it for a
 * stop by the operator and for a run ended by an error (with its code, in the interface language; 046 decision 3). The
 * administrator's own Stop (`user_stop`) and any other reason write no line, as before (no new state, no new control).
 * Red before on `main` `efc12f62`: an operator stop had no line (`null`), a run in `error` showed `CODE: message`.
 */
describe('046 wave 1, full - a run that ended says what to do (the reload), in the interface language', () => {
  const status = (state: string, stop_reason: string | null = null, last_error: { code: string; message: string; at: string } | null = null) =>
    ({ state, stop_reason, last_error })
  const always = () => true

  // Internal adversarial pass (2026-10-19, P2): the runbook's one-run stop (`POST /simulator/runs/{id}/stop`) sends NO
  // reason, and a stop-all may carry its own - a list of the operator's reasons wrote no line for them.
  it('a stop by anything but this screen\'s Stop - the operator\'s stop-all, a stop with no reason or another one: the reload', () => {
    for (const reason of ['admin_stop', 'admin_stop_all', null, '', 'maintenance window']) {
      const line = runStatusErrorLine(status('stopped', reason), always)
      expect(line, String(reason)).toEqual({ kind: 'dict', key: 'runLine.stopped' })
      expect(renderRunErrorLine(line!, 'en')).toBe('The run was stopped. To start again, reload the page.')
      expect(renderRunErrorLine(line!, 'ru')).toBe('Прогон остановлен. Чтобы запустить снова, перезагрузите страницу.')
    }
  })

  it('a run ended by an error: the code and the reload, not the server\'s English prose; the step refusal keeps its text', () => {
    const line = runStatusErrorLine(status('error', null, { code: 'REAL_MODE_TICK_FAILED', message: 'tick failed', at: 't' }), always)
    expect(line).toEqual({ kind: 'code', key: 'runLine.errorStopped', code: 'REAL_MODE_TICK_FAILED', detail: 'REAL_MODE_TICK_FAILED: tick failed' })
    expect(runErrorLineTitle(line), 'the server full text: the title of the line (048 decision 3)').toBe('REAL_MODE_TICK_FAILED: tick failed')
    expect(renderRunErrorLine(line!, 'en')).toBe('The run stopped with an error (REAL_MODE_TICK_FAILED). To start again, reload the page.')
    expect(renderRunErrorLine(line!, 'ru')).toBe('Прогон остановился с ошибкой (REAL_MODE_TICK_FAILED). Чтобы запустить снова, перезагрузите страницу.')
    expect(renderRunErrorLine(line!, 'ru')).not.toContain('tick failed')
    const step = runStatusErrorLine(status('error', null, { code: 'CLEARING_REFUSED', message: 'x', at: 't' }), always)
    expect(step).toEqual({ kind: 'dict', key: 'refusal.clearingStepNoEq' })
  })

  // 048 S3: "a live run keeps CODE: message" became "a live run's code is a sentence with the code" (decision 3).
  it('counter-checks: the idle stop keeps its line; the administrator\'s own Stop writes none; a live run keeps its code', () => {
    expect(runStatusErrorLine(status('stopped', 'idle_no_subscribers'), always)).toEqual({ kind: 'dict', key: 'runLine.idleStopped' })
    expect(runStatusErrorLine(status('stopped', 'user_stop'), always), 'Stop of this screen: Start is beside it').toBeNull()
    expect(runStatusErrorLine(status('running', 'admin_stop'), always), 'the reason of a run that is not stopped says nothing').toBeNull()
    expect(runStatusErrorLine(status('running', null, { code: 'INTERNAL_ERROR', message: 'boom', at: 't' }), always))
      .toEqual({ kind: 'code', key: 'runLine.runningError', code: 'INTERNAL_ERROR', detail: 'INTERNAL_ERROR: boom' })
  })
})

/**
 * §15 Codex round (advisor's condition, 2026-10-19; 048 decision 3, AGENTS.md section 12): the server's full text of a run
 * ended by an error goes to the console once - not on each status that repeats it, nor on a re-render after a switch of
 * the language; another error is logged again. No token or payload is logged: the code and the server's message only.
 */
describe('046 wave 1 - the server text of an ended run: one console line', () => {
  it('logged once with the code and the message; repeats and re-renders are silent; a new error logs again', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    try {
      const holder: { lastError: string; lastErrorLine: RunErrorLine | null } = { lastError: '', lastErrorLine: null }
      const ended = (message: string) => runStatusErrorLine({ state: 'error', last_error: { code: 'REAL_MODE_TICK_FAILED', message, at: 't' } }, () => true)
      writeRunErrorLine(holder, ended('tick failed'), 'en')
      writeRunErrorLine(holder, ended('tick failed'), 'en') // the REST refresh repeats the status
      writeRunErrorLine(holder, holder.lastErrorLine, 'ru') // a switch of the language re-renders the same value
      expect(warn.mock.calls).toEqual([['[run] stopped with an error:', 'REAL_MODE_TICK_FAILED: tick failed']])
      writeRunErrorLine(holder, ended('another'), 'en')
      expect(warn).toHaveBeenCalledTimes(2)
      // Counter-check: a line without a server text logs nothing.
      writeRunErrorLine(holder, ZERO_INTENSITY_LINE, 'en')
      expect(warn).toHaveBeenCalledTimes(2)
    } finally {
      warn.mockRestore()
    }
  })
})
