/**
 * Internal adversarial review of programme 013 - the three admin-ui graph-analytics findings.
 *
 * F-013-R1. The batch that taught the Activity card to say "we were not told" wired the METRICS
 * branch of `selectedActivity` to `m.activity.has_transactions`, which is a MEASURED zero
 * (`app/core/admin/metrics.py`: `has_transactions = len(tx_rows) > 0` over committed
 * PAYMENT/CLEARING in a 90-day window). The client-side flag it was mapped onto means "the response
 * named this collection at all". Merging them made a quiet-but-measured real system report
 * "Transaction activity was not requested" and print "- / - / -" over counters the server had
 * actually computed. The programme's own defect class, inverted, introduced by its own fix.
 *
 * F-013-R2. `incidentCount` and `participantOps` are derived from two snapshot collections the
 * client does not ask for, so on the fallback branch they are structurally zero in real mode and
 * printed as bare numbers beside counters that now honestly say "-" or ">=N". Same for the drawer's
 * incident-ratio row, which prints a hard `0.00` for every participant in real mode.
 *
 * F-013-R3. The consumer's reads of the producer's `from`/`to` (payment) and `edges` (clearing)
 * had no test: every row in the sibling suite was built by a helper that never sets those fields,
 * so both reads could be reverted to the old payload-only form with the whole suite still green.
 * And `if (from || to)` called a row attributable when only ONE of the pair was present, so a
 * payment carrying `from` alone was recorded as "not this participant's" on the strength of a
 * field nobody sent.
 *
 * F-013-R4 (EXTERNAL review). The flag F-013-R3 introduced - "some row in this collection could not
 * be attributed" - was one boolean for the whole `transactions` collection, read by the payment cell
 * and the clearing cell alike and by all three windows of each. So one committed payment with no
 * published recipient erased a clearing count of zero that had been reached without ever consulting
 * it, in windows that payment was far too old to belong to. The doubt is now recorded where it is
 * displayed: per counter, and per window.
 *
 * WHY THE RENDERED STRING AND NOT THE FLAGS. Every assertion below states what the operator reads.
 * The two adapters (`renderCounts`, `renderRatio`) are the only place a field name appears; each is
 * the production call site copied verbatim, so an assertion cannot drift into describing the
 * implementation. During the red phase they are the OLD call sites, which is what makes the red
 * output a statement about behaviour ("expected '- / - / -' to be '0 / 0 / 0'") rather than a
 * TypeError about a renamed key.
 *
 * WHY THIS FILE AND NOT `useGraphData.test.ts` / a drawer test: this batch's editable surface is
 * `useGraphAnalytics*.test.ts`, and the behaviour under test is produced by `useGraphAnalytics`
 * and rendered by two one-line helpers in `graphPageHelpers`.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'

const apiMock = vi.hoisted(() => ({
  participantMetrics: vi.fn(),
}))

vi.mock('../api', () => ({ api: apiMock }))

import { useGraphAnalytics } from './useGraphAnalytics'
import {
  activityCounts,
  activityNotices,
  collectionConfidence,
  incidentRatioDisplay,
  type CountConfidence,
} from '../pages/graph/graphPageHelpers'
import { EN } from '../i18n/en'
import { RU } from '../i18n/ru'
import type { SelectedInfo } from './useGraphVisualization'
import type {
  AuditLogEntry,
  ClearingCycles,
  Debt,
  Incident,
  Participant,
  Transaction,
  Trustline,
} from '../pages/graph/graphTypes'
import type { ParticipantMetrics } from '../types/domain'

const PID = 'PID_A'
const OTHER = 'PID_B'
const THIRD = 'PID_C'
const NOW_MS = Date.parse('2026-09-10T12:00:00Z')

function daysBefore(days: number): string {
  return new Date(NOW_MS - days * 24 * 60 * 60 * 1000).toISOString()
}

type Analytics = ReturnType<typeof useGraphAnalytics>
type Activity = NonNullable<Analytics['selectedActivity']['value']>

/**
 * The Activity card's counter cell, verbatim from GraphAnalyticsDrawer.vue / RiskTab.vue.
 * `which` names the confidence the counter is displayed at - one per COUNTER since F-013-R4, so
 * naming the wrong one here is a test that describes a different cell than it claims to.
 */
function renderCounts(a: Activity, counts: Record<number, number>, which: 'payments' | 'clearings' | 'incidents' | 'auditLog'): string {
  return activityCounts(counts, a.windows, a[which])
}

/** The drawer's incident-ratio row, verbatim from GraphAnalyticsDrawer.vue. */
function renderRatio(a: Activity, ratioByPid: Map<string, number>, pid: string): string {
  return incidentRatioDisplay(ratioByPid.get(pid), a.snapshotIncidents)
}

function analyticsFor(input: {
  transactions?: Transaction[]
  trustlines?: Trustline[]
  incidents?: Incident[]
  auditLog?: AuditLogEntry[]
  included?: string[]
  truncated?: string[]
  realMode?: boolean
  /**
   * The equivalent the operator has selected. Defaults to EUR, as every test above assumes; the
   * F-013-R6 block below needs the other two settings of this control - a DIFFERENT equivalent,
   * and none at all - because the row-level doubt it is about exists only while one is selected.
   */
  eq?: string | null
}) {
  const eq = input.eq === undefined ? 'EUR' : input.eq
  return useGraphAnalytics({
    isRealMode: computed(() => input.realMode ?? true),
    threshold: ref('0.10'),
    analyticsEq: computed(() => eq),

    precisionByEq: computed(() => new Map([['EUR', 2], ['UAH', 2]])),
    availableEquivalents: computed(() => ['EUR', 'UAH']),
    participantByPid: computed(() => new Map<string, Participant>()),

    participants: ref<Participant[]>([{ pid: PID, display_name: 'Alice' }]),
    trustlines: ref<Trustline[]>(input.trustlines || []),
    debts: ref<Debt[]>([]),
    incidents: ref<Incident[]>(input.incidents || []),
    auditLog: ref<AuditLogEntry[]>(input.auditLog || []),
    transactions: ref<Transaction[]>(input.transactions || []),
    included: ref<string[]>(input.included || []),
    truncated: ref<string[]>(input.truncated || []),
    clearingCycles: ref<ClearingCycles | null>(null),

    selected: ref<SelectedInfo | null>({ kind: 'node', pid: PID, degree: 0, inDegree: 0, outDegree: 0 }),
  })
}

