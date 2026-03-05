import type { Ref } from 'vue'

import type { ClearingDoneEvent } from '../../types'
import { CLEARING_LABEL_COLOR } from '../../config/fxConfig'
import { spawnEdgePulses as spawnEdgePulsesDefault, spawnNodeBursts as spawnNodeBurstsDefault } from '../../render/fxRenderer'
import { computeClearingAmountAnchorFromEdgeMidpoints } from '../../utils/clearingAmountAnchor'
import { isZeroDecimalString } from '../../utils/isZeroDecimalString'
import { __retryUntilTruthyOrDeadline } from '../../utils/retryUntilTruthy'
import type { ClearingAmountOverlay } from '../useOverlayState'

export type ClearingFxParams = {
  edges: Array<{ from: string; to: string }>
  totalAmount: string // e.g. "10.00"
  equivalent: string // e.g. "UAH"
  planId?: string // optional; used for throttle key
}

type SpawnEdgePulsesFn = typeof spawnEdgePulsesDefault
type SpawnNodeBurstsFn = typeof spawnNodeBurstsDefault

function nodesFromEdges(edges: Array<{ from: string; to: string }>): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  for (const e of edges) {
    if (!seen.has(e.from)) {
      seen.add(e.from)
      out.push(e.from)
    }
    if (!seen.has(e.to)) {
      seen.add(e.to)
      out.push(e.to)
    }
  }
  return out
}

