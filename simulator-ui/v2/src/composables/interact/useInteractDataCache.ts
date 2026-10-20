import { computed, ref, shallowRef, watch, type ComputedRef, type Ref } from 'vue'

import type { GraphSnapshot } from '../../types'
import { uiLocaleRef, type UiLocale } from '../../i18n/locale'
import { t } from '../../i18n/t'
import { actionErrorText } from '../../utils/paymentRefusalText'
import { errorLog, logFailureLine, requestIdOf } from '../../utils/errorMessage'
import { subMoney } from '../../utils/money'
import { isActiveStatus } from '../../utils/status'
import type { ParticipantInfo, TrustlineInfo } from '../../api/simulatorTypes'
import type { useInteractActions } from '../useInteractActions'
import { findAnsweredRow, type TrustlinesFetchState } from './trustlinesSourceState'
import { canonicalEquivalent, sameEquivalent } from '../../config/equivalents'

// Register 271: an `InteractActionError` is a plain object, not an `Error` - read its `message` too.
// 046 wave 2: a coded answer is told by its code in the language of the interface (`actionErrorText`), not by the server's
// English message; a failure without a code keeps its own text.
// 048 S3 (§15 Codex P2): the text only - the request id is kept apart by the caller that shows it (`trustlinesLastErrorId`).
// 046 wave 3 (BACKLOG 046-3): told in the asked language; the cache keeps the ERROR, not this text, so a reader that
// computes it follows a switch.
function getErrorMessage(error: unknown, fallback: string, locale: UiLocale): string {
  return (error == null ? '' : actionErrorText(error, locale).text.trim()) || fallback
}

export type PaymentTargetDetail = { hops: number; max_available: string | null }

