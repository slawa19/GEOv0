import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'

import { api } from '../api'
import { assertSuccess } from '../api/envelope'
import { t } from '../i18n'
import { COUNT_MEASURED, collectionConfidence, makeMetricsKey } from '../pages/graph/graphPageHelpers'
import { isRatioBelowThreshold, isUnitIntervalDecimalString } from '../utils/decimal'
import type { ParticipantMetrics } from '../types/domain'
import type {
  AuditLogEntry,
  ClearingCycles,
  Debt,
  Incident,
  Participant,
  Transaction,
  Trustline,
} from '../pages/graph/graphTypes'
import type { SelectedInfo } from './useGraphVisualization'
import { useLatestRequest } from './useLatestRequest'

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n))
}

function normEq(v: string): string {
  return String(v || '').trim().toUpperCase()
}

export function equivalentPrecision(precisionByEq: Map<string, number>, eqCode: string): number | null {
  const precision = precisionByEq.get(normEq(eqCode))
  return Number.isInteger(precision) && Number(precision) >= 0 ? Number(precision) : null
}

function parseIsoMillis(ts?: string | null): number | null {
  const s = String(ts || '').trim()
  if (!s) return null
  const ms = Date.parse(s)
  return Number.isFinite(ms) ? ms : null
}

export function decimalToAtoms(input: string, precision: number): bigint {
  const raw = String(input ?? '').trim()
  if (!raw) return 0n
  const m = /^(-)?(\d+)(?:\.(\d+))?$/.exec(raw)
  if (!m) return 0n
  const neg = Boolean(m[1])
  const intPart = m[2] || '0'
  const fracPart = m[3] || ''
  const frac = (fracPart + '0'.repeat(precision)).slice(0, precision)
  const atoms = BigInt((intPart + frac).replace(/^0+(?=\d)/, '') || '0')
  return neg ? -atoms : atoms
}

export function atomsToDecimal(atoms: bigint, precision: number): string {
  const neg = atoms < 0n
  const abs = neg ? -atoms : atoms
  const s = abs.toString()
  if (precision <= 0) return (neg ? '-' : '') + s
  const pad = precision + 1
  const padded = s.length >= pad ? s : '0'.repeat(pad - s.length) + s
  const head = padded.slice(0, padded.length - precision)
  const frac = padded.slice(padded.length - precision)
  return (neg ? '-' : '') + head + '.' + frac
}

type CounterpartySplitRow = NonNullable<NonNullable<ParticipantMetrics['counterparty']>['creditors']>[number]

type BalanceRow = NonNullable<ParticipantMetrics['balance_rows']>[number]

function emptyCounterpartySplit() {
  return {
    eq: null as string | null,
    totalDebtAtoms: 0n,
    totalCreditAtoms: 0n,
    creditors: [] as CounterpartySplitRow[],
    debtors: [] as CounterpartySplitRow[],
  }
}

type NetDist = {
  eq: string
  n: number
  netAtomsByPid: Map<string, bigint>
  sortedPids: string[]
  min: bigint
  max: bigint
  bins: Array<{ from: bigint; to: bigint; count: number }>
}

function hhiFromShares(rows: Array<{ share: number }>): number {
  let hhi = 0
  for (const r of rows) {
    const s = Number(r.share) || 0
    hhi += s * s
  }
  return hhi
}

function hhiLevel(hhi: number): { label: string; type: 'success' | 'warning' | 'danger' } {
  if (hhi >= 0.25) return { label: t('graph.analytics.hhiLevel.high'), type: 'danger' }
  if (hhi >= 0.15) return { label: t('graph.analytics.hhiLevel.medium'), type: 'warning' }
  return { label: t('graph.analytics.hhiLevel.low'), type: 'success' }
}

