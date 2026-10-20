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
 * does, for its lines), a series' count (it gives way after the names), amounts beyond ten characters.
 */
import { describe, expect, it } from 'vitest'

import { en } from '../i18n/en'
import { isPluralMessage } from '../i18n/messages'
import { t, type MessageKey, type PluralKey } from '../i18n/t'
import type { UiLocale } from '../i18n/locale'
import { historyEntryParts, type InteractHistoryEntry } from './interact/useInteractHistory'

const BUDGET = 47
/** The widest values: a seven-digit amount or limit with its cents, a count of two digits. */
const VARS = { amount: '1000000.00', limit: '1000000.00', unit: 'UAH', from: 'X', to: 'X' }
const COUNTS = [1, 2, 5, 12, 21]
/** A series' lead is not a line: it gives way after the names (`InteractHistoryLog.vue`). */
const LEADS = new Set(['paymentSeries', 'failureSeries', 'you'])

type Line = { name: string; line: InteractHistoryEntry['line']; own: boolean }

function lines(): Line[] {
  const out: Line[] = []
  for (const [group, own] of [['history', true], ['feed', false]] as const) {
    for (const [k, m] of Object.entries(en[group])) {
      if (LEADS.has(k)) continue
      if (isPluralMessage(m)) for (const count of COUNTS) out.push({ name: `${group}.${k} (${count})`, line: { plural: `${group}.${k}` as PluralKey, count, vars: VARS }, own })
      else out.push({ name: `${group}.${k}`, line: { key: `${group}.${k}` as MessageKey, vars: VARS }, own })
    }
  }
  return out
}

/** The characters a line needs at the least: its values whole, each name as its first letter and the ellipsis (2). */
function least(l: Line, locale: UiLocale): number {
  const parts = historyEntryParts({ line: l.line }, locale)
  const values = parts.filter((p) => p.role === 'value').reduce((n, p) => n + [...p.text].length, 0)
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
      expect(all.some((l) => historyEntryParts({ line: l.line }, locale).some((p) => p.role === 'name')), 'counter-check: names are found').toBe(true)
      const over = all.map((l) => ({ name: l.name, need: least(l, locale) })).filter((x) => x.need > BUDGET)
      expect(over, `[${locale}] lines whose values do not fit ${BUDGET} characters`).toEqual([])
    })
  }
})