/**
 * A row exactly as `_graph_fetch_transactions` puts it on the wire: no `payload`, `equivalent`
 * lifted to the top level, and - since 2026-09-10 - `from`/`to` on a payment and `edges` on a
 * clearing. `Transaction` in graphTypes does not declare the last three, which is why the
 * production reader casts; the tests build them the same way rather than pretending otherwise.
 */
function producerRow(over: Record<string, unknown> = {}): Transaction {
  return {
    tx_id: 'tx-1',
    type: 'PAYMENT',
    state: 'COMMITTED',
    initiator_pid: OTHER,
    created_at: daysBefore(1),
    updated_at: daysBefore(1),
    equivalent: 'EUR',
    error: null,
    ...over,
  } as unknown as Transaction
}

/**
 * The metrics endpoint's answer. Every counter here was computed server-side over the whole table
 * for this participant; none of it depends on the snapshot's `include`.
 */
function metricsEnvelope(activity: NonNullable<ParticipantMetrics['activity']>) {
  const metrics: ParticipantMetrics = {
    pid: PID,
    equivalent: 'EUR',
    balance_rows: [],
    activity,
  }
  return { success: true as const, data: metrics }
}

const QUIET_SYSTEM = {
  windows: [7, 30, 90],
  trustline_created: { 7: 0, 30: 1, 90: 4 },
  trustline_closed: { 7: 0, 30: 0, 90: 1 },
  incident_count: { 7: 0, 30: 1, 90: 2 },
  participant_ops: { 7: 1, 30: 1, 90: 3 },
  payment_committed: { 7: 0, 30: 0, 90: 0 },
  clearing_committed: { 7: 0, 30: 0, 90: 0 },
  // The 90-day window held no committed PAYMENT or CLEARING at all. A MEASUREMENT, taken by
  // `len(tx_rows) > 0` - not a statement about what this client asked for.
  has_transactions: false,
}

const BUSY_SYSTEM = {
  ...QUIET_SYSTEM,
  // Deliberately different per window and different between the two rows, so a reader that
  // confuses payments with clearings, or reads the wrong window, cannot pass.
  payment_committed: { 7: 1, 30: 3, 90: 5 },
  clearing_committed: { 7: 0, 30: 2, 90: 4 },
  has_transactions: true,
}

async function withMetrics(
  g: ReturnType<typeof analyticsFor>,
  activity: NonNullable<ParticipantMetrics['activity']>,
): Promise<Activity> {
  apiMock.participantMetrics.mockResolvedValue(metricsEnvelope(activity))
  await g.loadSelectedMetrics()
  const a = g.selectedActivity.value
  expect(a, 'the metrics branch must be the one under test').toBeTruthy()
  return a as Activity
}

beforeEach(() => {
  apiMock.participantMetrics.mockReset()
})

// =================================================================================================
// F-013-R1. A measured zero says zero. On BOTH branches.
// =================================================================================================
describe('F-013-R1: the metrics branch measures, so its zeros are zeros', () => {
  it('a quiet real system prints 0 / 0 / 0, not "we were not told"', async () => {
    // The snapshot deliberately carries NOTHING: `included: []`. The metrics endpoint answered
    // anyway, and its answer is what the card shows. A fix that derives this branch's confidence
    // from the snapshot's `included` would print dashes here and is exactly as wrong as the
    // defect - which is why this fixture leaves `included` empty.
    const g = analyticsFor({ included: [], truncated: [] })
    const a = await withMetrics(g, QUIET_SYSTEM)

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('0 / 0 / 0')
    expect(renderCounts(a, a.clearingCommitted, 'clearings')).toBe('0 / 0 / 0')

    // And the card must not raise "Transaction activity was not requested" over a measurement.
    expect(a.payments.known).toBe(true)
    expect(a.payments.incompleteWindows).toEqual([])
  })

  it('a busy real system prints the measured numbers, never as lower bounds', async () => {
    // `truncated: ['transactions']` describes a SNAPSHOT that was cut. The metrics endpoint does
    // not paginate its counters, so a fix that lets the snapshot's `truncated` reach this branch
    // would stamp ">=" on totals that are exact.
    const g = analyticsFor({ included: ['transactions'], truncated: ['transactions'] })
    const a = await withMetrics(g, BUSY_SYSTEM)

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('1 / 3 / 5')
    expect(renderCounts(a, a.clearingCommitted, 'clearings')).toBe('0 / 2 / 4')
    expect(a.payments.lowerBound).toBe(false)
  })

  it('the snapshot-derived branch still says "we were not told" when nothing was included', () => {
    // The other half of the pair: identical empty `transactions` array, no metrics answer, and the
    // conclusion must be the opposite one. This is what stops the R1 fix from being "always known".
    const g = analyticsFor({ included: [], truncated: [] })
    const a = g.selectedActivity.value as Activity

    expect(a.payments.known).toBe(false)
    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('— / — / —')
  })
})

