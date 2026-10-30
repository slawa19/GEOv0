/**
 * 049 S2 (decision 8), reproducer (ж): the five texts of the states and «Свободно по линиям» are the spec's words, in both
 * languages (`specs/049-demo-screen-polish/spec.md`, decision 8). The parity of the two dictionaries is held by
 * `dictionaryParity.policy.test.ts`; what is held here is the wording the owner's adviser chose - a dictionary value, the
 * product's own text, not a document. Red on `main` (`d1289867`): every one of them was the earlier wording.
 */
import { describe, expect, it } from 'vitest'

import { t, type MessageKey } from './t'

const SPEC: ReadonlyArray<[string, MessageKey, string, string]> = [
  ['the run failed', 'runLine.errorStopped',
    'Демо остановилось из-за сбоя. Перезагрузите страницу, чтобы начать новый прогон.',
    'The demo stopped because of an error. Reload the page to start a new run.'],
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
    const raw = (locale: 'ru' | 'en') => t(key, key === 'refusal.insufficientCapacityMax' ? { max: '{max}', eq: '{eq}' } : undefined, locale)
    expect(raw('ru')).toBe(ru)
    expect(raw('en')).toBe(en)
  })

  it('the run failure claims no more than it knows: no code in brackets; the stall alert is gone (054-B-7a)', () => {
    for (const locale of ['ru', 'en'] as const) {
      expect(t('runLine.errorStopped', { code: 'REAL_MODE_TICK_FAILED' }, locale)).not.toContain('REAL_MODE_TICK_FAILED')
      expect(t('runLine.runningError', { code: 'INTERNAL_ERROR' }, locale)).not.toContain('INTERNAL_ERROR')
      // A missing key renders as itself.
      expect(t('topBar.stall' as MessageKey, undefined, locale)).toBe('topBar.stall')
    }
  })
})
