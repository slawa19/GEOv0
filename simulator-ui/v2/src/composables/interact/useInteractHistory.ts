import { reactive } from 'vue'

import { equivalentPrecision } from '../../config/equivalentPrecision'
import { keptText, type KeptText, type MessageKey } from '../../i18n/t'
import type { UiLocale } from '../../i18n/locale'
import { formatMoney, moneyText } from '../../utils/money'
import type { RealEventFeedItem } from '../realEventPipeline'

/**
 * A line of the history. 046 wave 3 (BACKLOG 046-3): the line is KEPT as its dictionary key and data (`KeptText`), not as
 * a text - it is drawn in the language of the moment it is shown (`historyEntryText`), so a switch re-renders the lines
 * already there. Until wave 3 a line kept the language it was made in.
 *
 * 049 S2 (decision 5): two sources. `own` - a result of the person's own action, known locally and for certain (the lines
 * the history always had); `network` - an accepted SSE event of the run (the network's events, written only while «Show
 * events» is on). A network line of a series carries its `lead` («In 2 s: 6 payments; latest — ») before `line`.
 */
export type InteractHistoryEntry = {
  id: number
  icon: string
  line: KeptText
  lead?: KeptText
  timeText: string
  timestampMs: number
  /** The order of the last write: the own and the network lines are shown merged by it. */
  seq: number
  source: 'own' | 'network'
  /**
   * An own line written while the network's events were on, whose event the network's lines carry as well (a committed
   * payment, a clearing that cleared - both are published to the run's stream): shown only while the events are off, so
   * the event is one line, not two (decision 5, «одна строка на событие»).
   */
  shownByNetwork?: boolean
  /** A network line of a series: what it counts and since when (the 2-second window starts at its first event). */
  series?: { kind: 'payment' | 'failure'; startMs: number; count: number }
}

/** 049 S2 (decision 5): payments (and, apart, failures) arriving within this window of the first one are one line. */
export const FEED_SERIES_WINDOW_MS = 2000
/** Lines shown: today's three own lines with the events off, five with them on (decision 5). */
export const OWN_LINES_SHOWN = 3
export const FEED_LINES_SHOWN = 5

/** The text of a history line in `locale` (the language of the interface by default). */
export function historyEntryText(entry: Pick<InteractHistoryEntry, 'line' | 'lead'>, locale?: UiLocale): string {
  return (entry.lead ? keptText(entry.lead, locale) : '') + keptText(entry.line, locale)
}

/** The reason words of a failure by its code; any other code is a failure without a reason - no diagnosis is guessed. */
const FAILURE_LINE: Readonly<Record<string, MessageKey>> = Object.freeze({
  ROUTING_NO_CAPACITY: 'feed.failedNoCapacity',
  ROUTING_NO_ROUTE: 'feed.failedNoRoute',
  TRUSTLINE_LIMIT_EXCEEDED: 'feed.failedLimit',
  TRUSTLINE_NOT_ACTIVE: 'feed.failedLineInactive',
  PAYMENT_TIMEOUT: 'feed.failedTimeout',
})

export type FeedLine = { kind: RealEventFeedItem['kind']; icon: string; line: KeptText }

/**
 * 049 S2 (decision 5): the line of an accepted event, or null when the event does not carry what the line says (a line is
 * never completed by a guess). Amounts are the event's own strings and the pair difference the pipeline took
 * (`debtChange.ts`), rendered at the equivalent's precision - no arithmetic here. `name` prints a participant id.
 */
export function feedLineOf(item: RealEventFeedItem, name: (pid: string) => string): FeedLine | null {
  const unit = item.equivalent
  const money = (v: string) => formatMoney(v, equivalentPrecision(unit))
  switch (item.kind) {
    case 'payment': {
      if (moneyText(item.amount) === null) return null
      return { kind: 'payment', icon: '💸', line: { key: 'feed.payment', vars: { from: name(item.from), to: name(item.to), amount: money(item.amount), unit } } }
    }
    case 'failure': {
      const key = FAILURE_LINE[item.code.toUpperCase()] ?? 'feed.failed'
      return { kind: 'failure', icon: '✕', line: { key, vars: { from: name(item.from), to: name(item.to) } } }
    }
    case 'clearing': {
      const drop = item.debtDrop !== null && moneyText(item.debtDrop) !== null ? money(item.debtDrop) : null
      const cycles = item.cycles !== null && item.cycles > 0 ? item.cycles : null
      if (drop !== null && cycles !== null) return { kind: 'clearing', icon: '🌀', line: { plural: 'feed.clearing', count: cycles, vars: { amount: drop, unit } } }
      if (cycles !== null) return { kind: 'clearing', icon: '🌀', line: { plural: 'feed.clearingCycles', count: cycles } }
      if (drop !== null) return { kind: 'clearing', icon: '🌀', line: { key: 'feed.clearingDebt', vars: { amount: drop, unit } } }
      return null
    }
    case 'trustGrowth':
      return { kind: 'trustGrowth', icon: '📈', line: { key: 'feed.trustGrowth' } }
  }
}