export function useInteractDataCache(opts: {
  actions: ReturnType<typeof useInteractActions>
  runId: Ref<string>
  equivalent: Ref<string>
  snapshot: Ref<GraphSnapshot | null>
  parseAmountStringOrNull: (v: unknown) => string | null
}): {
  participants: ComputedRef<ParticipantInfo[]>
  trustlines: ComputedRef<TrustlineInfo[]>
  trustlinesLoading: ComputedRef<boolean>
  trustlinesLastError: ComputedRef<string | null>
  /**
   * `F-013-7`: состояние источника trustlines как СОСТОЯНИЕ. Две величины выше его не описывают:
   * при обеих снятых источник может быть и «ответившим», и «не спрошенным», а это разные вещи.
   */
  trustlinesFetchState: ComputedRef<TrustlinesFetchState>
  /**
   * Строка пары из того списка, который источник ДЕЙСТВИТЕЛЬНО прислал для текущих (run, eq).
   * Отличается от `findActiveTrustline` тем, что не заглядывает в снапшотный фоллбэк: `null`
   * значит «источник такой строки не присылал», а не «такой линии нет».
   */
  findAnsweredTrustline: (from: string | null, to: string | null) => TrustlineInfo | null
  refreshParticipants: (o?: { force?: boolean }) => Promise<void>
  refreshTrustlines: (o?: { force?: boolean }) => Promise<void>
  /** Phase 2.5: backend-first reachable payment targets (multi-hop). */
  refreshPaymentTargets: (o: { fromPid: string; maxHops: number; force?: boolean }) => Promise<void>
  /** Cache (by run+eq+from+maxHops) for To dropdown filtering. */
  paymentTargetsByKey: Ref<Map<string, Set<string>>>
  /**
   * 037 F-037-5: what the same answer says per target, under the same key as `paymentTargetsByKey`. `hops` is the
   * shortest path (steps); `max_available` is the server's ESTIMATE of the maximum (max-flow), a different computation:
   * a decimal string, or `null` = "the server did not estimate" - which is not the measured zero `"0.00"`.
   */
  paymentTargetDetailsByKey: Ref<Map<string, Map<string, PaymentTargetDetail>>>
  /**
   * BACKLOG 037-5 (c): the LAST answer the server gave under a key, kept across the invalidation by snapshot revision
   * (which empties `paymentTargetsByKey` on every applied patch). Only for showing a list while the fresh answer is on
   * its way: it gates nothing and is not "known" - `paymentTargetsByKey` alone is. Dropped with the run or the equivalent.
   */
  paymentTargetsLastAnswerByKey: Ref<Map<string, Set<string>>>
  /** Loading flags per key. Key must match paymentTargetsByKey key. */
  paymentTargetsLoadingByKey: Ref<Map<string, boolean>>
  /** Error per cache key; stale failures cannot degrade a different active sender. */
  paymentTargetsLastErrorByKey: ComputedRef<Map<string, string>>
  paymentTargetsKey: (o: { runId: string; eq: string; fromPid: string; maxHops: number }) => string
  invalidateTrustlinesCache: (eq?: string) => void
  patchTrustlineLimitLocal: (from: string, to: string, newLimit: string, eq?: string) => void
  findActiveTrustline: (from: string | null, to: string | null) => TrustlineInfo | null
} {
  // -------------------------
  // Participants (dropdown)
  // -------------------------

  const snapshotParticipants = ref<ParticipantInfo[]>([])
  const fetchedParticipants = ref<ParticipantInfo[] | null>(null)
  const fetchedParticipantsRunId = ref<string>('')
  let participantsFetchedAtMs = 0
  let participantsFetchEpoch = 0

  const participants = computed(() => {
    const runId = normalizeRunId(opts.runId.value)
    const fetchedOk = fetchedParticipantsRunId.value === runId && fetchedParticipants.value != null
    return (fetchedOk ? fetchedParticipants.value : null) ?? snapshotParticipants.value
  })

  async function refreshParticipants(o?: { force?: boolean }) {
    // Best-effort only: dropdowns can fall back to snapshot.
    const runId = normalizeRunId(opts.runId.value)
    if (!runId) return
    const now = Date.now()
    const cachedForRun = fetchedParticipantsRunId.value === runId && fetchedParticipants.value != null
    if (!o?.force && cachedForRun && now - participantsFetchedAtMs < 30_000) return

    const myEpoch = ++participantsFetchEpoch
    try {
      const items = await opts.actions.fetchParticipants()
      // Ignore stale result.
      if (participantsFetchEpoch !== myEpoch || normalizeRunId(opts.runId.value) !== runId) return
      // Safety: don't replace snapshot-derived data with an empty list.
      if (Array.isArray(items) && items.length > 0) {
        fetchedParticipants.value = items
        fetchedParticipantsRunId.value = runId
        participantsFetchedAtMs = now
      }
    } catch {
      // ignore (fallback on snapshot)
    }
  }

  // -------------------------
  // Trustlines (dropdown + capacity)
  // -------------------------

  const snapshotTrustlines = ref<TrustlineInfo[]>([])
  const fetchedTrustlines = ref<TrustlineInfo[] | null>(null)
  const fetchedTrustlinesEq = ref<string>('')
  const fetchedTrustlinesRunId = ref<string>('')
  let trustlinesFetchedAtMs = 0
  let trustlinesFetchEpoch = 0

  const trustlinesLoadingRef = ref(false)

  // 046 wave 3 (BACKLOG 046-3): the failure is kept as the error itself (in a box: an error may be any value); its sentence is
  // told in the language of now by the computed below - until wave 3 the composed text was stored, in the language it
  // failed in.
  const trustlinesLastFailure = shallowRef<{ readonly error: unknown } | null>(null)
  const trustlinesFailureText = (locale: UiLocale): string | null => {
    const failure = trustlinesLastFailure.value
    return failure ? getErrorMessage(failure.error, t('trustlineSource.trustlinesFailed', {}, locale), locale) : null
  }
  const trustlinesLastErrorRef = computed(() => trustlinesFailureText(uiLocaleRef.value))
  /** 048 S3 (§15 Codex P2): the request id of that failure (AGENTS.md section 12), written and cleared with it. */
  const trustlinesLastErrorId = ref<string | null>(null)

  /**
   * Ответ источника ДЛЯ ТЕКУЩЕГО контекста (run + equivalent), без снапшотного фоллбэка.
   * `null` — источник для этого контекста ещё ничего не присылал.
   *
   * Это та самая величина, которую `trustlines` ниже сливает со снапшотом и тем самым теряет:
   * после слияния уже нельзя сказать, кто наполнил список. Всё, что решает МОЖНО ЛИ ДЕЙСТВОВАТЬ,
   * обязано смотреть сюда, а не в слитый список (`F-013-7`).
   */
  const answeredTrustlines = computed<TrustlineInfo[] | null>(() => {
    const runId = normalizeRunId(opts.runId.value)
    const eq = canonicalEquivalent(opts.equivalent.value)
    const fetchedOk =
      fetchedTrustlinesRunId.value === runId &&
      sameEquivalent(fetchedTrustlinesEq.value, eq) &&
      fetchedTrustlines.value != null
    return fetchedOk ? fetchedTrustlines.value : null
  })

  // 028 F-028-47 (B2): the snapshot fallback is of the snapshot's equivalent, so it serves only that one.
  const snapshotIsCurrentEq = () => sameEquivalent(opts.snapshot.value?.equivalent, opts.equivalent.value)
  const trustlines = computed(() => {
    return answeredTrustlines.value ?? (snapshotIsCurrentEq() ? snapshotTrustlines.value : [])
  })

  /**
   * `F-013-7`. Порядок ветвей здесь — это и есть содержание находки.
   *
   * «Ответил» проверяется ПЕРВЫМ и перебивает выполняющийся запрос: держать ответ и обновлять его
   * — это устаревание, а не отсутствие, и блокировать по нему мутации значило бы гасить панель на
   * каждый опрос. «Не спрашивали» — последняя ветвь, и она НЕ равна «ответил пусто»: в первом
   * случае на экране числа снапшота, за которые не поручился никто.
   */
  const trustlinesFetchState = computed<TrustlinesFetchState>(() => {
    if (answeredTrustlines.value != null) return { kind: 'answered' }
    if (trustlinesLoadingRef.value) return { kind: 'loading' }
    const message = trustlinesLastErrorRef.value
    const failure = trustlinesLastFailure.value
    // `render`: the same sentence in another language - a frozen copy of this state keeps it (046 wave 3).
    if (message && failure) {
      const render = (locale: UiLocale) => getErrorMessage(failure.error, t('trustlineSource.trustlinesFailed', {}, locale), locale)
      return { kind: 'failed', message, render, ...(trustlinesLastErrorId.value ? { ref: trustlinesLastErrorId.value } : {}) }
    }
    return { kind: 'never-asked' }
  })

  function findAnsweredTrustline(from: string | null, to: string | null): TrustlineInfo | null {
    return findAnsweredRow(answeredTrustlines.value, from, to)
  }

  function invalidateTrustlinesCache(eq?: string) {
    const curEq = canonicalEquivalent(eq ?? opts.equivalent.value)
    if (!sameEquivalent(fetchedTrustlinesEq.value, curEq)) return
    fetchedTrustlines.value = null
    fetchedTrustlinesEq.value = ''
    fetchedTrustlinesRunId.value = ''
    trustlinesFetchedAtMs = 0
  }

  function normalizeAmount(v: unknown): string {
    const s = String(v ?? '').trim()
    return opts.parseAmountStringOrNull(s) ?? s
  }

  function recomputeAvailable(used: string | null | undefined, limit: string): string | null {
    return subMoney(limit, used)
  }

  function patchList(items: TrustlineInfo[] | null, from: string, to: string, limit: string): TrustlineInfo[] | null {
    if (!Array.isArray(items) || items.length === 0) return items
    let changed = false
    const next = items.map((tl) => {
      if (tl.from_pid !== from || tl.to_pid !== to) return tl
      const available = recomputeAvailable(tl.used ?? null, limit)
      changed = true
      return {
        ...tl,
        limit,
        ...(available != null ? { available } : {}),
      }
    })
    return changed ? next : items
  }

  /**
   * Best-effort optimistic patch so UI reflects the new limit immediately.
   * Also helps when `fetchTrustlines` fails (it is intentionally swallowed).
   */
  function patchTrustlineLimitLocal(from: string, to: string, newLimit: string, eq?: string) {
    const fromPid = String(from ?? '').trim()
    const toPid = String(to ?? '').trim()
    if (!fromPid || !toPid) return

    const targetEq = canonicalEquivalent(eq ?? opts.equivalent.value)
    const limit = normalizeAmount(newLimit)
    if (!limit) return

    // Patch fetched list only if it's for the active equivalent.
    if (sameEquivalent(fetchedTrustlinesEq.value, targetEq)) {
      fetchedTrustlines.value = patchList(fetchedTrustlines.value, fromPid, toPid, limit)
    }

    // Patch snapshot-derived list (used as fallback) - only when it is of the same equivalent (B2).
    if (!sameEquivalent(opts.snapshot.value?.equivalent, targetEq)) return
    snapshotTrustlines.value = (patchList(snapshotTrustlines.value, fromPid, toPid, limit) ?? snapshotTrustlines.value) as TrustlineInfo[]
  }

  async function refreshTrustlines(o?: { force?: boolean }) {
    // Best-effort only: dropdowns can fall back to snapshot.
    const runId = normalizeRunId(opts.runId.value)
    const eq = canonicalEquivalent(opts.equivalent.value)
    if (!runId || !eq) return
    const now = Date.now()
    const cachedForContext =
      fetchedTrustlinesRunId.value === runId &&
      sameEquivalent(fetchedTrustlinesEq.value, eq) &&
      fetchedTrustlines.value != null
    if (!o?.force && cachedForContext && now - trustlinesFetchedAtMs < 15_000) return

    trustlinesLoadingRef.value = true

    const myEpoch = ++trustlinesFetchEpoch
    try {
      const items = await opts.actions.fetchTrustlines(eq)
      // Ignore stale result.
      if (
        trustlinesFetchEpoch !== myEpoch ||
        normalizeRunId(opts.runId.value) !== runId ||
        !sameEquivalent(opts.equivalent.value, eq)
      )
        return
      if (Array.isArray(items)) {
        fetchedTrustlines.value = items
        fetchedTrustlinesEq.value = eq
        fetchedTrustlinesRunId.value = runId
        trustlinesFetchedAtMs = now
        trustlinesLastFailure.value = null
        trustlinesLastErrorId.value = null
      }
    } catch (error) {
      if (
        trustlinesFetchEpoch !== myEpoch ||
        normalizeRunId(opts.runId.value) !== runId ||
        !sameEquivalent(opts.equivalent.value, eq)
      )
        return
      // Best-effort: fall back on snapshot-derived trustlines.
      // Keep a lightweight error signal for UI hints/debugging.
      trustlinesLastErrorId.value = requestIdOf(error)
      trustlinesLastFailure.value = { error }
      // 049 S2 (§15 Codex P2, AGENTS.md section 12): the notices keep the id in `data-request-id` only; the console line
      // is the other half of the correlation (one line per failed read - a read is a person's step, not a tick).
      logFailureLine('[trustlines] read failed:', errorLog(error), trustlinesLastErrorId.value)
    } finally {
      if (
        trustlinesFetchEpoch === myEpoch &&
        normalizeRunId(opts.runId.value) === runId &&
        sameEquivalent(opts.equivalent.value, eq)
      ) {
        trustlinesLoadingRef.value = false
      }
    }
  }

  const trustlinesLoading = computed(() => trustlinesLoadingRef.value)
  const trustlinesLastError = computed(() => trustlinesLastErrorRef.value)

  // -------------------------
  // Payment targets (To dropdown filtering)
  // -------------------------

  function normalizeRunId(v: unknown): string {
    return String(v ?? '').trim()
  }

  function normalizePid(v: unknown): string {
    return String(v ?? '').trim()
  }

  function paymentTargetsKey(o: { runId: string; eq: string; fromPid: string; maxHops: number }): string {
    const runId = normalizeRunId(o.runId)
    const eq = canonicalEquivalent(o.eq)
    const fromPid = normalizePid(o.fromPid)
    const maxHops = Number(o.maxHops)
    return `${runId}::${eq}::${fromPid}::${Number.isFinite(maxHops) ? String(maxHops) : 'NaN'}`
  }

  // NOTE: keep Maps in refs and replace on update so consumers can depend on ref identity.
  const paymentTargetsByKey = ref(new Map<string, Set<string>>())
  const paymentTargetDetailsByKey = ref(new Map<string, Map<string, PaymentTargetDetail>>())
  const paymentTargetsLoadingByKey = ref(new Map<string, boolean>())
  // 046 wave 3 (BACKLOG 046-3): the failures are kept as the errors; their sentences are told in the language of now.
  const paymentTargetsLastFailureByKey = ref(new Map<string, { readonly error: unknown }>())
  const paymentTargetsLastErrorByKey = computed(() => {
    const locale = uiLocaleRef.value
    const out = new Map<string, string>()
    for (const [key, failure] of paymentTargetsLastFailureByKey.value) {
      out.set(key, getErrorMessage(failure.error, t('trustlineSource.paymentTargetsFailed', {}, locale), locale))
    }
    return out
  })
  const paymentTargetsFetchEpochByKey = new Map<string, number>()
  const paymentTargetsFetchedAtMsByKey = new Map<string, number>()
  let paymentTargetsRequestSerial = 0
  const paymentTargetsLastAnswerByKey = ref(new Map<string, Set<string>>())
  const paymentTargetsLastAnswerSerialByKey = new Map<string, number>()
  /** Moves with the run or the equivalent: an answer asked for before the move belongs to nobody after it. */
  let paymentTargetsContext = 0

  // Prevent “forever stale” targets when the underlying graph changes over time.
  // Must be aligned with other dropdown caches in this file (participants=30s, trustlines=15s).
  const PAYMENT_TARGETS_TTL_MS = 10_000

  function setPaymentTargetsLoading(key: string, loading: boolean) {
    const next = new Map(paymentTargetsLoadingByKey.value)
    if (loading) next.set(key, true)
    else next.delete(key)
    paymentTargetsLoadingByKey.value = next
  }

  function setPaymentTargetsError(key: string, failure: { readonly error: unknown } | null) {
    const next = new Map(paymentTargetsLastFailureByKey.value)
    if (failure) next.set(key, failure)
    else next.delete(key)
    paymentTargetsLastFailureByKey.value = next
  }

  async function refreshPaymentTargets(o: { fromPid: string; maxHops: number; force?: boolean }) {
    const runId = normalizeRunId(opts.runId.value)
    const eq = canonicalEquivalent(opts.equivalent.value)
    const fromPid = normalizePid(o.fromPid)
    const maxHops = Number(o.maxHops)
    if (!runId || !eq || !fromPid || !Number.isFinite(maxHops) || !(maxHops >= 1)) return

    const key = paymentTargetsKey({ runId, eq, fromPid, maxHops })
    const now = Date.now()
    const cached = paymentTargetsByKey.value.get(key)

    // TTL: allow reuse for a short time, but revalidate periodically.
    if (!o.force && cached) {
      const fetchedAt = paymentTargetsFetchedAtMsByKey.get(key) ?? 0
      if (fetchedAt > 0 && now - fetchedAt < PAYMENT_TARGETS_TTL_MS) return
    }

    setPaymentTargetsLoading(key, true)
    // Request identities are process-monotonic. Cache invalidation may clear the
    // per-key ownership map, but it must never make an old same-key request current again.
    const myEpoch = ++paymentTargetsRequestSerial
    paymentTargetsFetchEpochByKey.set(key, myEpoch)
    const myContext = paymentTargetsContext

    try {
      const items = await opts.actions.fetchPaymentTargets(eq, fromPid, maxHops)
      const superseded = paymentTargetsFetchEpochByKey.get(key) !== myEpoch

      const ids = new Set<string>()
      const details = new Map<string, PaymentTargetDetail>()
      for (const it of items ?? []) {
        const pid = normalizePid(it.to_pid)
        if (!pid) continue
        ids.add(pid)
        details.set(pid, { hops: it.hops, max_available: typeof it.max_available === 'string' ? it.max_available : null })
      }

      // An answer that is no longer the newest request is still what the server last said for this key - unless the
      // run or the equivalent moved, or a later request has already answered.
      if (myContext === paymentTargetsContext && !((paymentTargetsLastAnswerSerialByKey.get(key) ?? 0) > myEpoch)) {
        paymentTargetsLastAnswerSerialByKey.set(key, myEpoch)
        paymentTargetsLastAnswerByKey.value = new Map(paymentTargetsLastAnswerByKey.value).set(key, ids)
      }
      // Ignore stale result for the same key.
      if (superseded) return

      const next = new Map(paymentTargetsByKey.value)
      next.set(key, ids)
      paymentTargetsByKey.value = next
      const nextDetails = new Map(paymentTargetDetailsByKey.value)
      nextDetails.set(key, details)
      paymentTargetDetailsByKey.value = nextDetails
      setPaymentTargetsError(key, null)
      paymentTargetsFetchedAtMsByKey.set(key, now)
    } catch (error) {
      // Ignore stale error for the same key.
      if (paymentTargetsFetchEpochByKey.get(key) !== myEpoch) return

      // Best-effort: treat as known-empty (so UI does not crash / is deterministic).
      const next = new Map(paymentTargetsByKey.value)
      next.set(key, new Set())
      paymentTargetsByKey.value = next
      const nextDetails = new Map(paymentTargetDetailsByKey.value)
      nextDetails.set(key, new Map())
      paymentTargetDetailsByKey.value = nextDetails
      setPaymentTargetsError(key, { error })
      // Treat error response as “known” for UI determinism, but still revalidate after TTL.
      paymentTargetsFetchedAtMsByKey.set(key, now)
    } finally {
      // Only clear if this fetch is still the latest for the key.
      if (paymentTargetsFetchEpochByKey.get(key) === myEpoch) {
        setPaymentTargetsLoading(key, false)
      }
    }
  }

  // Run/EQ change invalidates payment targets semantics.
  watch(
    () => `${normalizeRunId(opts.runId.value)}::${canonicalEquivalent(opts.equivalent.value)}`,
    () => {
      paymentTargetsContext += 1
      paymentTargetsLastAnswerByKey.value = new Map()
      paymentTargetsLastAnswerSerialByKey.clear()
      paymentTargetsByKey.value = new Map()
      paymentTargetDetailsByKey.value = new Map()
      paymentTargetsLoadingByKey.value = new Map()
      paymentTargetsLastFailureByKey.value = new Map()
      paymentTargetsFetchEpochByKey.clear()
      paymentTargetsFetchedAtMsByKey.clear()
    },
    { immediate: true },
  )

  // Snapshot change implies the underlying graph semantics may have changed.
  // Keep behavior deterministic: clear payment-targets cache so consumers can re-fetch.
  //
  // `data_revision` ДОБАВЛЕН 2026-09-10 (`F-013-2` / `T1303`), и без него этот watch пропускал
  // ровно те изменения, ради которых он нужен: `tx.updated` и `clearing.done` применяют патчи и
  // НЕ двигают `generated_at` (`realEventPipeline.ts:349-350`, `:414-415` против `:236`, `:312`),
  // поэтому после платежа, изменившего балансы, кэш целей продолжал отвечать доплатёжным
  // множеством — и `canSendPayment` разрешал отправку по маршруту, которого уже нет.
  watch(
    () => `${String(opts.snapshot.value?.generated_at ?? '')}|${opts.snapshot.value?.data_revision ?? 0}`,
    () => {
      paymentTargetsByKey.value = new Map()
      paymentTargetDetailsByKey.value = new Map()
      paymentTargetsLoadingByKey.value = new Map()
      paymentTargetsLastFailureByKey.value = new Map()
      paymentTargetsFetchEpochByKey.clear()
      paymentTargetsFetchedAtMsByKey.clear()
    },
    { immediate: false },
  )

  function findActiveTrustline(from: string | null, to: string | null): TrustlineInfo | null {
    if (!from || !to) return null
    const items = trustlines.value
    for (const tl of items ?? []) {
      if (tl.from_pid === from && tl.to_pid === to && isActiveStatus(tl.status)) return tl
    }
    return null
  }

  // Keep participants cache scoped to the active run. Clearing snapshot-derived
  // fallback prevents the previous run from leaking while its replacement loads.
  watch(
    () => normalizeRunId(opts.runId.value),
    () => {
      participantsFetchEpoch += 1
      fetchedParticipants.value = null
      fetchedParticipantsRunId.value = ''
      participantsFetchedAtMs = 0
      snapshotParticipants.value = []
      void refreshParticipants()
    },
    { immediate: true },
  )

  // Trustlines are scoped by run and equivalent. Epoch invalidation also keeps
  // late success/error/finally branches from publishing into the new context.
  watch(
    () => `${normalizeRunId(opts.runId.value)}::${canonicalEquivalent(opts.equivalent.value)}`,
    () => {
      trustlinesFetchEpoch += 1
      fetchedTrustlines.value = null
      fetchedTrustlinesEq.value = ''
      fetchedTrustlinesRunId.value = ''
      trustlinesFetchedAtMs = 0
      trustlinesLoadingRef.value = false
      trustlinesLastFailure.value = null
      trustlinesLastErrorId.value = null
      snapshotTrustlines.value = []
      void refreshTrustlines()
    },
    { immediate: true },
  )

  watch(
    () => opts.snapshot.value,
    (snap) => {
      if (!snap) {
        snapshotParticipants.value = []
        snapshotTrustlines.value = []
        return
      }

      snapshotParticipants.value = (snap.nodes ?? []).map((n) => ({
        pid: n.id,
        name: n.name ?? n.id,
        type: n.type ?? '',
        status: n.status ?? 'active',
      }))

      const nameByPid = new Map<string, string>()
      for (const n of snap.nodes ?? []) {
        const pid = n.id.trim()
        if (!pid) continue
        const nm = (n.name ?? pid).trim() || pid
        nameByPid.set(pid, nm)
      }

      const eq = canonicalEquivalent(snap.equivalent)
      snapshotTrustlines.value = (snap.links ?? []).map((l) => {
        const from = l.source
        const to = l.target

        // NOTE(14.7): Snapshot fallback for trustlines is best-effort; `reverse_used` is mapped when present.
        // 026: a close with debt is a request, so no debt guard reads it; the request state comes through.
        const reverseUsed = opts.parseAmountStringOrNull(l.reverse_used)
        return {
          from_pid: from,
          from_name: nameByPid.get(from) ?? from,
          to_pid: to,
          to_name: nameByPid.get(to) ?? to,
          equivalent: eq,
          limit: normalizeAmount(l.trust_limit),
          used: normalizeAmount(l.used),
          ...(reverseUsed != null ? { reverse_used: reverseUsed } : {}),
          available: normalizeAmount(l.available),
          status: l.status ?? 'active',
          close_requested_at: l.close_requested_at ?? null,
        }
      })
    },
    { immediate: true },
  )

  return {
    participants,
    trustlines,
    trustlinesLoading,
    trustlinesLastError,
    trustlinesFetchState,
    findAnsweredTrustline,
    refreshParticipants,
    refreshTrustlines,
    refreshPaymentTargets,
    paymentTargetsByKey,
    paymentTargetDetailsByKey,
    paymentTargetsLastAnswerByKey,
    paymentTargetsLoadingByKey,
    paymentTargetsLastErrorByKey,
    paymentTargetsKey,
    invalidateTrustlinesCache,
    patchTrustlineLimitLocal,
    findActiveTrustline,
  }
}
