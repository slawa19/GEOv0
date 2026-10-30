/**
 * 049 S2 (decision 8), reproducer (ж): the five texts of the states and «Свободно по линиям» are the spec's words, in both
 * languages (`specs/049-demo-screen-polish/spec.md`, decision 8). The parity of the two dictionaries is held by
 * `dictionaryParity.policy.test.ts`; what is held here is the wording the owner's adviser chose - a dictionary value, the
 * product's own text, not a document. Red on `main` (`d1289867`): every one of them was the earlier wording.
 */
import { describe, expect, it } from 'vitest'

import { t, type MessageKey } from './t'

const SPEC: ReadonlyArray<[string, MessageKey, string, string]> = [
  // 054-B-7b (the owner's decision of 2026-10-30): the code, the server's message and its time are in the text; a restart.
  ['the run failed', 'runLine.errorStopped',
    'Прогон остановлен: {code} — {message} ({at}). Перезапустите прогон.',
    'The run stopped: {code} — {message} ({at}). Restart the run.'],
  ['the stream is lost', 'failure.streamFailed',
    'Обновления сети прервались; показанные данные могут быть устаревшими. Перезагрузите страницу.',
    'Network updates stopped; the displayed data may be outdated. Reload the page.'],
  // 054-B-7a (the owner's decision of 2026-10-30): the stall alert is gone (the system tries the clearing itself); a run the
  // server paused because its scenario's automatic payments are used up says so, in the owner's words.
  ['the scenario payments are used up', 'runLine.scenarioExhausted',
    'Автоматические платежи сценария исчерпаны; платите вручную или перезапустите.',
    "The scenario's automatic payments are used up; pay by hand or restart."],
  ['the limit', 'refusal.insufficientCapacityMax',
    'Платёж не прошёл: сейчас доступно не больше {max} {eq}. Уменьшите сумму.',
    'Payment declined: at most {max} {eq} is available now. Reduce the amount.'],
  ['no balance', 'card.balanceUnavailable', 'Баланс не получен.', 'Balance unavailable.'],
  ['the figure of the free limits', 'systemBalance.available', 'Свободно по линиям', 'Free on lines'],
]

describe('049 S2 (ж): the texts of the five states and «Свободно по линиям», RU/EN', () => {
  it.each(SPEC)('%s (%s)', (_what, key, ru, en) => {
    // The template itself: `{max}`/`{eq}` stay placeholders (no vars given, they are filled with nothing).
    const keep: Partial<Record<MessageKey, Record<string, string>>> = {
      'refusal.insufficientCapacityMax': { max: '{max}', eq: '{eq}' },
      'runLine.errorStopped': { code: '{code}', message: '{message}', at: '{at}' },
    }
    const raw = (locale: 'ru' | 'en') => t(key, keep[key], locale)
    expect(raw('ru')).toBe(ru)
    expect(raw('en')).toBe(en)
  })

  // 054-B-7b (the owner's decision of 2026-10-30) turns the code half over: the code and the message are drawn - still not
  // in brackets («CODE — message»).
  it('the run failure names its code and message, not in brackets; the stall alert is gone (054-B-7a)', () => {
    for (const locale of ['ru', 'en'] as const) {
      const vars = { code: 'REAL_MODE_TICK_FAILED', message: 'tick failed', at: '2026-10-30T00:00:00Z' }
      expect(t('runLine.errorStopped', vars, locale)).toContain('REAL_MODE_TICK_FAILED — tick failed')
      expect(t('runLine.runningError', { ...vars, code: 'INTERNAL_ERROR' }, locale)).toContain('INTERNAL_ERROR — tick failed')
      expect(t('runLine.errorStopped', vars, locale)).not.toMatch(/\(\s*[A-Z_]{3,}\s*\)/)
      // A missing key renders as itself.
      expect(t('topBar.stall' as MessageKey, undefined, locale)).toBe('topBar.stall')
    }
  })
})
