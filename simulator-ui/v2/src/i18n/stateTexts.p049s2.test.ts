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
  ['the payments stall', 'topBar.stall',
    'Последние платежи не проходят. Попробуйте клиринг или увеличьте лимит нужной линии.',
    "Recent payments are not going through. Try clearing or increase the relevant trustline's limit."],
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

  it('the run failure and the stall claim no more than they know: no code in brackets, no «limits are exhausted»', () => {
    for (const locale of ['ru', 'en'] as const) {
      expect(t('runLine.errorStopped', { code: 'REAL_MODE_TICK_FAILED' }, locale)).not.toContain('REAL_MODE_TICK_FAILED')
      expect(t('runLine.runningError', { code: 'INTERNAL_ERROR' }, locale)).not.toContain('INTERNAL_ERROR')
      expect(t('topBar.stall', undefined, locale)).not.toMatch(/исчерпаны|exhausted/)
    }
  })
})