export function useInteractHistory(o?: { max?: number; now?: () => number }) {
  const max = typeof o?.max === 'number' && o.max > 0 ? Math.floor(o.max) : 20
  const now = o?.now ?? (() => Date.now())

  /** The person's own actions (as before 049). */
  const history = reactive<InteractHistoryEntry[]>([])
  /** 049 S2: the network's events (written by the caller only while «Show events» is on). */
  const feed = reactive<InteractHistoryEntry[]>([])
  let nextHistoryId = 1
  let nextSeq = 1

  function timeTextOf(ms: number): string {
    const dt = new Date(ms)
    return `${String(dt.getHours()).padStart(2, '0')}:${String(dt.getMinutes()).padStart(2, '0')}:${String(dt.getSeconds()).padStart(2, '0')}`
  }

  function pushHistory(icon: string, line: KeptText, opts?: { shownByNetwork?: boolean }) {
    const nowMs = now()
    history.push({
      id: nextHistoryId++,
      icon,
      line,
      timeText: timeTextOf(nowMs),
      timestampMs: nowMs,
      seq: nextSeq++,
      source: 'own',
      ...(opts?.shownByNetwork ? { shownByNetwork: true } : {}),
    })
    if (history.length > max) history.splice(0, history.length - max)
  }

  /**
   * A network line. A payment (or a failure) arriving within `FEED_SERIES_WINDOW_MS` of the first event of the latest
   * series of its kind joins it: the series is one line, counted, with the latest event after its lead, moved to the top.
   */
  function pushFeedLine(l: FeedLine) {
    const nowMs = now()
    const seriesKind = l.kind === 'payment' || l.kind === 'failure' ? l.kind : null
    if (seriesKind) {
      let at = -1
      for (let i = feed.length - 1; i >= 0; i -= 1) {
        if (feed[i]!.series?.kind === seriesKind) {
          at = i
          break
        }
      }
      const open = at >= 0 ? feed[at]! : null
      if (open?.series && nowMs - open.series.startMs < FEED_SERIES_WINDOW_MS) {
        const count = open.series.count + 1
        feed.splice(at, 1)
        feed.push({
          ...open,
          line: l.line,
          lead: { plural: seriesKind === 'payment' ? 'feed.paymentSeries' : 'feed.failureSeries', count },
          timeText: timeTextOf(nowMs),
          timestampMs: nowMs,
          seq: nextSeq++,
          series: { kind: seriesKind, startMs: open.series.startMs, count },
        })
        return
      }
    }
    feed.push({
      id: nextHistoryId++,
      icon: l.icon,
      line: l.line,
      timeText: timeTextOf(nowMs),
      timestampMs: nowMs,
      seq: nextSeq++,
      source: 'network',
      ...(seriesKind ? { series: { kind: seriesKind, startMs: nowMs, count: 1 } } : {}),
    })
    if (feed.length > max) feed.splice(0, feed.length - max)
  }

  /**
   * Drops the network's lines. The own lines they stood in for come back (internal adversarial pass, 2026-10-20): an own
   * line hidden because the network's line carried its event (`shownByNetwork`) would otherwise stay hidden after the
   * network's line is gone - the switch turned off and on, another run or equivalent - and the person's own payment or
   * clearing would leave no line at all.
   */
  function clearFeed() {
    if (feed.length) feed.splice(0, feed.length)
    for (const e of history) if (e.shownByNetwork) e.shownByNetwork = false
  }

  /**
   * Drops every line, own and network (049 decision 5, «смена прогона или эквивалента очищает строки»; closing Codex review,
   * 2026-10-20): the lines of another run or equivalent say nothing of the one on screen - an own payment of the old run
   * must not come back under «События сети». The switch alone is `clearFeed`: the own lines are the current run's.
   */
  function clearAll() {
    if (feed.length) feed.splice(0, feed.length)
    if (history.length) history.splice(0, history.length)
  }

  /**
   * What the history shows, oldest first: with the events off exactly the own lines (as before 049); with them on the own
   * lines and the network's merged by the order of writing, without the own lines the network carries.
   */
  function shownEntries(feedOn: boolean): InteractHistoryEntry[] {
    if (!feedOn) return history
    return [...history.filter((e) => !e.shownByNetwork), ...feed].sort((a, b) => a.seq - b.seq)
  }

  return { history, feed, pushHistory, pushFeedLine, clearFeed, clearAll, shownEntries }
}