export function useGraphAnalytics(opts: {
  isRealMode: ComputedRef<boolean>
  threshold: Ref<string>
  analyticsEq: ComputedRef<string | null>

  precisionByEq: ComputedRef<Map<string, number>>
  availableEquivalents: ComputedRef<string[]>
  participantByPid: ComputedRef<Map<string, Participant>>

  participants: Ref<Participant[] | null>
  trustlines: Ref<Trustline[] | null>
  debts: Ref<Debt[] | null>
  incidents: Ref<Incident[] | null>
  auditLog: Ref<AuditLogEntry[] | null>
  transactions: Ref<Transaction[] | null>
  // F-013-1 / T1302. What the snapshot said it actually carried, and which of those it cut at the
  // include limit. `transactions.value.length === 0` cannot answer either question: it is produced
  // both by "the page never asked" and by "the page asked and the period is genuinely empty".
  included: Ref<string[] | null>
  truncated: Ref<string[] | null>
  clearingCycles: Ref<ClearingCycles | null>

  selected: Ref<SelectedInfo | null>
}) {
  const metricsCache = ref(new Map<string, ParticipantMetrics>())
  const metricsLoading = ref(false)
  const metricsError = ref<string | null>(null)
  const metricsRequests = useLatestRequest()

  const selectedPid = computed(() => (opts.selected.value && opts.selected.value.kind === 'node' ? opts.selected.value.pid : ''))
  const selectedEqCode = computed(() => {
    const eqCode = normEq(opts.analyticsEq.value || '')
    return eqCode || null
  })

  const precisionOf = (eqCode: string) => equivalentPrecision(opts.precisionByEq.value, eqCode)

  const selectedMetrics = computed(() => {
    const pid = selectedPid.value
    if (!pid) return null
    const key = makeMetricsKey(pid, selectedEqCode.value, String(opts.threshold.value || ''))
    return metricsCache.value.get(key) || null
  })

  async function loadSelectedMetrics() {
    const request = metricsRequests.begin()
    if (!opts.isRealMode.value) {
      metricsLoading.value = false
      metricsError.value = null
      return
    }
    const pid = selectedPid.value
    if (!pid) {
      metricsLoading.value = false
      metricsError.value = null
      return
    }

    const eqCode = selectedEqCode.value
    const thr = String(opts.threshold.value || '').trim()
    if (!isUnitIntervalDecimalString(thr)) {
      metricsLoading.value = false
      metricsError.value = t('validation.thresholdUnitInterval')
      return
    }
    const key = makeMetricsKey(pid, eqCode, thr)
    if (metricsCache.value.has(key)) {
      metricsLoading.value = false
      metricsError.value = null
      return
    }

    metricsLoading.value = true
    metricsError.value = null
    try {
      const res = await api.participantMetrics(pid, { equivalent: eqCode, threshold: thr })
      if (!request.isCurrent()) return
      const m = assertSuccess(res) as ParticipantMetrics
      metricsCache.value.set(key, m)
    } catch (e: unknown) {
      if (!request.isCurrent()) return
      const msg = e instanceof Error ? e.message : String(e)
      metricsError.value = msg || t('graph.analytics.metricsLoadFailed')
    } finally {
      if (request.isCurrent()) metricsLoading.value = false
    }
  }

  watch([selectedPid, selectedEqCode, opts.threshold], () => {
    void loadSelectedMetrics()
  })

  function isBottleneck(t: Trustline): boolean {
    return isRatioBelowThreshold({ numerator: t.available, denominator: t.limit, threshold: opts.threshold.value })
  }

  const selectedBalanceRows = computed<BalanceRow[]>(() => {
    const m = selectedMetrics.value
    if (m?.balance_rows) return m.balance_rows
    if (!opts.selected.value || opts.selected.value.kind !== 'node') return []
    const pid = opts.selected.value.pid
    const debtAtomsByEq = new Map<string, bigint>()
    const creditAtomsByEq = new Map<string, bigint>()

    for (const d of opts.debts.value || []) {
      const eqCode = normEq(d.equivalent)
      if (!eqCode) continue
      const p = precisionOf(eqCode)
      if (p === null) continue
      const amt = decimalToAtoms(d.amount, p)
      if (d.debtor === pid) debtAtomsByEq.set(eqCode, (debtAtomsByEq.get(eqCode) || 0n) + amt)
      if (d.creditor === pid) creditAtomsByEq.set(eqCode, (creditAtomsByEq.get(eqCode) || 0n) + amt)
    }

    const outLimitByEq = new Map<string, bigint>()
    const outUsedByEq = new Map<string, bigint>()
    const inLimitByEq = new Map<string, bigint>()
    const inUsedByEq = new Map<string, bigint>()

    for (const t of opts.trustlines.value || []) {
      const eqCode = normEq(t.equivalent)
      if (!eqCode) continue
      const p = precisionOf(eqCode)
      if (p === null) continue
      const lim = decimalToAtoms(t.limit, p)
      const used = decimalToAtoms(t.used, p)
      if (t.from === pid) {
        outLimitByEq.set(eqCode, (outLimitByEq.get(eqCode) || 0n) + lim)
        outUsedByEq.set(eqCode, (outUsedByEq.get(eqCode) || 0n) + used)
      }
      if (t.to === pid) {
        inLimitByEq.set(eqCode, (inLimitByEq.get(eqCode) || 0n) + lim)
        inUsedByEq.set(eqCode, (inUsedByEq.get(eqCode) || 0n) + used)
      }
    }

    const eqs = (opts.availableEquivalents.value || []).map(normEq).filter((x) => x && x !== 'ALL')
    const wanted = selectedEqCode.value ? [selectedEqCode.value] : eqs

    return wanted
      .flatMap((eqCode) => {
        const p = precisionOf(eqCode)
        if (p === null) return []
        const debt = debtAtomsByEq.get(eqCode) || 0n
        const credit = creditAtomsByEq.get(eqCode) || 0n
        const net = credit - debt
        return {
          equivalent: eqCode,
          outgoing_limit: atomsToDecimal(outLimitByEq.get(eqCode) || 0n, p),
          outgoing_used: atomsToDecimal(outUsedByEq.get(eqCode) || 0n, p),
          incoming_limit: atomsToDecimal(inLimitByEq.get(eqCode) || 0n, p),
          incoming_used: atomsToDecimal(inUsedByEq.get(eqCode) || 0n, p),
          total_debt: atomsToDecimal(debt, p),
          total_credit: atomsToDecimal(credit, p),
          net: atomsToDecimal(net, p),
        }
      })
      .filter((r) => {
        if (selectedEqCode.value) return true
        const p = precisionOf(r.equivalent)
        if (p === null) return false
        return !(
          decimalToAtoms(r.outgoing_limit, p) === 0n &&
          decimalToAtoms(r.incoming_limit, p) === 0n &&
          decimalToAtoms(r.total_debt, p) === 0n &&
          decimalToAtoms(r.total_credit, p) === 0n
        )
      })
  })

  const selectedCounterpartySplit = computed(() => {
    const m = selectedMetrics.value
    if (m?.counterparty && m.counterparty.eq) {
      const eqCode = normEq(m.counterparty.eq)
      const prec = precisionOf(eqCode)
      if (prec === null) return emptyCounterpartySplit()
      return {
        eq: eqCode,
        totalDebtAtoms: decimalToAtoms(m.counterparty.totalDebt, prec),
        totalCreditAtoms: decimalToAtoms(m.counterparty.totalCredit, prec),
        creditors: m.counterparty.creditors,
        debtors: m.counterparty.debtors,
      }
    }

    if (!opts.selected.value || opts.selected.value.kind !== 'node') return emptyCounterpartySplit()

    const eqCode = selectedEqCode.value
    if (!eqCode) return emptyCounterpartySplit()

    const pid = opts.selected.value.pid
    const prec = precisionOf(eqCode)
    if (prec === null) return emptyCounterpartySplit()

    const creditors = new Map<string, bigint>()
    const debtors = new Map<string, bigint>()

    let totalDebtAtoms = 0n
    let totalCreditAtoms = 0n

    for (const d of opts.debts.value || []) {
      if (normEq(d.equivalent) !== eqCode) continue
      const amt = decimalToAtoms(d.amount, prec)
      if (d.debtor === pid) {
        totalDebtAtoms += amt
        const other = String(d.creditor || '').trim()
        if (other) creditors.set(other, (creditors.get(other) || 0n) + amt)
      } else if (d.creditor === pid) {
        totalCreditAtoms += amt
        const other = String(d.debtor || '').trim()
        if (other) debtors.set(other, (debtors.get(other) || 0n) + amt)
      }
    }

    const toRows = (m: Map<string, bigint>, total: bigint): CounterpartySplitRow[] => {
      const out: CounterpartySplitRow[] = []
      for (const [otherPid, amt] of m.entries()) {
        const p = opts.participantByPid.value.get(otherPid)
        const name = String(p?.display_name || '').trim()
        const share = total > 0n ? Number(amt) / Number(total) : 0
        out.push({
          pid: otherPid,
          display_name: name || otherPid,
          amount: atomsToDecimal(amt, prec),
          share,
        })
      }
      out.sort((a, b) => {
        const ai = decimalToAtoms(a.amount, prec)
        const bi = decimalToAtoms(b.amount, prec)
        if (ai === bi) return a.pid.localeCompare(b.pid)
        return bi > ai ? 1 : -1
      })
      return out
    }

    return {
      eq: eqCode,
      totalDebtAtoms,
      totalCreditAtoms,
      creditors: toRows(creditors, totalDebtAtoms),
      debtors: toRows(debtors, totalCreditAtoms),
    }
  })

  const selectedConcentration = computed(() => {
    const eqCode = selectedCounterpartySplit.value.eq
    if (!eqCode) {
      return {
        eq: null as string | null,
        outgoing: { top1: 0, top5: 0, hhi: 0, level: hhiLevel(0) },
        incoming: { top1: 0, top5: 0, hhi: 0, level: hhiLevel(0) },
      }
    }

    const outRows = selectedCounterpartySplit.value.creditors
    const inRows = selectedCounterpartySplit.value.debtors

    const outTop1 = outRows[0]?.share || 0
    const outTop5 = outRows.slice(0, 5).reduce((acc, r) => acc + (r.share || 0), 0)
    const outHhi = hhiFromShares(outRows)

    const inTop1 = inRows[0]?.share || 0
    const inTop5 = inRows.slice(0, 5).reduce((acc, r) => acc + (r.share || 0), 0)
    const inHhi = hhiFromShares(inRows)

    return {
      eq: eqCode,
      outgoing: { top1: outTop1, top5: outTop5, hhi: outHhi, level: hhiLevel(outHhi) },
      incoming: { top1: inTop1, top5: inTop5, hhi: inHhi, level: hhiLevel(inHhi) },
    }
  })

  const netDistribution = computed<NetDist | null>(() => {
    const m = selectedMetrics.value
    if (m?.distribution && m.distribution.eq) {
      const bins = (m.distribution.bins || []).map((b) => ({
        from: BigInt(b.from_atoms || '0'),
        to: BigInt(b.to_atoms || '0'),
        count: Number(b.count || 0),
      }))
      return {
        eq: m.distribution.eq,
        n: bins.reduce((acc, b) => acc + (b.count || 0), 0),
        netAtomsByPid: new Map(),
        sortedPids: [],
        min: BigInt(m.distribution.min_atoms || '0'),
        max: BigInt(m.distribution.max_atoms || '0'),
        bins,
      }
    }

    const eqCode = selectedEqCode.value
    if (!eqCode) return null

    const prec = precisionOf(eqCode)
    if (prec === null) return null
    const net = new Map<string, bigint>()

    for (const p of opts.participants.value || []) {
      if (p?.pid) net.set(p.pid, 0n)
    }

    for (const d of opts.debts.value || []) {
      if (normEq(d.equivalent) !== eqCode) continue
      const amt = decimalToAtoms(d.amount, prec)
      const debtor = String(d.debtor || '').trim()
      const creditor = String(d.creditor || '').trim()
      if (debtor) net.set(debtor, (net.get(debtor) || 0n) - amt)
      if (creditor) net.set(creditor, (net.get(creditor) || 0n) + amt)
    }

    const pids = Array.from(net.keys())
    pids.sort((a, b) => {
      const av = net.get(a) || 0n
      const bv = net.get(b) || 0n
      if (av === bv) return a.localeCompare(b)
      return bv > av ? 1 : -1
    })

    let min = 0n
    let max = 0n
    for (const v of net.values()) {
      if (v < min) min = v
      if (v > max) max = v
    }

    const binsCount = 20
    const span = max - min
    const bins: Array<{ from: bigint; to: bigint; count: number }> = []
    if (span <= 0n) {
      bins.push({ from: min, to: max, count: pids.length })
    } else {
      const w = (span + BigInt(binsCount) - 1n) / BigInt(binsCount)
      for (let i = 0; i < binsCount; i++) {
        const from = min + BigInt(i) * w
        const to = i === binsCount - 1 ? max : from + w
        bins.push({ from, to, count: 0 })
      }
      for (const pid of pids) {
        const v = net.get(pid) || 0n
        const idx = w > 0n ? Number((v - min) / w) : 0
        const safe = clamp(idx, 0, binsCount - 1)
        const bucket = bins[safe]
        if (bucket) bucket.count += 1
      }
    }

    return { eq: eqCode, n: pids.length, netAtomsByPid: net, sortedPids: pids, min, max, bins }
  })

  const selectedRank = computed(() => {
    const m = selectedMetrics.value
    if (m?.rank && m.rank.eq) return m.rank

    if (!opts.selected.value || opts.selected.value.kind !== 'node') return null
    const dist = netDistribution.value
    if (!dist) return null
    const idx = dist.sortedPids.indexOf(opts.selected.value.pid)
    if (idx < 0) return null
    const rank = idx + 1
    const n = dist.n
    const percentile = n <= 1 ? 1 : (n - rank) / (n - 1)
    const precision = precisionOf(dist.eq)
    if (precision === null) return null
    return {
      eq: dist.eq,
      rank,
      n,
      percentile,
      net: atomsToDecimal(dist.netAtomsByPid.get(opts.selected.value.pid) || 0n, precision),
    }
  })

  const selectedCapacity = computed(() => {
    const m = selectedMetrics.value
    if (m?.capacity && m.capacity.eq) {
      const precision = precisionOf(m.capacity.eq)
      if (precision === null) return null
      return {
        eq: m.capacity.eq,
        out: {
          limit: decimalToAtoms(m.capacity.out.limit, precision),
          used: decimalToAtoms(m.capacity.out.used, precision),
          pct: Number(m.capacity.out.pct || 0),
        },
        inc: {
          limit: decimalToAtoms(m.capacity.inc.limit, precision),
          used: decimalToAtoms(m.capacity.inc.used, precision),
          pct: Number(m.capacity.inc.pct || 0),
        },
        bottlenecks: (m.capacity.bottlenecks || []).map((b) => ({ dir: b.dir, other: b.other, t: b.trustline })),
      }
    }

    if (!opts.selected.value || opts.selected.value.kind !== 'node') return null
    const pid = opts.selected.value.pid
    const eqCode = selectedEqCode.value

    let outLimit = 0n
    let outUsed = 0n
    let inLimit = 0n
    let inUsed = 0n

    const bottlenecks: Array<{ dir: 'out' | 'in'; other: string; t: Trustline }> = []

    for (const t of opts.trustlines.value || []) {
      const e = normEq(t.equivalent)
      if (eqCode && e !== eqCode) continue
      const p = precisionOf(e)
      if (p === null) return null

      const isOutgoing = t.from === pid
      const isIncoming = !isOutgoing && t.to === pid
      if (!isOutgoing && !isIncoming) continue

      // F-012-8: ёмкость суммируется только внутри одного эквивалента. Атом HOUR и атом UAH —
      // разные единицы даже при равной precision, и их сумма не является величиной.
      if (eqCode) {
        const lim = decimalToAtoms(t.limit, p)
        const used = decimalToAtoms(t.used, p)
        if (isOutgoing) {
          outLimit += lim
          outUsed += used
        } else {
          inLimit += lim
          inUsed += used
        }
      }

      // Узкое место — отношение available/limit внутри одной линии доверия: величина
      // безразмерная, поэтому считать такие линии по всем эквивалентам осмысленно.
      if (isBottleneck(t)) bottlenecks.push({ dir: isOutgoing ? 'out' : 'in', other: isOutgoing ? t.to : t.from, t })
    }

    if (!eqCode) return { eq: null, out: null, inc: null, bottlenecks }

    const outPct = outLimit > 0n ? Number(outUsed) / Number(outLimit) : 0
    const inPct = inLimit > 0n ? Number(inUsed) / Number(inLimit) : 0

    return {
      eq: eqCode,
      out: { limit: outLimit, used: outUsed, pct: outPct },
      inc: { limit: inLimit, used: inUsed, pct: inPct },
      bottlenecks,
    }
  })

  // F-013-1 / T1302. The three states the Activity card must be able to tell apart, derived from
  // the completeness metadata rather than from an array length.
  //
  //   not asked      -> included does not name the collection. We know NOTHING; a count would be
  //                     an invention and the card must show absence, not zero.
  //   asked, empty   -> included names it, truncated does not, the array is empty. Zero is a real
  //                     measurement and may be shown as a number.
  //   asked, cut     -> included and truncated both name it. Every count over the rows we hold is a
  //                     LOWER BOUND, because the server returned a prefix of a longer list.
  //
  // F-013-R2. All THREE optional collections, not just `transactions`. `incidentCount` is derived
  // from `incidents` and `participantOps` from `audit_log`; the client asks for neither, so on this
  // branch both are structurally empty in real mode and used to print a bare `0 / 0 / 0` beside
  // counters that had just learnt to say the dash. A confidence per collection, carried next to the
  // counters it governs, is what stops one collection's answer from being told about another's.
  const snapshotTransactions = computed(() =>
    collectionConfidence('transactions', opts.included.value, opts.truncated.value),
  )
  const snapshotIncidents = computed(() =>
    collectionConfidence('incidents', opts.included.value, opts.truncated.value),
  )
  const snapshotAuditLog = computed(() =>
    collectionConfidence('audit_log', opts.included.value, opts.truncated.value),
  )

  const selectedActivity = computed(() => {
    const m = selectedMetrics.value
    if (m?.activity) {
      return {
        windows: m.activity.windows,
        trustlineCreated: m.activity.trustline_created,
        trustlineClosed: m.activity.trustline_closed,
        incidentCount: m.activity.incident_count,
        participantOps: m.activity.participant_ops,
        paymentCommitted: m.activity.payment_committed,
        clearingCommitted: m.activity.clearing_committed,
        // F-013-R1. THIS BRANCH MEASURED. `/metrics` computes every counter above server-side over
        // the whole table for this participant: it does not paginate, does not depend on the
        // snapshot's `include`, and answers 0 by counting to 0. So all three counters are known,
        // exact and complete, and a zero here is a fact about the system.
        //
        // What used to stand here was `hasTransactions: Boolean(m.activity.has_transactions)`, and
        // it is the reason this comment exists. The server's `has_transactions` is a MEASUREMENT -
        // `len(tx_rows) > 0` over committed PAYMENT/CLEARING in a 90-day window
        // (app/core/admin/metrics.py) - while the client-side flag it was fed to means "the
        // response named this collection at all". Merging the two made a quiet real system, which
        // the server had actually measured as empty, report "Transaction activity was not
        // requested" and print three dashes over counters it was holding. Nothing on this branch
        // reads `has_transactions` any more: the counters already say everything it said, and they
        // say it without claiming an ignorance we do not have.
        transactions: COUNT_MEASURED,
        incidents: COUNT_MEASURED,
        auditLog: COUNT_MEASURED,
        // ... but NOT the incident RATIO. `/metrics` publishes no ratio; the drawer's row is fed by
        // the snapshot's `incidents` collection on every branch, so its confidence is the
        // snapshot's on every branch too. A metrics answer must not make an uncarried collection
        // look carried.
        snapshotIncidents: snapshotIncidents.value,
      }
    }

    if (!opts.selected.value || opts.selected.value.kind !== 'node') return null
    const pid = opts.selected.value.pid
    const eqCode = selectedEqCode.value

    const tsCandidates: number[] = []
    for (const t of opts.trustlines.value || []) {
      const ms = parseIsoMillis(t.created_at)
      if (ms !== null) tsCandidates.push(ms)
    }
    for (const i of opts.incidents.value || []) {
      const ms = parseIsoMillis(i.created_at)
      if (ms !== null) tsCandidates.push(ms)
    }
    for (const a of opts.auditLog.value || []) {
      const ms = parseIsoMillis(a.timestamp)
      if (ms !== null) tsCandidates.push(ms)
    }

    for (const t of opts.transactions.value || []) {
      const ms = parseIsoMillis(t.updated_at || t.created_at)
      if (ms !== null) tsCandidates.push(ms)
    }

    const now = tsCandidates.length ? Math.max(...tsCandidates) : Date.now()
    const dayMs = 24 * 60 * 60 * 1000

    const windows = [7, 30, 90]
    const trustlineCreated: Record<number, number> = { 7: 0, 30: 0, 90: 0 }
    const trustlineClosed: Record<number, number> = { 7: 0, 30: 0, 90: 0 }
    const incidentCount: Record<number, number> = { 7: 0, 30: 0, 90: 0 }
    const participantOps: Record<number, number> = { 7: 0, 30: 0, 90: 0 }
    const paymentCommitted: Record<number, number> = { 7: 0, 30: 0, 90: 0 }
    const clearingCommitted: Record<number, number> = { 7: 0, 30: 0, 90: 0 }

    for (const t of opts.trustlines.value || []) {
      if (eqCode && normEq(t.equivalent) !== eqCode) continue
      if (t.from !== pid && t.to !== pid) continue
      const ms = parseIsoMillis(t.created_at)
      if (ms === null) continue
      const ageDays = (now - ms) / dayMs
      for (const w of windows) {
        if (ageDays <= w) {
          trustlineCreated[w] = (trustlineCreated[w] ?? 0) + 1
          if (String(t.status || '').toLowerCase() === 'closed') trustlineClosed[w] = (trustlineClosed[w] ?? 0) + 1
        }
      }
    }

    for (const i of opts.incidents.value || []) {
      if (eqCode && normEq(i.equivalent) !== eqCode) continue
      if (i.initiator_pid !== pid) continue
      const ms = parseIsoMillis(i.created_at)
      if (ms === null) continue
      const ageDays = (now - ms) / dayMs
      for (const w of windows) {
        if (ageDays <= w) incidentCount[w] = (incidentCount[w] ?? 0) + 1
      }
    }

    for (const a of opts.auditLog.value || []) {
      if (String(a.object_id || '') !== pid) continue
      const action = String(a.action || '')
      if (!action.startsWith('admin.participants.')) continue
      const ms = parseIsoMillis(a.timestamp)
      if (ms === null) continue
      const ageDays = (now - ms) / dayMs
      for (const w of windows) {
        if (ageDays <= w) participantOps[w] = (participantOps[w] ?? 0) + 1
      }
    }

    // F-013-1 / T1302. `unattributable` counts rows this loop had to skip because the response
    // carries no field that could place the participant in the transaction. It is not a tidiness
    // metric: a skipped row is a committed payment that exists and is not in the count, so a total
    // computed while this is non-zero is an undercount presented as a total - the same defect one
    // level down from the one this task removes.
    let unattributable = 0

    for (const tx of opts.transactions.value || []) {
      const type = String(tx.type || '')
      if (type !== 'PAYMENT' && type !== 'CLEARING') continue
      if (String(tx.state || '') !== 'COMMITTED') continue

      // The producer lifts the equivalent to the TOP level of the row and does not publish
      // `payload` at all (`_graph_fetch_transactions`). This used to read `tx.payload.equivalent`,
      // which is undefined on every real row, so the equivalent filter below never fired and the
      // reader was consulting a key the wire does not have. The `payload` fallback is kept only
      // for the mock fixture, which still ships one.
      const payload = (tx.payload || {}) as Record<string, unknown>
      const rowEqRaw =
        typeof tx.equivalent === 'string'
          ? tx.equivalent
          : typeof payload.equivalent === 'string'
            ? payload.equivalent
            : null
      if (eqCode && rowEqRaw && normEq(rowEqRaw) !== eqCode) continue

      // Participant attribution. The projection now publishes `from`/`to` on a payment and
      // `edges` on a clearing (`app/api/v1/admin.py::_graph_fetch_transactions`, 2026-09-10), so
      // most rows can be answered outright. `payload` is read only as a fallback, for the mock,
      // whose fixtures still carry it; where NEITHER source names the participant the honest
      // answer stays "we cannot tell", not "not involved" - which is what `attributable` records.
      let involved = false
      let attributable = false
      if (type === 'PAYMENT') {
        const txRow = tx as unknown as Record<string, unknown>
        const from =
          typeof txRow.from === 'string' ? txRow.from : typeof payload.from === 'string' ? payload.from : ''
        const to =
          typeof txRow.to === 'string' ? txRow.to : typeof payload.to === 'string' ? payload.to : ''
        if (from === pid || to === pid) {
          // A naming settles the positive case outright, whichever half of the pair carries it.
          attributable = true
          involved = true
        } else if (from && to) {
          // BOTH counterparties named, neither of them ours: a real "not this participant's".
          //
          // F-013-R3. This used to be `if (from || to)`, which reached the same conclusion from ONE
          // half of the pair - so a payment carrying `from` and no `to` (the producer emits each key
          // only when the internal payload has a non-empty value for it) was recorded as not ours
          // on the strength of a field nobody sent. The zero that produced is the same false zero
          // this programme exists to remove, one level further down.
          attributable = true
          involved = false
        } else if (String(tx.initiator_pid || '') === pid) {
          // The initiator is definitely a party to its own payment. The converse does not hold -
          // being someone else's initiator says nothing about whether `pid` was the counterparty -
          // so this settles the positive case only.
          attributable = true
          involved = true
        }
      } else {
        const txRow = tx as unknown as Record<string, unknown>
        const edges = Array.isArray(txRow.edges)
          ? (txRow.edges as unknown[])
          : Array.isArray(payload.edges)
            ? (payload.edges as unknown[])
            : []
        if (edges.length) {
          attributable = true
          involved = edges.some((edge) => {
            if (!edge || typeof edge !== 'object') return false
            const e = edge as Record<string, unknown>
            return e.debtor === pid || e.creditor === pid
          })
          // Kept from the original: a participant who initiated the clearing counts even if the
          // published edges do not name them. Only the shape of the guard changed, not this rule.
          if (!involved) involved = String(tx.initiator_pid || '') === pid
        } else if (String(tx.initiator_pid || '') === pid) {
          attributable = true
          involved = true
        }
      }

      if (!attributable) {
        unattributable += 1
        continue
      }
      if (!involved) continue

      const ms = parseIsoMillis(tx.updated_at || tx.created_at)
      if (ms === null) continue
      const ageDays = (now - ms) / dayMs
      for (const w of windows) {
        if (ageDays <= w) {
          if (type === 'PAYMENT') paymentCommitted[w] = (paymentCommitted[w] ?? 0) + 1
          if (type === 'CLEARING') clearingCommitted[w] = (clearingCommitted[w] ?? 0) + 1
        }
      }
    }

    return {
      windows,
      trustlineCreated,
      trustlineClosed,
      incidentCount,
      participantOps,
      paymentCommitted,
      clearingCommitted,
      // NOT `transactions.length > 0`. Length answers "did any row arrive", which is a different
      // question from "were we told anything about this collection at all", and conflating the two
      // is the whole of F-013-1: a page that never sent `include=transactions` reported a
      // confident zero for a period it had never enquired about.
      //
      // Every counter above this line was computed HERE, out of the snapshot's own arrays, so each
      // one inherits the confidence of the collection it was counted from - and `incidents` and
      // `audit_log` are not requested by this client at all, which is why theirs is silence.
      transactions: {
        ...snapshotTransactions.value,
        incomplete: snapshotTransactions.value.known && unattributable > 0,
      },
      incidents: snapshotIncidents.value,
      auditLog: snapshotAuditLog.value,
      snapshotIncidents: snapshotIncidents.value,
    }
  })

  const selectedCycles = computed(() => {
    if (!opts.selected.value || opts.selected.value.kind !== 'node') return []
    const eqCode = selectedEqCode.value
    if (!eqCode) return []
    const pid = opts.selected.value.pid

    const cycles = opts.clearingCycles.value?.equivalents?.[eqCode]?.cycles || []
    return cycles.filter((c) => c.some((e) => e.debtor === pid || e.creditor === pid)).slice(0, 10)
  })

  return {
    metricsLoading,
    metricsError,
    loadSelectedMetrics,

    selectedBalanceRows,
    selectedCounterpartySplit,
    selectedConcentration,
    netDistribution,
    selectedRank,
    selectedCapacity,
    selectedActivity,
    selectedCycles,

    decimalToAtoms,
    atomsToDecimal,
  }
}