// =================================================================================================
// F-013-R2. The other two collections, and the incident ratio.
// =================================================================================================
describe('F-013-R2: unasked collections must not print bare zeros', () => {
  const anchor: Trustline[] = [
    // Anchors the fallback branch's "now" without belonging to PID_A, whose counters must stay
    // driven by the incident and audit rows alone.
    { from: OTHER, to: THIRD, equivalent: 'EUR', limit: '1.00', used: '0.00', available: '1.00', status: 'active', created_at: daysBefore(0) },
  ]

  const incidents: Incident[] = [
    { tx_id: 'i-1', state: 'PREPARED', initiator_pid: PID, equivalent: 'EUR', age_seconds: 10, sla_seconds: 100, created_at: daysBefore(1) },
    { tx_id: 'i-2', state: 'PREPARED', initiator_pid: PID, equivalent: 'EUR', age_seconds: 30, sla_seconds: 100, created_at: daysBefore(15) },
    { tx_id: 'i-3', state: 'PREPARED', initiator_pid: PID, equivalent: 'EUR', age_seconds: 75, sla_seconds: 100, created_at: daysBefore(60) },
    // Neither of these belongs to the counter: wrong participant, wrong equivalent.
    { tx_id: 'i-4', state: 'PREPARED', initiator_pid: OTHER, equivalent: 'EUR', age_seconds: 99, sla_seconds: 100, created_at: daysBefore(1) },
    { tx_id: 'i-5', state: 'PREPARED', initiator_pid: PID, equivalent: 'UAH', age_seconds: 99, sla_seconds: 100, created_at: daysBefore(1) },
  ]

  const auditLog: AuditLogEntry[] = [
    { id: 'a-1', timestamp: daysBefore(2), action: 'admin.participants.update', object_type: 'participant', object_id: PID },
    { id: 'a-2', timestamp: daysBefore(45), action: 'admin.participants.block', object_type: 'participant', object_id: PID },
    { id: 'a-3', timestamp: daysBefore(2), action: 'admin.config.patch', object_type: 'config', object_id: PID },
    { id: 'a-4', timestamp: daysBefore(2), action: 'admin.participants.update', object_type: 'participant', object_id: OTHER },
  ]

  it('a snapshot that carries only transactions says nothing about incidents or participant ops', () => {
    // This is what real mode asks for today. The rows below are present in the composable's inputs
    // but the response never claimed to carry them, so the counters are not measurements.
    const g = analyticsFor({ trustlines: anchor, incidents, auditLog, included: ['transactions'], truncated: [] })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.incidentCount, 'incidents')).toBe('— / — / —')
    expect(renderCounts(a, a.participantOps, 'auditLog')).toBe('— / — / —')
    // ... while the collection that WAS carried still prints numbers. A blanket "everything is
    // unknown" fix would pass the two lines above and fail this one.
    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('0 / 0 / 0')
  })

  it('a snapshot that names all three prints the counts it actually measured', () => {
    const g = analyticsFor({
      trustlines: anchor,
      incidents,
      auditLog,
      included: ['transactions', 'incidents', 'audit_log'],
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    // 1 / 15 / 60 days old -> 1 / 2 / 3, and the foreign-participant and foreign-equivalent rows
    // are excluded. Distinct from the audit numbers below on purpose: a reader that crosses the
    // two collections cannot produce both lines.
    expect(renderCounts(a, a.incidentCount, 'incidents')).toBe('1 / 2 / 3')
    // 2 and 45 days old, and only the `admin.participants.` actions for THIS object_id -> 1 / 1 / 2.
    expect(renderCounts(a, a.participantOps, 'auditLog')).toBe('1 / 1 / 2')
  })

  it('a cut collection turns its counters into lower bounds', () => {
    const g = analyticsFor({
      trustlines: anchor,
      incidents,
      auditLog,
      included: ['incidents', 'audit_log'],
      truncated: ['incidents'],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.incidentCount, 'incidents')).toBe('≥1 / ≥2 / ≥3')
    // The cut is per collection: audit_log was included and NOT cut, so its counters stay exact.
    expect(renderCounts(a, a.participantOps, 'auditLog')).toBe('1 / 1 / 2')
  })

  it('and the same with the roles REVERSED: a cut audit log, an intact incident list', () => {
    // THE SAME PAIR, THE OTHER WAY ROUND (external review of 013, finding 2). Until this case
    // existed, `snapshotAuditLog` could be written `collectionConfidence('audit_log', included, [])`
    // - its `truncated` argument thrown away - and all 21 confidence assertions still passed,
    // because every fixture in the file truncated INCIDENTS and left audit_log whole. A collection
    // whose cut is never exercised is a collection whose cut is not tested, however many assertions
    // stand around it.
    const g = analyticsFor({
      trustlines: anchor,
      incidents,
      auditLog,
      included: ['incidents', 'audit_log'],
      truncated: ['audit_log'],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.participantOps, 'auditLog')).toBe('≥1 / ≥1 / ≥2')
    expect(renderCounts(a, a.incidentCount, 'incidents')).toBe('1 / 2 / 3')
  })

  it('the metrics branch measures these two as well, whatever the snapshot carried', async () => {
    const g = analyticsFor({ trustlines: anchor, incidents, auditLog, included: [], truncated: [] })
    const a = await withMetrics(g, QUIET_SYSTEM)

    // Server-side counters over the whole table (`metrics.py`: incident_count, participant_ops).
    // They owe nothing to the snapshot's include, so an empty `included` must not blank them.
    expect(renderCounts(a, a.incidentCount, 'incidents')).toBe('0 / 1 / 2')
    expect(renderCounts(a, a.participantOps, 'auditLog')).toBe('1 / 1 / 3')
  })

  describe('the drawer incident ratio', () => {
    const ratios = new Map<string, number>([[PID, 0.75]])

    it('prints nothing when the snapshot never carried incidents', () => {
      const g = analyticsFor({ trustlines: anchor, incidents, included: ['transactions'], truncated: [] })
      const a = g.selectedActivity.value as Activity

      // Today's expression is `(incidentRatioByPid.get(pid) || 0).toFixed(2)`, which prints a hard
      // 0.00 for EVERY participant in real mode - a measurement-shaped glyph over an empty map.
      expect(renderRatio(a, ratios, PID)).toBe('—')
      expect(renderRatio(a, ratios, OTHER)).toBe('—')
    })

    it('prints the measured ratio, and a measured 0.00, when incidents were carried whole', () => {
      const g = analyticsFor({ trustlines: anchor, incidents, included: ['incidents'], truncated: [] })
      const a = g.selectedActivity.value as Activity

      expect(renderRatio(a, ratios, PID)).toBe('0.75')
      // Absent from the map, and the list was complete: this zero is a real measurement.
      expect(renderRatio(a, ratios, OTHER)).toBe('0.00')
    })

    it('prints a lower bound when the incident list was cut, and nothing for a participant not in it', () => {
      const g = analyticsFor({ trustlines: anchor, incidents, included: ['incidents'], truncated: ['incidents'] })
      const a = g.selectedActivity.value as Activity

      // The worst ratio we hold is the worst of a PREFIX, so it can only rise.
      expect(renderRatio(a, ratios, PID)).toBe('≥0.75')
      // And a participant missing from a cut list may simply be in the part that was cut.
      expect(renderRatio(a, ratios, OTHER)).toBe('—')
    })
  })

  it('the metrics branch does not launder the snapshot incident collection', async () => {
    // The ratio row is fed by the snapshot's `incidents` on EVERY branch - the metrics endpoint
    // publishes no such ratio. So a metrics answer must not make an uncarried collection look
    // carried, even though it does settle the card's incident COUNTERS above.
    const g = analyticsFor({ trustlines: anchor, incidents, included: [], truncated: [] })
    const a = await withMetrics(g, QUIET_SYSTEM)

    expect(renderRatio(a, new Map([[PID, 0.75]]), PID)).toBe('—')
    expect(renderCounts(a, a.incidentCount, 'incidents')).toBe('0 / 1 / 2')
  })
})

