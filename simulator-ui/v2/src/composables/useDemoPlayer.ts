import { reactive } from 'vue'
import type { ClearingDoneEvent, ClearingPlanEvent, DemoEvent, TxUpdatedEvent } from '../types'
import type { LayoutNodeWithId as BaseLayoutNodeWithId } from '../types/layout'
import { getFxConfig, intensityScale } from '../config/fxConfig'

// Demo visuals must match real visuals 1:1.
const CLEARING_ANIMATION = getFxConfig('real')

const REAL_TX_TTL_MIN_MS = 240
const REAL_TX_TTL_MAX_MS = 900

function clampRealTxTtlMs(ttlRaw: unknown, fallbackMs = 1200): number {
  const ttlN = Number(ttlRaw ?? fallbackMs)
  const ttl = Number.isFinite(ttlN) ? ttlN : fallbackMs
  return Math.max(REAL_TX_TTL_MIN_MS, Math.min(REAL_TX_TTL_MAX_MS, ttl))
}

function pickSparkEdges(edges: TxUpdatedEvent['edges']): Array<{ from: string; to: string }> {
  if (!edges || edges.length === 0) return []
  if (edges.length <= 2) return edges
  const first = edges[0]!
  const last = edges[edges.length - 1]!
  if (first.from === last.from && first.to === last.to) return [first]
  return [first, last]
}

function intensityCountPerEdge(intensityKey?: string): number {
  const s = intensityScale(intensityKey)
  return s >= 1.3 ? 2 : 1
}

export type SceneId = 'A' | 'B' | 'C' | 'D' | 'E'

export type LayoutNode = BaseLayoutNodeWithId

export type FloatingLabelOpts = {
  id?: number
  nodeId: string
  text: string
  color: string
  ttlMs?: number
  offsetXPx?: number
  offsetYPx?: number
  throttleKey?: string
  throttleMs?: number
}

type DemoPlayerDeps = {
  // Patches
  applyPatches: (evt: DemoEvent) => void

  // FX spawning (DI: App.vue passes imported functions)
  spawnSparks: (opts: {
    edges: Array<{ from: string; to: string }>
    nowMs: number
    ttlMs: number
    colorCore: string
    colorTrail: string
    thickness: number
    kind: 'comet' | 'beam'
    seedPrefix: string
    countPerEdge: number
    keyEdge: (a: string, b: string) => string
    seedFn: (s: string) => number
    isTestMode: boolean
  }) => void

  spawnNodeBursts: (opts: {
    nodeIds: string[]
    nowMs: number
    durationMs: number
    color: string
    kind: 'clearing' | 'tx-impact' | 'glow'
    seedPrefix: string
    seedFn: (s: string) => number
    isTestMode: boolean
  }) => void

  spawnEdgePulses: (opts: {
    edges: Array<{ from: string; to: string }>
    nowMs: number
    durationMs: number
    color: string
    thickness: number
    seedPrefix: string
    countPerEdge: number
    keyEdge: (a: string, b: string) => string
    seedFn: (s: string) => number
    isTestMode: boolean
  }) => void

  // UI feedback
  pushFloatingLabel: (opts: FloatingLabelOpts) => void
  setFlash: (v: number) => void
  resetOverlays: () => void
  fxColorForNode: (id: string, fallback: string) => string
  addActiveEdge: (key: string, ttlMs?: number) => void

  // Timing
  scheduleTimeout: (fn: () => void, ms: number) => number
  clearScheduledTimeouts: () => void

  /**
   * Optional: notify external systems that a demo event changed visual state.
   * Used to briefly keep the render loop active and/or wake it from deep idle.
   */
  onDemoEvent?: () => void

  // Layout access (read-only)
  getLayoutNode: (id: string) => LayoutNode | undefined

  // Optional: adaptive FX budget scale (1 = full). Provided by render loop via fxState.
  getFxBudgetScale?: () => number

  // Helpers/config
  isTestMode: () => boolean
  isWebDriver: boolean
  effectiveEq: () => string
  keyEdge: (a: string, b: string) => string
  seedFn: (s: string) => number
  edgeDirCaption: () => string

  // Mapping bits
  txSparkCore: string
  txSparkTrail: string
  clearingFlashFallback: string
}

