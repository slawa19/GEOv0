/**
 * 049 decision 9 (owner's review of the final frames, 2026-10-20): in a history line only the participants' names give way
 * (`InteractHistoryLog.vue`: a name shrinks to its first letter and an ellipsis, a value never shrinks). So every line of
 * the dictionaries must fit the log's column with its names at their least - else its end is clipped. This guard holds
 * that for EVERY `history.*` and `feed.*` line in both languages, with the widest values a demo shows; the stand
 * (`e2e/qa/debt-before-after.qa.ts`) measures the pixels of the lines it draws, this one reaches the lines the stand
 * cannot draw (an own trust line created, changed or closed with a debt).
 *
 * The budget is a count of characters, valid for the hud's monospace only (the saas face is proportional and narrower):
 * the column is at most 448 px (`min(28rem, 45vw)`; 28rem at both 1366 and 1024 px), minus the icon (1.2em), the time
 * (8 characters at 0.7rem) and two gaps of 6 px - about 366 px of text; a character of CI's monospace (0.602em at 0.78rem)
 * is 7.51 px, so about 48.7 characters fit; 47 leaves about 13 px. What it does NOT see: real fonts and pixels (the stand
 * does, for its lines), amounts beyond ten characters, counts beyond two digits.
 *
 * A series (049 closing polish, advisor 2026-10-21) is measured as it is drawn: its lead («За 2 с: 12 отказов; ») WHOLE,
 * then the line the product writes after it (`feed.payment`, or the short failure line of `FAILURE_SERIES_LINE`), with the
 * names at their least. The lead still gives way after the names on screen; this guard holds that it never has to in the
 * hud - so the room «последний —» took is the names'.
 */
import { describe, expect, it } from 'vitest'

import { en } from '../i18n/en'
import { isPluralMessage } from '../i18n/messages'
import { t, type MessageKey, type PluralKey } from '../i18n/t'
import type { UiLocale } from '../i18n/locale'
import { FAILURE_SERIES_LINE, SERIES_FAILED_LINE, historyEntryParts, type InteractHistoryEntry } from './interact/useInteractHistory'

const BUDGET = 47
/** The widest values: a seven-digit amount or limit with its cents, a count of two digits. */
const VARS = { amount: '1000000.00', limit: '1000000.00', unit: 'UAH', from: 'X', to: 'X' }
const COUNTS = [1, 2, 5, 12, 21]
/** A lead is not a line of its own: a series' lead is measured with the lines it stands before (below), «вы» as the mark. */
const LEADS = new Set(['paymentSeries', 'failureSeries', 'you'])
/** A series has at least two events. */
const SERIES_COUNTS = COUNTS.filter((c) => c >= 2)

type Line = { name: string; line: InteractHistoryEntry['line']; lead?: InteractHistoryEntry['lead']; own: boolean }

function lines(): Line[] {
  const out: Line[] = []
  for (const [group, own] of [['history', true], ['feed', false]] as const) {
    for (const [k, m] of Object.entries(en[group])) {
      if (LEADS.has(k)) continue
      if (isPluralMessage(m)) for (const count of COUNTS) out.push({ name: `${group}.${k} (${count})`, line: { plural: `${group}.${k}` as PluralKey, count, vars: VARS }, own })
      else out.push({ name: `${group}.${k}`, line: { key: `${group}.${k}` as MessageKey, vars: VARS }, own })
    }
  }
  // The series lines, as `useInteractHistory.pushFeedLine` writes them: a payment series before `feed.payment`, a failure
  // series before the short failure line of its reason (or of an unknown one).
  const series: Array<[PluralKey, MessageKey]> = [
    ['feed.paymentSeries', 'feed.payment'],
    ...[...Object.values(FAILURE_SERIES_LINE), SERIES_FAILED_LINE].map((key) => ['feed.failureSeries', key] as [PluralKey, MessageKey]),
  ]
  for (const [lead, key] of series) {
    for (const count of SERIES_COUNTS) out.push({ name: `${lead} (${count}) + ${key}`, lead: { plural: lead, count }, line: { key, vars: VARS }, own: false })
  }
  return out
}

/**
 * The characters a line needs at the least: its values and a series' lead whole, each name as its first letter and the
 * ellipsis (2).
 */
function least(l: Line, locale: UiLocale): number {
  const parts = historyEntryParts({ line: l.line, ...(l.lead ? { lead: l.lead } : {}) }, locale)
  const values = parts.filter((p) => p.role !== 'name').reduce((n, p) => n + [...p.text].length, 0)
  const names = parts.filter((p) => p.role === 'name').length
  // An own line is marked «вы · » while the network's events are beside it - the widest case.
  const mark = l.own ? [...`${t('feed.you', {}, locale)} · `].length : 0
  return values + 2 * names + mark
}

describe('049 decision 9: every history line fits the column with its names at their least (hud monospace)', () => {
  for (const locale of ['ru', 'en'] as const) {
    it(`[${locale}] each line needs at most ${BUDGET} characters`, () => {
      const all = lines()
      // Anti-vacuum: the lines are read from the dictionary - own and network lines, plain and plural, with names.
      expect(all.filter((l) => l.own).length, 'counter-check: the own lines are measured').toBeGreaterThanOrEqual(6)
      expect(all.filter((l) => !l.own).length, 'counter-check: the network lines are measured').toBeGreaterThanOrEqual(10)
      // Anti-vacuum: both kinds of series are measured, each with its lead in the parts.
      const withLead = all.filter((l) => l.lead && historyEntryParts({ line: l.line, lead: l.lead }, locale)[0]?.role === 'lead')
      expect(withLead.filter((l) => l.name.startsWith('feed.paymentSeries')).length, 'counter-check: payment series are measured').toBeGreaterThanOrEqual(4)
      expect(withLead.filter((l) => l.name.startsWith('feed.failureSeries')).length, 'counter-check: failure series are measured').toBeGreaterThanOrEqual(24)
      expect(all.some((l) => historyEntryParts({ line: l.line }, locale).some((p) => p.role === 'name')), 'counter-check: names are found').toBe(true)
      const over = all.map((l) => ({ name: l.name, need: least(l, locale) })).filter((x) => x.need > BUDGET)
      expect(over, `[${locale}] lines whose values need more than ${BUDGET} characters. This guard checks a FORM, not the truth: `
        + 'a count of characters for the hud monospace only, each name taken at 2 characters, the lead of a series whole, no fonts '
        + 'and no pixels; the saas '
        + 'face and real pixels are judged by the stand for the lines it draws. What decides whether a line fits: the stand '
        + '(e2e/qa/debt-before-after.qa.ts, feedValuesWhole), npm --prefix simulator-ui/v2 run test:e2e:qa:gate. '
        + 'Shorten the line with the same meaning (both dictionaries), or show on the stand that it fits and move the budget '
        + 'with a dated reason').toEqual([])
    })
  }
})