// =================================================================================================
// F-013-R3. The client half of `from` / `to` / `edges`.
// =================================================================================================
describe('F-013-R3: the attribution fields the producer publishes are actually read', () => {
  const included = ['transactions']

  it('counts a payment this participant RECEIVED, which only `to` can reveal', () => {
    // Nothing else in the row points at PID_A: another participant initiated it and another sent
    // it. Reverting the reader to `payload.to` - the shape before 2026-09-10 - loses this row and
    // reports zero payments for someone who was paid.
    const g = analyticsFor({
      transactions: [
        producerRow({ tx_id: 'in', from: THIRD, to: PID, initiator_pid: THIRD }),
        // A second row that must NOT be counted, so a reader that simply counts rows fails.
        producerRow({ tx_id: 'elsewhere', from: THIRD, to: OTHER, initiator_pid: THIRD }),
      ],
      included,
    })
    const a = g.selectedActivity.value as Activity

    expect(a.paymentCommitted[7]).toBe(1)
    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('1 / 1 / 1')
    expect(a.payments.incompleteWindows).toEqual([])
  })

  it('counts a clearing this participant is only an EDGE of, which only `edges` can reveal', () => {
    const g = analyticsFor({
      transactions: [
        producerRow({
          tx_id: 'cl-in',
          type: 'CLEARING',
          initiator_pid: THIRD,
          edges: [
            { debtor: OTHER, creditor: THIRD },
            { debtor: THIRD, creditor: PID },
          ],
        }),
        producerRow({
          tx_id: 'cl-elsewhere',
          type: 'CLEARING',
          initiator_pid: THIRD,
          edges: [{ debtor: OTHER, creditor: THIRD }],
        }),
      ],
      included,
    })
    const a = g.selectedActivity.value as Activity

    expect(a.clearingCommitted[7]).toBe(1)
    expect(renderCounts(a, a.clearingCommitted, 'clearings')).toBe('1 / 1 / 1')
    // The second clearing names its full cycle and PID_A is not in it: that is a real "no", so
    // nothing about this pair is unknown.
    expect(a.clearings.incompleteWindows).toEqual([])
  })

  it('a payment carrying `from` alone cannot be ruled out, and is not silently counted as absent', () => {
    // THE SUB-FINDING. `if (from || to)` accepted this row as attributable and concluded "not this
    // participant's" from `to`, a field the producer never sent (`item["to"]` is only set when the
    // internal payload has a non-empty `to`). A confident zero built on a missing field.
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'half', from: THIRD, initiator_pid: THIRD })],
      included,
    })
    const a = g.selectedActivity.value as Activity

    expect(a.paymentCommitted[7]).toBe(0)
    expect(a.payments.incompleteWindows).toEqual([7, 30, 90])
    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('— / — / —')
  })

  it('but `from` alone still settles the POSITIVE case, so the fix is not "require both"', () => {
    // The obvious over-correction - `if (from && to)` - would call this row unattributable and
    // blank a payment we can see this participant sent. One half of the pair naming the
    // participant is proof; only the absence of a naming needs the other half.
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'sent', from: PID, initiator_pid: THIRD })],
      included,
    })
    const a = g.selectedActivity.value as Activity

    expect(a.paymentCommitted[7]).toBe(1)
    expect(a.payments.incompleteWindows).toEqual([])
    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('1 / 1 / 1')
  })

  it('`to` alone settles the positive case too', () => {
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'recv', to: PID, initiator_pid: THIRD })],
      included,
    })
    const a = g.selectedActivity.value as Activity

    expect(a.paymentCommitted[7]).toBe(1)
    expect(a.payments.incompleteWindows).toEqual([])
  })

  it('a payment naming both counterparties, neither of them ours, is a real zero', () => {
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'theirs', from: THIRD, to: OTHER, initiator_pid: THIRD })],
      included,
    })
    const a = g.selectedActivity.value as Activity

    expect(a.paymentCommitted[7]).toBe(0)
    expect(a.payments.incompleteWindows).toEqual([])
    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('0 / 0 / 0')
  })

  it('a clearing publishing no edges, initiated by someone else, stays unknown', () => {
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'cl-bare', type: 'CLEARING', initiator_pid: THIRD })],
      included,
    })
    const a = g.selectedActivity.value as Activity

    expect(a.clearingCommitted[7]).toBe(0)
    expect(a.clearings.incompleteWindows).toEqual([7, 30, 90])
    // ... and the PAYMENT cells, which this clearing says nothing whatever about, keep the zeros
    // they were counted to. Before F-013-R4 this row blanked them too.
    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('0 / 0 / 0')
  })
})