export function useRealClearingFx(deps: {
  fxState: any
  isTestMode: Readonly<Ref<boolean>>
  isWebDriver: boolean

  keyEdge: (from: string, to: string) => string
  seedFn: (s: string) => number

  clearingColor: string

  addActiveNode: (nodeId: string, ttlMs: number) => void
  addActiveEdge: (edgeKey: string, ttlMs: number) => void

  pushClearingAmountOverlay?: (
    overlay: ClearingAmountOverlay,
    opts: {
      color: string
      throttleMs?: number
    },
  ) => void

  scheduleTimeout: (fn: () => void, ms: number) => void
  getLayoutNodeById: (nodeId: string) => any
  setFlash: (v: number) => void

  nowMs?: () => number
  nowEpochMs?: () => number

  spawnEdgePulses?: SpawnEdgePulsesFn
  spawnNodeBursts?: SpawnNodeBurstsFn
}): {
  runClearingFx: (params: ClearingFxParams) => void
  runRealClearingDoneFx: (done: ClearingDoneEvent) => void
  resetDedup: () => void
} {
  const nowMs =
    deps.nowMs ??
    (() => {
      return typeof performance !== 'undefined' ? performance.now() : Date.now()
    })

  const nowEpochMs = deps.nowEpochMs ?? (() => Date.now())

  const spawnEdgePulses = deps.spawnEdgePulses ?? spawnEdgePulsesDefault
  const spawnNodeBursts = deps.spawnNodeBursts ?? spawnNodeBurstsDefault

  /** Edge-signature dedup: prevents double FX spawn when both HTTP and SSE paths fire. */
  // IMPORTANT: dedup policy must NOT suppress distinct clearing.done events that happen to
  // share the same edge set (common in small graphs / debug "clearing-once" loops).
  //
  // - HTTP path (Interact clearing-real) has no plan_id -> dedup by edge signature
  // - SSE path (clearing.done) has plan_id -> dedup by plan_id, but also suppress if
  //   a matching edge signature was just processed by the HTTP path (double-fire)
  const _clearingFxDedupByEdgeSig = new Map<string, number>() // edgeSig → timestamp (HTTP)
  const _clearingFxDedupByPlanId = new Map<string, number>() // planId → timestamp (SSE)
  const CLEARING_FX_DEDUP_WINDOW_MS = 3000

  function gcClearingFxDedup(map: Map<string, number>, now: number) {
    if (map.size <= 50) return
    for (const [k, t] of map) {
      if (now - t > CLEARING_FX_DEDUP_WINDOW_MS * 2) map.delete(k)
    }
  }

  function pushClearingLabelDeferred(
    opts: { edges: Array<{ from: string; to: string }>; text: string; planId?: string },
    startedAtMs = nowMs(),
    retryDelayMs = 80,
  ) {
    const getNodeWorldPos = (id: string) => {
      const ln = deps.getLayoutNodeById(id)
      if (!ln || typeof ln.__x !== 'number' || typeof ln.__y !== 'number') return null
      return { x: ln.__x, y: ln.__y }
    }

    const maxWaitMs = 800
    const overlayId = `clearing-total:${opts.planId ?? Math.round(startedAtMs)}`

    __retryUntilTruthyOrDeadline({
      startedAtMs,
      maxWaitMs,
      retryDelayMs,
      nowMs,
      scheduleTimeout: deps.scheduleTimeout,
      get: () => computeClearingAmountAnchorFromEdgeMidpoints(opts.edges, getNodeWorldPos),
      onSuccess: (anchor) => {
        const overlay = {
          id: overlayId,
          text: opts.text,
          worldX: anchor.x,
          worldY: anchor.y,
          ttlMs: 3800,
          styleKey: 'clearing-premium',
          planId: opts.planId,
        } satisfies ClearingAmountOverlay

        if (deps.pushClearingAmountOverlay) {
          deps.pushClearingAmountOverlay(overlay, { color: CLEARING_LABEL_COLOR, throttleMs: 500 })
        }
      },
      onTimeout: () => undefined,
    })
  }

  function runClearingFx(params: ClearingFxParams) {
    const { edges: edgesAll, totalAmount, equivalent, planId } = params

    const edgeSig = edgesAll.map((e) => `${e.from}>${e.to}`).sort().join('|')
    const now = nowEpochMs()

    if (planId) {
      const prevPlan = _clearingFxDedupByPlanId.get(planId)
      if (prevPlan !== undefined && now - prevPlan < CLEARING_FX_DEDUP_WINDOW_MS) return

      const prevHttp = _clearingFxDedupByEdgeSig.get(edgeSig)
      if (prevHttp !== undefined && now - prevHttp < CLEARING_FX_DEDUP_WINDOW_MS) return

      _clearingFxDedupByPlanId.set(planId, now)
      gcClearingFxDedup(_clearingFxDedupByPlanId, now)
    } else {
      const prev = _clearingFxDedupByEdgeSig.get(edgeSig)
      if (prev !== undefined && now - prev < CLEARING_FX_DEDUP_WINDOW_MS) return
      _clearingFxDedupByEdgeSig.set(edgeSig, now)
      gcClearingFxDedup(_clearingFxDedupByEdgeSig, now)
    }

    const tNowMs = nowMs()
    const nodeIds = nodesFromEdges(edgesAll)

    deps.setFlash(0.55)

    for (const id of nodeIds) deps.addActiveNode(id, 5200)

    const edgesFx = edgesAll.length > 30 ? edgesAll.slice(0, 30) : edgesAll
    if (edgesFx.length > 0) {
      spawnEdgePulses(deps.fxState, {
        edges: edgesFx,
        nowMs: tNowMs,
        durationMs: 4200,
        color: deps.clearingColor,
        thickness: 3.2,
        seedPrefix: `clearing:${planId ?? tNowMs.toFixed(0)}`,
        countPerEdge: 1,
        keyEdge: deps.keyEdge,
        seedFn: deps.seedFn,
        isTestMode: deps.isTestMode.value && deps.isWebDriver,
      })
    }

    for (const e of edgesAll) deps.addActiveEdge(deps.keyEdge(e.from, e.to), 5200)

    const burstNodeIds = nodeIds.slice(0, 40)
    if (burstNodeIds.length > 0) {
      spawnNodeBursts(deps.fxState, {
        nodeIds: burstNodeIds,
        nowMs: tNowMs,
        durationMs: 2800,
        color: deps.clearingColor,
        kind: 'clearing',
        seedPrefix: `clearing-burst:${planId ?? tNowMs.toFixed(0)}`,
        seedFn: deps.seedFn,
        isTestMode: deps.isTestMode.value && deps.isWebDriver,
      })
    }

    const clearedAmount = totalAmount.trim()
    if (edgesAll.length > 0 && clearedAmount && !isZeroDecimalString(clearedAmount)) {
      pushClearingLabelDeferred({
        edges: edgesAll,
        text: `−${clearedAmount.replace(/^-/, '')} ${equivalent}`,
        planId,
      })
    }
  }

  function runRealClearingDoneFx(done: ClearingDoneEvent) {
    const doneCycleEdges = (done as any)?.cycle_edges
    const edges: Array<{ from: string; to: string }> = []
    if (Array.isArray(doneCycleEdges) && doneCycleEdges.length > 0) {
      for (const e of doneCycleEdges) edges.push({ from: e.from, to: e.to })
    }
    if (edges.length === 0) return

    runClearingFx({
      edges,
      totalAmount: String((done as any)?.cleared_amount ?? '0'),
      equivalent: String((done as any)?.equivalent ?? ''),
      planId: String((done as any)?.plan_id ?? '') || undefined,
    })
  }

  function resetDedup(): void {
    _clearingFxDedupByEdgeSig.clear()
    _clearingFxDedupByPlanId.clear()
  }

  return { runClearingFx, runRealClearingDoneFx, resetDedup }
}