export function useDemoPlayer(deps: DemoPlayerDeps) {
  const playlist = reactive({
    playing: false,
    txIndex: 0,
    clearingStepIndex: 0,
  })

  let txRunSeq = 0
  let clearingRunSeq = 0

  function stopPlaylistPlayback() {
    playlist.playing = false
    deps.clearScheduledTimeouts()
  }

  function resetPlaylistPointers() {
    playlist.playing = false
    playlist.txIndex = 0
    playlist.clearingStepIndex = 0
  }

  function resetDemoState() {
    stopPlaylistPlayback()
    resetPlaylistPointers()
    deps.resetOverlays()
  }

  function runTxEvent(evt: TxUpdatedEvent, opts?: { onFinished?: () => void }) {
    deps.onDemoEvent?.()
    const fxTestMode = deps.isTestMode() && deps.isWebDriver
    const runId = ++txRunSeq

    // Real mode applies patches immediately upon event receipt. Keep demo identical.
    if (!fxTestMode) deps.applyPatches(evt)

    const budgetScaleRaw = typeof deps.getFxBudgetScale === 'function' ? deps.getFxBudgetScale() : 1
    const budgetScale = Math.max(0.25, Math.min(1, Number.isFinite(budgetScaleRaw) ? budgetScaleRaw : 1))
    const allowBursts = budgetScale >= 0.7

    const sparkEdges = pickSparkEdges(evt.edges)
    if (sparkEdges.length === 0) return

    const ttl = clampRealTxTtlMs(evt.ttl_ms)
    const k = intensityScale(evt.intensity_key)

    deps.spawnSparks({
      edges: sparkEdges,
      nowMs: performance.now(),
      ttlMs: ttl,
      colorCore: deps.txSparkCore,
      colorTrail: deps.txSparkTrail,
      thickness: 0.95 * k,
      kind: 'beam',
      seedPrefix: `tx:${deps.effectiveEq()}`,
      countPerEdge: 1,
      keyEdge: deps.keyEdge,
      seedFn: deps.seedFn,
      isTestMode: fxTestMode,
    })

    if (!fxTestMode && allowBursts) {
      const sourceId = sparkEdges[0]!.from
      deps.spawnNodeBursts({
        nodeIds: [sourceId],
        nowMs: performance.now(),
        durationMs: 280,
        color: deps.fxColorForNode(sourceId, deps.txSparkTrail),
        kind: 'tx-impact',
        seedPrefix: 'burst-src',
        seedFn: deps.seedFn,
        isTestMode: fxTestMode,
      })
    }

    if (!fxTestMode && allowBursts) {
      const targetId = sparkEdges[sparkEdges.length - 1]!.to

      deps.scheduleTimeout(() => {
        if (runId !== txRunSeq) return

        deps.spawnNodeBursts({
          nodeIds: [targetId],
          nowMs: performance.now(),
          durationMs: 420,
          color: deps.fxColorForNode(targetId, deps.txSparkTrail),
          kind: 'tx-impact',
          seedPrefix: 'burst',
          seedFn: deps.seedFn,
          isTestMode: fxTestMode,
        })
      }, ttl)
    }

    if (fxTestMode) return

    // Real mode does not reset overlays at the end of tx — FX prunes itself.
    // Keep a short callback for demo UI control flow.
    deps.scheduleTimeout(() => {
      if (runId !== txRunSeq) return
      opts?.onFinished?.()
    }, ttl + 60)
  }

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

  function allPlanEdges(plan: ClearingPlanEvent): Array<{ from: string; to: string }> {
    const out: Array<{ from: string; to: string }> = []
    for (const step of plan.steps ?? []) {
      for (const e of step.highlight_edges ?? []) out.push({ from: e.from, to: e.to })
      for (const e of step.particles_edges ?? []) out.push({ from: e.from, to: e.to })
    }

    const cycleEdges = (plan as any).cycle_edges as Array<{ from: string; to: string }> | undefined
    if (Array.isArray(cycleEdges) && cycleEdges.length > 0) {
      for (const e of cycleEdges) out.push({ from: e.from, to: e.to })
    }

    return out
  }

  function runRealLikeClearingPlanFx(plan: ClearingPlanEvent, runId: number) {
    const planId = String(plan?.plan_id ?? '')
    if (!planId) return

    const clearingColor = deps.clearingFlashFallback

    const edgesAll = allPlanEdges(plan)
    if (edgesAll.length > 0) {
      const nowMs = performance.now()
      for (const e of edgesAll) deps.addActiveEdge(deps.keyEdge(e.from, e.to), CLEARING_ANIMATION.highlightPulseMs + 3000)

      deps.spawnEdgePulses({
        edges: edgesAll,
        nowMs,
        durationMs: CLEARING_ANIMATION.highlightPulseMs,
        color: clearingColor,
        thickness: CLEARING_ANIMATION.highlightThickness,
        seedPrefix: `demo-clearing:plan:${planId}:${plan.equivalent}`,
        countPerEdge: 1,
        keyEdge: deps.keyEdge,
        seedFn: deps.seedFn,
        isTestMode: deps.isTestMode() && deps.isWebDriver,
      })
    }

    for (const step of plan.steps ?? []) {
      const atMs = Math.max(0, Number(step.at_ms ?? 0))
      deps.scheduleTimeout(() => {
        if (runId !== clearingRunSeq) return
        const nowMs = performance.now()
        const budgetScaleRaw = typeof deps.getFxBudgetScale === 'function' ? deps.getFxBudgetScale() : 1
        const budgetScale = Math.max(0.25, Math.min(1, Number.isFinite(budgetScaleRaw) ? budgetScaleRaw : 1))
        const burstNodeCap = budgetScale >= 0.85 ? 30 : budgetScale >= 0.7 ? 16 : 8

        const k = intensityScale(step.intensity_key)

        const particles = (step.particles_edges ?? []).map((e) => ({ from: e.from, to: e.to }))
        if (particles.length > 0) {
          deps.spawnSparks({
            edges: particles,
            nowMs,
            ttlMs: CLEARING_ANIMATION.microTtlMs,
            colorCore: deps.txSparkCore,
            colorTrail: clearingColor,
            thickness: CLEARING_ANIMATION.microThickness * k,
            kind: 'comet',
            seedPrefix: `demo-clearing:micro:${planId}:${plan.equivalent}:${step.at_ms}`,
            countPerEdge: 1,
            keyEdge: deps.keyEdge,
            seedFn: deps.seedFn,
            isTestMode: deps.isTestMode() && deps.isWebDriver,
          })

          if (budgetScale >= 0.55) {
            const nodeIds = nodesFromEdges(particles)
            const capped = nodeIds.length > burstNodeCap ? nodeIds.slice(0, burstNodeCap) : nodeIds
            deps.spawnNodeBursts({
              nodeIds: capped,
              nowMs,
              durationMs: CLEARING_ANIMATION.nodeBurstMs,
              color: clearingColor,
              kind: 'clearing',
              seedPrefix: `demo-clearing:nodes:${planId}:${step.at_ms}`,
              seedFn: deps.seedFn,
              isTestMode: deps.isTestMode() && deps.isWebDriver,
            })
          }
        }
      }, atMs)
    }
  }

  function runRealLikeClearingDoneFx(plan: ClearingPlanEvent | undefined, done: ClearingDoneEvent, runId: number) {
    if (runId !== clearingRunSeq) return
    const nowMs = performance.now()
    const clearingColor = deps.clearingFlashFallback

    const budgetScaleRaw = typeof deps.getFxBudgetScale === 'function' ? deps.getFxBudgetScale() : 1
    const budgetScale = Math.max(0.25, Math.min(1, Number.isFinite(budgetScaleRaw) ? budgetScaleRaw : 1))
    const burstNodeCap = budgetScale >= 0.85 ? 30 : budgetScale >= 0.7 ? 16 : 8

    // Match real-mode: single flash at clearing completion.
    deps.setFlash(0.85)

    const planEdges: Array<{ from: string; to: string }> = []
    if (plan?.steps) {
      for (const s of plan.steps) {
        for (const e of s.highlight_edges ?? []) planEdges.push({ from: e.from, to: e.to })
        for (const e of s.particles_edges ?? []) planEdges.push({ from: e.from, to: e.to })
      }
    }

    const cycleEdges = (plan as any)?.cycle_edges ?? (done as any)?.cycle_edges
    if (planEdges.length === 0 && Array.isArray(cycleEdges)) {
      for (const e of cycleEdges) planEdges.push({ from: e.from, to: e.to })
    }

    if (planEdges.length === 0 && done.node_patch && done.node_patch.length >= 2) {
      const patchIds = done.node_patch.map((p) => p.id).filter(Boolean)
      for (let i = 0; i < patchIds.length; i++) {
        const from = patchIds[i]!
        const to = patchIds[(i + 1) % patchIds.length]!
        if (from && to) planEdges.push({ from, to })
      }
    }

    const nodeIds = nodesFromEdges(planEdges)

    if (planEdges.length > 0) {
      deps.spawnEdgePulses({
        edges: planEdges,
        nowMs,
        durationMs: 4200,
        color: clearingColor,
        thickness: 3.2,
        seedPrefix: `demo-clearing:done:${done.plan_id}`,
        countPerEdge: 1,
        keyEdge: deps.keyEdge,
        seedFn: deps.seedFn,
        isTestMode: deps.isTestMode() && deps.isWebDriver,
      })

      for (const e of planEdges) deps.addActiveEdge(deps.keyEdge(e.from, e.to), 5200)

      if (budgetScale >= 0.55) {
        const capped = nodeIds.length > burstNodeCap ? nodeIds.slice(0, burstNodeCap) : nodeIds
        deps.spawnNodeBursts({
          nodeIds: capped,
          nowMs,
          durationMs: 900,
          color: clearingColor,
          kind: 'clearing',
          seedPrefix: `demo-clearing:done-nodes:${done.plan_id}`,
          seedFn: deps.seedFn,
          isTestMode: deps.isTestMode() && deps.isWebDriver,
        })
      }
    }

    // Match real-mode: only show total label when backend provides cleared_amount.
    // Demo fixtures do not have it, so do not invent amounts.
  }

  function runClearingStep(
    stepIndex: number,
    plan: ClearingPlanEvent,
    done: ClearingDoneEvent | null,
    opts?: { onFinished?: () => void },
  ) {
    deps.onDemoEvent?.()
    const step = plan.steps[stepIndex]
    if (!step) return

    const fxTestMode = deps.isTestMode() && deps.isWebDriver
    if (fxTestMode) {
      for (const e of step.highlight_edges ?? []) deps.addActiveEdge(deps.keyEdge(e.from, e.to))
      return
    }

    const runId = ++clearingRunSeq

    // Step-level preview: mimic real plan behavior, but scoped to this step.
    const clearingColor = deps.clearingFlashFallback
    const highlightEdges = (step.highlight_edges ?? []).map((e) => ({ from: e.from, to: e.to }))
    const particles = (step.particles_edges ?? []).map((e) => ({ from: e.from, to: e.to }))

    const k = intensityScale(step.intensity_key)

    if (highlightEdges.length > 0) {
      const nowMs = performance.now()
      for (const e of highlightEdges) deps.addActiveEdge(deps.keyEdge(e.from, e.to), CLEARING_ANIMATION.highlightPulseMs + 3000)
      deps.spawnEdgePulses({
        edges: highlightEdges,
        nowMs,
        durationMs: CLEARING_ANIMATION.highlightPulseMs,
        color: clearingColor,
        thickness: CLEARING_ANIMATION.highlightThickness,
        seedPrefix: `demo-clearing:step:${plan.plan_id}:${step.at_ms}`,
        countPerEdge: 1,
        keyEdge: deps.keyEdge,
        seedFn: deps.seedFn,
        isTestMode: false,
      })
    }

    if (particles.length > 0) {
      deps.spawnSparks({
        edges: particles,
        nowMs: performance.now(),
        ttlMs: CLEARING_ANIMATION.microTtlMs,
        colorCore: deps.txSparkCore,
        colorTrail: clearingColor,
        thickness: CLEARING_ANIMATION.microThickness * k,
        kind: 'comet',
        seedPrefix: `demo-clearing:step-micro:${plan.plan_id}:${step.at_ms}`,
        countPerEdge: 1,
        keyEdge: deps.keyEdge,
        seedFn: deps.seedFn,
        isTestMode: fxTestMode,
      })

      const budgetScaleRaw = typeof deps.getFxBudgetScale === 'function' ? deps.getFxBudgetScale() : 1
      const budgetScale = Math.max(0.25, Math.min(1, Number.isFinite(budgetScaleRaw) ? budgetScaleRaw : 1))
      const burstNodeCap = budgetScale >= 0.85 ? 30 : budgetScale >= 0.7 ? 16 : 8
      if (budgetScale >= 0.55) {
        const nodeIds = nodesFromEdges(particles)
        const capped = nodeIds.length > burstNodeCap ? nodeIds.slice(0, burstNodeCap) : nodeIds
        deps.spawnNodeBursts({
          nodeIds: capped,
          nowMs: performance.now(),
          durationMs: CLEARING_ANIMATION.nodeBurstMs,
          color: clearingColor,
          kind: 'clearing',
          seedPrefix: `demo-clearing:step-nodes:${plan.plan_id}:${step.at_ms}`,
          seedFn: deps.seedFn,
          isTestMode: fxTestMode,
        })
      }
    }

    const isLast = stepIndex >= plan.steps.length - 1
    const cleanupDelayMs = Math.max(350, CLEARING_ANIMATION.microTtlMs + 120)

    deps.scheduleTimeout(() => {
      if (runId !== clearingRunSeq) return
      if (isLast && done) {
        deps.applyPatches(done)
        runRealLikeClearingDoneFx(plan, done, runId)
      }
      opts?.onFinished?.()
    }, cleanupDelayMs)
  }

  function runClearingOnce(plan: ClearingPlanEvent, done: ClearingDoneEvent | null) {
    deps.onDemoEvent?.()
    // In Playwright test-mode, apply the first highlight step deterministically (no timers/FX).
    const fxTestMode = deps.isTestMode() && deps.isWebDriver
    if (fxTestMode) {
      const step0 = plan.steps[0]
      for (const e of step0?.highlight_edges ?? []) deps.addActiveEdge(deps.keyEdge(e.from, e.to))
      return
    }

    const runId = ++clearingRunSeq

    runRealLikeClearingPlanFx(plan, runId)

    const maxAt = Math.max(
      0,
      ...((plan.steps ?? []).map((s) => Number(s.at_ms ?? 0)).filter((v) => Number.isFinite(v))),
    )
    const doneDelayMs = Math.max(1200, maxAt + CLEARING_ANIMATION.microTtlMs + 350)

    deps.scheduleTimeout(() => {
      if (runId !== clearingRunSeq) return
      if (done) {
        deps.applyPatches(done)
        runRealLikeClearingDoneFx(plan, done, runId)
      }
    }, doneDelayMs)
  }

  function demoStepOnce(scene: SceneId, txEvents: TxUpdatedEvent[], clearingPlan: ClearingPlanEvent | null, clearingDone: ClearingDoneEvent | null) {
    stopPlaylistPlayback()
    deps.resetOverlays()

    if (scene === 'D') {
      const n = Math.max(1, txEvents.length)
      const i = ((playlist.txIndex % n) + n) % n
      const evt = txEvents[i]
      if (!evt) return
      playlist.txIndex = (i + 1) % n
      runTxEvent(evt)
      return
    }

    if (scene === 'E') {
      const plan = clearingPlan
      if (!plan || plan.steps.length === 0) return
      const n = plan.steps.length
      const i = ((playlist.clearingStepIndex % n) + n) % n
      playlist.clearingStepIndex = (i + 1) % n
      runClearingStep(i, plan, clearingDone)
    }
  }

  function demoTogglePlay(scene: SceneId, txEvents: TxUpdatedEvent[], clearingPlan: ClearingPlanEvent | null, clearingDone: ClearingDoneEvent | null) {
    if (playlist.playing) {
      stopPlaylistPlayback()
      return
    }

    deps.clearScheduledTimeouts()
    deps.resetOverlays()
    playlist.playing = true

    const playNext = () => {
      if (!playlist.playing) return

      if (scene === 'D') {
        const n = Math.max(1, txEvents.length)
        const i = ((playlist.txIndex % n) + n) % n
        const evt = txEvents[i]
        if (!evt) {
          playlist.playing = false
          return
        }
        runTxEvent(evt, {
          onFinished: () => {
            if (!playlist.playing) return
            playlist.txIndex = (i + 1) % n
            deps.scheduleTimeout(playNext, 120)
          },
        })
        return
      }

      if (scene === 'E') {
        const plan = clearingPlan
        if (!plan || plan.steps.length === 0) {
          playlist.playing = false
          return
        }
        const n = plan.steps.length
        const i = ((playlist.clearingStepIndex % n) + n) % n
        runClearingStep(i, plan, clearingDone, {
          onFinished: () => {
            if (!playlist.playing) return
            playlist.clearingStepIndex = (i + 1) % n
            deps.scheduleTimeout(playNext, 120)
          },
        })
        return
      }
    }

    playNext()
  }

  function demoReset() {
    resetDemoState()
  }

  return {
    playlist,
    stopPlaylistPlayback,
    resetPlaylistPointers,
    resetDemoState,
    runTxEvent,
    runClearingOnce,
    runClearingStep,
    demoTogglePlay,
    demoStepOnce,
    demoReset,
  }
}