// =================================================================================================
// F-013-R4 (EXTERNAL review of programme 013). A doubt at the granularity of the cell that shows it.
//
// The flag F-013-R3 added was one boolean over the whole `transactions` collection. Both the payment
// cell and the clearing cell read it, and each read it for all three windows at once. So a single
// committed PAYMENT with no published recipient - a legitimate doubt about payment attribution -
// erased a CLEARING count of zero that had been reached without ever consulting that row, and erased
// it in windows the row was 60 days too old to belong to.
//
// Both wrong fixes have to fail here, and each has its own cases:
//   * too COARSE (one flag for the collection, or one for all windows): the "left standing" cases go
//     red, because a cell that owes nothing to the doubtful row would print "—".
//   * too FINE (a doubt that reaches no cell at all): the "still fires" cases go red, because a
//     payment nobody can attribute would print a confident number.
// =================================================================================================
describe('F-013-R4: a doubt clouds its own counter, in its own windows, and nothing else', () => {
  const included = ['transactions']

  /** The drawer's "cannot be attributed" notice, verbatim from GraphAnalyticsDrawer.vue. */
  function showsUnattributableNotice(a: Activity): boolean {
    return Boolean(a.payments.incompleteWindows.length || a.clearings.incompleteWindows.length)
  }

  it('THE REPRODUCER: an unattributable payment leaves the KNOWN clearing zero standing', () => {
    // The reviewer's input, verbatim: included=[transactions], truncated=[], one COMMITTED PAYMENT
    // in EUR, initiated by B, `from` B, no `to`. The complete collection holds no clearings at all,
    // and that count was taken without reference to this payment's missing recipient.
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'half', from: OTHER, initiator_pid: OTHER })],
      included,
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('— / — / —')
    expect(renderCounts(a, a.clearingCommitted, 'clearings')).toBe('0 / 0 / 0')
    // STILL FIRES: the payment cells are blank and the operator is told why. A fix that narrowed
    // the doubt until it reached nothing would pass line two and fail these.
    expect(showsUnattributableNotice(a)).toBe(true)
  })

  it('the doubt reaches only the windows the doubtful row could have landed in', () => {
    // One payment we can see is ours, one day old; one payment we cannot place, 59 days old. The
    // second cannot be in the 7- or 30-day count whoever it belongs to, so those two cells are
    // still exact - and the 90-day cell is not, even though it holds a real count of 1.
    const g = analyticsFor({
      transactions: [
        producerRow({ tx_id: 'mine', from: PID, to: THIRD, initiator_pid: PID, created_at: daysBefore(1), updated_at: daysBefore(1) }),
        producerRow({ tx_id: 'stale-half', from: THIRD, initiator_pid: THIRD, created_at: daysBefore(60), updated_at: daysBefore(60) }),
      ],
      included,
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('1 / 1 / —')
    expect(a.payments.incompleteWindows).toEqual([90])
    // The clearing cells are untouched by a payment's doubt, in every window.
    expect(renderCounts(a, a.clearingCommitted, 'clearings')).toBe('0 / 0 / 0')
    expect(showsUnattributableNotice(a)).toBe(true)
  })

  it('a cut collection still stamps ≥ on the windows the doubt does not reach', () => {
    // The two states are independent and both have to survive: "at least this many" for the windows
    // we can count, silence for the one we cannot. A fix that let one swallow the other passes
    // neither half of this line.
    const g = analyticsFor({
      transactions: [
        producerRow({ tx_id: 'mine', from: PID, to: THIRD, initiator_pid: PID, created_at: daysBefore(1), updated_at: daysBefore(1) }),
        producerRow({ tx_id: 'stale-half', from: THIRD, initiator_pid: THIRD, created_at: daysBefore(60), updated_at: daysBefore(60) }),
      ],
      included,
      truncated: ['transactions'],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('≥1 / ≥1 / —')
    expect(renderCounts(a, a.clearingCommitted, 'clearings')).toBe('≥0 / ≥0 / ≥0')
  })

  it('THE MIRROR: an unattributable clearing leaves the payment counters standing', () => {
    // The same finding with the roles exchanged, because a fix that hard-wires the doubt to the
    // payment cell would pass the reproducer above and fail this.
    const g = analyticsFor({
      transactions: [
        producerRow({ tx_id: 'cl-bare', type: 'CLEARING', initiator_pid: THIRD, created_at: daysBefore(60), updated_at: daysBefore(60) }),
        producerRow({ tx_id: 'paid', from: PID, to: THIRD, initiator_pid: PID, created_at: daysBefore(1), updated_at: daysBefore(1) }),
      ],
      included,
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('1 / 1 / 1')
    expect(renderCounts(a, a.clearingCommitted, 'clearings')).toBe('0 / 0 / —')
    expect(showsUnattributableNotice(a)).toBe(true)
  })

  it('a collection whose every row can be placed raises no doubt and no notice', () => {
    // The other side of "still fires": the notice must not become permanent furniture. One payment
    // we sent, one clearing that names its whole cycle without us.
    const g = analyticsFor({
      transactions: [
        producerRow({ tx_id: 'paid', from: PID, to: THIRD, initiator_pid: PID }),
        producerRow({ tx_id: 'cl-theirs', type: 'CLEARING', initiator_pid: THIRD, edges: [{ debtor: OTHER, creditor: THIRD }] }),
      ],
      included,
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('1 / 1 / 1')
    expect(renderCounts(a, a.clearingCommitted, 'clearings')).toBe('0 / 0 / 0')
    expect(showsUnattributableNotice(a)).toBe(false)
  })

  it('a row that cannot be placed in TIME clouds every window of its own counter', () => {
    // The window half of the same question. This payment is demonstrably ours - `from` names us -
    // but neither timestamp parses, so it joins no window's count and every payment cell is short
    // by it. The loop used to `continue` past this row in silence, which is the finding's own shape
    // one level in.
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'mine-undated', from: PID, to: THIRD, initiator_pid: PID, created_at: '', updated_at: '' })],
      included,
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('— / — / —')
    expect(renderCounts(a, a.clearingCommitted, 'clearings')).toBe('0 / 0 / 0')
  })

  it('... but a row that is not ours clouds nothing, whatever its timestamp says', () => {
    // Both counterparties named, neither of them ours: a row we are NOT missing from our counts, so
    // its unreadable timestamp is not our problem. A fix that clouded on any unparseable date would
    // blank this cell for a payment between two other people.
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'theirs-undated', from: THIRD, to: OTHER, initiator_pid: THIRD, created_at: '', updated_at: '' })],
      included,
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('0 / 0 / 0')
    expect(showsUnattributableNotice(a)).toBe(false)
  })

  it('a collection that was never carried says nothing, and the doubt does not contradict it', () => {
    // Ordering, as the drawer states it: "we were not told" outranks "we were told something we
    // cannot use". An unattributable row inside a collection nobody asked for must not turn silence
    // into a narrower, more confident-looking silence.
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'half', from: OTHER, initiator_pid: OTHER })],
      included: [],
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(a.payments.known).toBe(false)
    expect(a.payments.incompleteWindows).toEqual([])
    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('— / — / —')
    expect(showsUnattributableNotice(a)).toBe(false)
  })
})

// =================================================================================================
// The rendering helpers, as units. Every branch of the three-state contract, stated once.
// =================================================================================================
describe('the display contract: not told / told and zero / told a lower bound', () => {
  const known: CountConfidence = { known: true, lowerBound: false, incompleteWindows: [] }

  it('collectionConfidence reads one collection at a time', () => {
    const included = ['transactions', 'incidents']
    const truncated = ['incidents', 'audit_log']

    expect(collectionConfidence('transactions', included, truncated)).toEqual({ known: true, lowerBound: false, incompleteWindows: [] })
    expect(collectionConfidence('incidents', included, truncated)).toEqual({ known: true, lowerBound: true, incompleteWindows: [] })
    // Cut but never carried: a cut we were not told we received tells us nothing.
    expect(collectionConfidence('audit_log', included, truncated)).toEqual({ known: false, lowerBound: false, incompleteWindows: [] })
    // A doubt about a collection we were never told about is not a narrower silence: it is the same
    // silence, and the windows are dropped rather than printed as a more specific ignorance.
    expect(collectionConfidence('audit_log', included, truncated, [7, 30])).toEqual({ known: false, lowerBound: false, incompleteWindows: [] })
    expect(collectionConfidence('incidents', included, truncated, [30])).toEqual({ known: true, lowerBound: true, incompleteWindows: [30] })
  })

  it('activityCounts prints per window, and 0 is a number when it was measured', () => {
    expect(activityCounts({ 7: 0, 30: 2, 90: 9 }, [7, 30, 90], known)).toBe('0 / 2 / 9')
    expect(activityCounts({ 7: 0, 30: 2, 90: 9 }, [7, 30, 90], { ...known, lowerBound: true })).toBe('≥0 / ≥2 / ≥9')
    expect(activityCounts({ 7: 0, 30: 2, 90: 9 }, [7, 30, 90], { ...known, known: false })).toBe('— / — / —')
    // Per window, and ONLY the listed windows. Both of the wrong shapes are stated here as units:
    // a doubt over one window must not blank the other two, and a doubt over all three must.
    expect(activityCounts({ 7: 0, 30: 2, 90: 9 }, [7, 30, 90], { ...known, incompleteWindows: [90] })).toBe('0 / 2 / —')
    expect(activityCounts({ 7: 0, 30: 2, 90: 9 }, [7, 30, 90], { ...known, incompleteWindows: [7, 30, 90] })).toBe('— / — / —')
    expect(activityCounts({ 7: 0, 30: 2, 90: 9 }, [7, 30, 90], { ...known, lowerBound: true, incompleteWindows: [7] })).toBe('— / ≥2 / ≥9')
  })

  it('incidentRatioDisplay keeps a missing participant apart from a missing collection', () => {
    expect(incidentRatioDisplay(0.5, known)).toBe('0.50')
    expect(incidentRatioDisplay(undefined, known)).toBe('0.00')
    expect(incidentRatioDisplay(0.5, { ...known, known: false })).toBe('—')
    expect(incidentRatioDisplay(undefined, { ...known, known: false })).toBe('—')
    expect(incidentRatioDisplay(0.5, { ...known, lowerBound: true })).toBe('≥0.50')
    // Absent from a list we know was cut: silence, not zero.
    expect(incidentRatioDisplay(undefined, { ...known, lowerBound: true })).toBe('—')
    // The ratio is not windowed - it is one number over the whole collection - so ANY unplaceable
    // row in it is enough to withhold it. This is the one display where the doubt is not per window.
    expect(incidentRatioDisplay(0.5, { ...known, incompleteWindows: [7] })).toBe('—')
  })

  /**
   * ЧАСОВОЙ НА ТО, ЧЕГО ВЫБОРКА ЭТОГО ФАЙЛА НЕ ВИДИТ, и это признал сам её автор.
   *
   * Все тесты выше судят через `renderCounts` — адаптер, ПОВТОРЯЮЩИЙ пары «счётчик ↔ уверенность»
   * из шаблона. То есть пар в дереве две реализации: восемь мест в `GraphAnalyticsDrawer.vue` и
   * одна здесь. Если в шаблоне поменять местами `payments` и `clearings`, платёжные ячейки начнут
   * гаситься сомнением о клирингах и наоборот — ровно тот дефект, ради которого вводилась
   * гранулярность, — а ни один тест этого файла не покраснеет.
   *
   * «Выборкой бывают не значения, а РЕАЛИЗАЦИИ» — записанный урок этого репозитория, и здесь он в
   * чистом виде. Правильная починка — свести пары к одному источнику; она инвазивна для шаблона и
   * не делается в фикс-раунде внешнего ревью. Пока пар две, их согласие утверждается тестом, а не
   * подразумевается: он читает исходник дровера и требует, чтобы каждая пара стояла как надо.
   *
   * Это утверждение о ТЕКСТЕ, а не о поведении, и потому слабее остальных: оно не переживёт
   * переписывания шаблона на другую форму вызова. Это осознанная цена, и когда пары станут одной
   * реализацией, тест надо удалить вместе с ними.
   */
  it('sentinel: the drawer pairs each counter with its OWN confidence', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/pages/graph/GraphAnalyticsDrawer.vue'), 'utf8')

    const PAIRS: Array<[string, string]> = [
      ['paymentCommitted', 'payments'],
      ['clearingCommitted', 'clearings'],
      ['incidentCount', 'incidents'],
      ['participantOps', 'auditLog'],
    ]

    for (const [counts, confidence] of PAIRS) {
      const call = `activityCounts(selectedActivity.${counts}, selectedActivity.windows, selectedActivity.${confidence})`
      expect(
        source.includes(call),
        `дровер не вызывает \`${call}\`: счётчик ${counts} либо не отрисован, либо спарен с чужой ` +
          'уверенностью — тогда его ячейки гасятся сомнением о другой коллекции, а тесты этого файла ' +
          'судят копию пар, а не сам шаблон',
      ).toBe(true)
    }

    // И ни одной перекрёстной пары: наличие правильной не исключает наличия неправильной рядом.
    const CROSSED: Array<[string, string]> = [
      ['paymentCommitted', 'clearings'],
      ['clearingCommitted', 'payments'],
      ['incidentCount', 'auditLog'],
      ['participantOps', 'incidents'],
    ]
    for (const [counts, confidence] of CROSSED) {
      const call = `activityCounts(selectedActivity.${counts}, selectedActivity.windows, selectedActivity.${confidence})`
      expect(source.includes(call), `дровер спарил ${counts} с чужой уверенностью ${confidence}`).toBe(false)
    }
  })
})

// =================================================================================================
// F-013-R5 (CROSS review). Every caveat on screen must have the sentence that explains it.
//
// The three notices under the Activity card were written as a v-if / v-else-if / v-else-if chain,
// i.e. as three MUTUALLY EXCLUSIVE statements. That was sound only while `incompleteWindows`
// blanked every cell of the collection: a suppressed truncation notice explained nothing, because
// no "at least" could be on screen at the same time. F-013-R4 made the doubt per window, so the two
// caveats became simultaneously true - a cut collection with one clouded window prints its
// surviving cells as "≥N" while the middle branch swallows the sentence that says what "≥" means.
//
// WHY THE NOTICES ARE JUDGED THROUGH `activityNotices` AND NOT THROUGH CONDITIONS COPIED FROM THE
// TEMPLATE. The finding was reachable in the first place because the chain existed twice in the
// tree - GraphAnalyticsDrawer.vue and tabs/RiskTab.vue, one copy each - and nowhere as a value a
// test could hold. It is now one function called from both templates; the sentinel at the end of
// this block is what keeps that true.
// =================================================================================================
describe('F-013-R5: a notice for every caveat on screen, and none without one', () => {
  // All three optional collections carried, so the `incidents` / `audit_log` notice is not standing
  // in every assertion below: this block is about the transactions chain, and states it exactly.
  const included = ['transactions', 'incidents', 'audit_log']

  /** What the operator actually reads under the Activity card, in order. */
  function noticesOf(a: Activity): string[] {
    return activityNotices(a).map((n) => n.kind)
  }

  it('THE REPRODUCER: a cut collection with one clouded window prints ≥ AND explains it', () => {
    // The reviewer's input, verbatim: included=[transactions], truncated=[transactions], one
    // payment we own at 1 day, one payment we cannot place at 59 days.
    const g = analyticsFor({
      transactions: [
        producerRow({ tx_id: 'mine', from: PID, to: THIRD, initiator_pid: PID, created_at: daysBefore(1), updated_at: daysBefore(1) }),
        producerRow({ tx_id: 'stale-half', from: THIRD, initiator_pid: THIRD, created_at: daysBefore(59), updated_at: daysBefore(59) }),
      ],
      included,
      truncated: ['transactions'],
    })
    const a = g.selectedActivity.value as Activity

    // Two caveats, both true at once, and the cells carry both: "at least" in the two windows the
    // doubt does not reach, silence in the one it does. Both must be spelled out below the card.
    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('≥1 / ≥1 / —')
    expect(noticesOf(a)).toEqual(['transactionsUnattributable', 'transactionsTruncated'])
  })

  it('a cut collection with nothing in doubt explains the cut and nothing else', () => {
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'mine', from: PID, to: THIRD, initiator_pid: PID })],
      included,
      truncated: ['transactions'],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('≥1 / ≥1 / ≥1')
    expect(noticesOf(a)).toEqual(['transactionsTruncated'])
  })

  it('a whole collection with a row in doubt explains the doubt and nothing else', () => {
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'half', from: OTHER, initiator_pid: OTHER })],
      included,
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('— / — / —')
    expect(noticesOf(a)).toEqual(['transactionsUnattributable'])
  })

  it('a doubt raised by the CLEARING counter alone still gets its sentence', () => {
    // The notice is about the collection, but the doubt is per counter since F-013-R4, so it must
    // be asked of BOTH. A condition that reads `payments.incompleteWindows` only would leave this
    // card with three blanked clearing cells and nothing saying why.
    const g = analyticsFor({
      transactions: [
        producerRow({ tx_id: 'cl-bare', type: 'CLEARING', initiator_pid: THIRD, created_at: daysBefore(60), updated_at: daysBefore(60) }),
        producerRow({ tx_id: 'mine', from: PID, to: THIRD, initiator_pid: PID, created_at: daysBefore(1), updated_at: daysBefore(1) }),
      ],
      included,
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.clearingCommitted, 'clearings')).toBe('0 / 0 / —')
    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('1 / 1 / 1')
    expect(noticesOf(a)).toEqual(['transactionsUnattributable'])
  })

  it('a whole collection with every row placed says nothing at all about transactions', () => {
    // The anti-over-fix line. "Show all three notices unconditionally" satisfies the reproducer and
    // dies here, on a snapshot that carries a complete, fully attributable collection.
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'mine', from: PID, to: THIRD, initiator_pid: PID })],
      included,
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('1 / 1 / 1')
    expect(noticesOf(a)).toEqual([])
  })

  it('a collection nobody asked for says only that, however doubtful the rows inside it', () => {
    // The ordering survives the change: "we were not told" is not joined by two caveats about a
    // collection we do not have. It cannot be - `collectionConfidence` drops both for an uncarried
    // collection - and that is asserted here at the level the operator reads it.
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'half', from: OTHER, initiator_pid: OTHER })],
      included: ['incidents', 'audit_log'],
      truncated: ['transactions'],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('— / — / —')
    expect(noticesOf(a)).toEqual(['transactionsNotIncluded'])
  })

  it('the two snapshot collections keep their own notice, beside the transactions ones', () => {
    // `incidents` and `audit_log` fail independently of `transactions`, so their sentence is not
    // part of the transactions chain: it appears next to it, never instead of it.
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'mine', from: PID, to: THIRD, initiator_pid: PID })],
      included: ['transactions'],
      truncated: ['transactions'],
    })
    const a = g.selectedActivity.value as Activity

    expect(noticesOf(a)).toEqual(['transactionsTruncated', 'snapshotCollectionsNotIncluded'])
  })

  it('every notice names a title and a description that both locales define', () => {
    // The notices now carry their own keys instead of having them spelled out at each call site, so
    // a key that exists in one locale only - or a renamed one - would otherwise surface as raw
    // `graph.analytics.…` text in the UI and in no test.
    const g = analyticsFor({
      transactions: [
        producerRow({ tx_id: 'mine', from: PID, to: THIRD, initiator_pid: PID }),
        producerRow({ tx_id: 'half', from: OTHER, initiator_pid: OTHER }),
      ],
      included: ['transactions'],
      truncated: ['transactions'],
    })
    const a = g.selectedActivity.value as Activity
    const all = activityNotices(a)
    expect(all.map((n) => n.kind)).toEqual([
      'transactionsUnattributable',
      'transactionsTruncated',
      'snapshotCollectionsNotIncluded',
    ])

    for (const notice of all) {
      for (const [name, dict] of [['en', EN], ['ru', RU]] as const) {
        for (const key of [notice.titleKey, notice.descriptionKey]) {
          expect(Object.prototype.hasOwnProperty.call(dict, key), `${name} не определяет ${key}`).toBe(true)
        }
      }
    }
  })

  /**
   * ЧАСОВОЙ: обе карточки рисуют блок предупреждений из ОДНОГО источника.
   *
   * Находка кросс-ревью стала возможной потому, что цепочка `v-if` жила в дереве дважды и ни разу —
   * как значение, которое можно предъявить тесту. Теперь это одна функция; пока в шаблонах нет
   * собственных условий про `payments.known` / `incompleteWindows` / `lowerBound`, разойтись
   * реализациям негде.
   *
   * Это утверждение о ТЕКСТЕ шаблонов, как и соседний часовой выше, и потому слабее остальных: оно
   * не переживёт переписывания блока на другую форму вызова. Осознанная цена — до тех пор, пока
   * шаблон нельзя проверить поведением, не заводя рендер-тест на две карточки сразу.
   */
  it('sentinel: both cards render the notices from the one helper, with no conditions of their own', () => {
    const FILES = ['src/pages/graph/GraphAnalyticsDrawer.vue', 'src/pages/graph/tabs/RiskTab.vue']
    for (const file of FILES) {
      const source = readFileSync(resolve(process.cwd(), file), 'utf8')
      expect(
        source.includes('v-for="notice in activityNotices(selectedActivity)"'),
        `${file} не рисует блок уведомлений через activityNotices — значит, условия снова в шаблоне`,
      ).toBe(true)
      for (const own of ['payments.known', 'payments.lowerBound', 'incompleteWindows.length', 'auditLog.known']) {
        expect(
          source.includes(own),
          `${file} снова решает сам, какое уведомление показать (${own}): комплектов условий опять два`,
        ).toBe(false)
      }
    }
  })
})

// =================================================================================================
// F-013-R6 (CROSS review). A row whose equivalent the producer could not name was counted into
// WHICHEVER equivalent the operator had selected, at full confidence.
//
// `"equivalent": payload.get("equivalent")` (app/api/v1/admin.py) is `null` whenever the stored
// payload lacks the key, and the filter here read `if (eqCode && rowEqRaw && normEq(rowEqRaw) !==
// eqCode) continue` - a row with no equivalent slipped past it and joined the count of every
// equivalent in turn. Same family as the attribution finding, one field over: "we cannot place this
// row" reported as a number.
//
// THE DECISION, stated where it is tested: such a row CLOUDS the windows it could have landed in,
// exactly as an unattributable one does. Excluding it instead would assert "this row is not in the
// selected equivalent" - the same invention with the opposite sign, and it would produce an
// undercount wearing the typography of a total.
// =================================================================================================
describe('F-013-R6: a row whose equivalent cannot be read is not counted as the selected one', () => {
  const included = ['transactions']

  it('THE REPRODUCER: an unnamed equivalent on a row of ours clouds its windows', () => {
    // The reviewer's input: one committed payment we own, `equivalent: null` exactly as the
    // producer emits it when the stored payload has no such key.
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'no-eq', from: PID, to: THIRD, initiator_pid: PID, equivalent: null })],
      included,
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('— / — / —')
    expect(a.payments.incompleteWindows).toEqual([7, 30, 90])
    expect(activityNotices(a).map((n) => n.kind)).toContain('transactionsUnattributable')
  })

  it('THE CONTROL: and the same row is no more EUR than it is UAH', () => {
    // The reviewer's control. Before the fix this one row was counted once as EUR and once as UAH -
    // one payment, two confident totals, in two different units.
    const g = analyticsFor({
      eq: 'UAH',
      transactions: [producerRow({ tx_id: 'no-eq', from: PID, to: THIRD, initiator_pid: PID, equivalent: null })],
      included,
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('— / — / —')
  })

  it('an empty equivalent is as unreadable as a missing one', () => {
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'blank-eq', from: PID, to: THIRD, initiator_pid: PID, equivalent: '' })],
      included,
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('— / — / —')
  })

  it('a row that NAMES a different equivalent is still a plain, silent exclusion', () => {
    // The anti-over-fix line. "Cloud whenever the row is not counted" passes the reproducer and
    // dies here: a UAH payment is not missing from the EUR count, it does not belong in it.
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'uah', from: PID, to: THIRD, initiator_pid: PID, equivalent: 'UAH' })],
      included,
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('0 / 0 / 0')
    expect(a.payments.incompleteWindows).toEqual([])
    expect(activityNotices(a).map((n) => n.kind)).not.toContain('transactionsUnattributable')
  })

  it('with NO equivalent selected there is nothing to place the row against, and no doubt', () => {
    // The counters then count every equivalent, so a row that names none is not excluded from
    // anything and joins the count exactly as it did. A fix that clouded on a missing `equivalent`
    // whatever the control says would blank a correct number here.
    const g = analyticsFor({
      eq: null,
      transactions: [producerRow({ tx_id: 'no-eq', from: PID, to: THIRD, initiator_pid: PID, equivalent: null })],
      included,
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('1 / 1 / 1')
    expect(a.payments.incompleteWindows).toEqual([])
  })

  it('an unreadable equivalent on a row that is NOT ours clouds nothing', () => {
    // Both counterparties named, neither of them us: whatever unit it is in, it was never going to
    // join our count. A doubt is admitted only where it could have changed a number of ours.
    const g = analyticsFor({
      transactions: [producerRow({ tx_id: 'theirs-no-eq', from: THIRD, to: OTHER, initiator_pid: THIRD, equivalent: null })],
      included,
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('0 / 0 / 0')
    expect(a.payments.incompleteWindows).toEqual([])
  })

  it('the doubt reaches only the windows that row could have landed in', () => {
    // One EUR payment of ours at 1 day, one unreadable-equivalent payment of ours at 59 days. The
    // second cannot be in the 7- or 30-day count in any unit, so those two cells keep their number.
    const g = analyticsFor({
      transactions: [
        producerRow({ tx_id: 'mine-eur', from: PID, to: THIRD, initiator_pid: PID, created_at: daysBefore(1), updated_at: daysBefore(1) }),
        producerRow({ tx_id: 'stale-no-eq', from: PID, to: THIRD, initiator_pid: PID, equivalent: null, created_at: daysBefore(59), updated_at: daysBefore(59) }),
      ],
      included,
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('1 / 1 / —')
    expect(a.payments.incompleteWindows).toEqual([90])
  })

  it('THE MIRROR: an unreadable equivalent on a CLEARING clouds clearings only', () => {
    // A fix wired into the payment branch would pass every line above and fail this one.
    const g = analyticsFor({
      transactions: [
        producerRow({ tx_id: 'cl-no-eq', type: 'CLEARING', initiator_pid: PID, equivalent: null }),
        producerRow({ tx_id: 'mine-eur', from: PID, to: THIRD, initiator_pid: PID }),
      ],
      included,
      truncated: [],
    })
    const a = g.selectedActivity.value as Activity

    expect(renderCounts(a, a.clearingCommitted, 'clearings')).toBe('— / — / —')
    expect(renderCounts(a, a.paymentCommitted, 'payments')).toBe('1 / 1 / 1')
  })
})
