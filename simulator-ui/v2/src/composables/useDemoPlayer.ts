import { reactive } from 'vue'
import type { ClearingDoneEvent, ClearingPlanEvent, DemoEvent, TxUpdatedEvent } from '../types'
import type { LayoutNodeWithId as BaseLayoutNodeWithId } from '../types/layout'
import { getFxConfig, intensityScale } from '../config/fxConfig'

const CLEARING_ANIMATION = getFxConfig('demo')

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
  resetOverlays: () => void
  fxColorForNode: (id: string, fallback: string) => string
  addActiveEdge: (key: string, ttlMs?: number) => void

  // Timing
  scheduleTimeout: (fn: () => void, ms: number) => number
  clearScheduledTimeouts: () => void

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
    const fxTestMode = deps.isTestMode() && deps.isWebDriver
    const runId = ++txRunSeq

    const budgetScaleRaw = typeof deps.getFxBudgetScale === 'function' ? deps.getFxBudgetScale() : 1
    const budgetScale = Math.max(0.25, Math.min(1, Number.isFinite(budgetScaleRaw) ? budgetScaleRaw : 1))
    const allowBursts = budgetScale >= 0.7

    // Keep a short-lived highlighted edge so the "spark flight" reads as
    // moving along a fading line (same UX idea as clearing highlight edges).
    for (const e of evt.edges) deps.addActiveEdge(deps.keyEdge(e.from, e.to))

    const ttl = Math.max(250, evt.ttl_ms || 1200)
    const k = intensityScale(evt.intensity_key)

    deps.spawnSparks({
      edges: evt.edges,
      nowMs: performance.now(),
      ttlMs: ttl,
      colorCore: deps.txSparkCore,
      colorTrail: deps.txSparkTrail,
      thickness: 1.0 * k * Math.min(1, 0.85 + budgetScale * 0.15),
      kind: 'beam',
      seedPrefix: `tx:${deps.effectiveEq()}`,
      countPerEdge: 1,
      keyEdge: deps.keyEdge,
      seedFn: deps.seedFn,
      isTestMode: fxTestMode,
    })

    if (!fxTestMode && allowBursts && evt.edges.length > 0) {
      const sourceId = evt.edges[0]!.from
      deps.spawnNodeBursts({
        nodeIds: [sourceId],
        nowMs: performance.now(),
        durationMs: 360,
        color: deps.fxColorForNode(sourceId, deps.txSparkTrail),
        kind: 'tx-impact',
        seedPrefix: 'burst-src',
        seedFn: deps.seedFn,
        isTestMode: fxTestMode,
      })
    }

    if (!fxTestMode && allowBursts && evt.edges.length > 0) {
      const lastEdge = evt.edges[evt.edges.length - 1]!
      const targetId = lastEdge.to

      deps.scheduleTimeout(() => {
        if (runId !== txRunSeq) return

        deps.spawnNodeBursts({
          nodeIds: [targetId],
          nowMs: performance.now(),
          durationMs: 520,
          color: deps.fxColorForNode(targetId, deps.txSparkTrail),
          kind: 'tx-impact',
          seedPrefix: 'burst',
          seedFn: deps.seedFn,
          isTestMode: fxTestMode,
        })

        const ln = deps.getLayoutNode(targetId)
        if (ln) {
          deps.pushFloatingLabel({
            nodeId: targetId,
            text: `+125 GC`,
            color: deps.fxColorForNode(targetId, '#22d3ee'),
            ttlMs: 2200,
            offsetYPx: -6,
          })
        }
      }, ttl)
    }

    if (fxTestMode) return

    const burstDurationMs = 520
    const cleanupDelayMs = ttl + burstDurationMs + 50

    deps.scheduleTimeout(() => {
      if (runId !== txRunSeq) return
      deps.applyPatches(evt)
      deps.resetOverlays()
      opts?.onFinished?.()
    }, cleanupDelayMs)
  }

  function runClearingStep(
    stepIndex: number,
    plan: ClearingPlanEvent,
    done: ClearingDoneEvent | null,
    opts?: { onFinished?: () => void },
  ) {
    const step = plan.steps[stepIndex]
    if (!step) return

    const fxTestMode = deps.isTestMode() && deps.isWebDriver
    if (fxTestMode) {
      for (const e of step.highlight_edges ?? []) deps.addActiveEdge(deps.keyEdge(e.from, e.to))
      return
    }

    const runId = ++clearingRunSeq
    const clearingColor = deps.clearingFlashFallback
    const { microTtlMs, microGapMs, labelLifeMs } = CLEARING_ANIMATION

    const formatDemoDebtAmount = (from: string, to: string, seedAtMs: number) => {
      const h = deps.seedFn(`clearing:amt:${deps.effectiveEq()}:${seedAtMs}:${deps.keyEdge(from, to)}`)
      const amt = 10 + (h % 197) * 5
      return `${amt} GC`
    }

    const edges = step.particles_edges ?? []

    const stepIntensityKey = step.intensity_key
    const k = intensityScale(stepIntensityKey)

    const budgetScaleRaw = typeof deps.getFxBudgetScale === 'function' ? deps.getFxBudgetScale() : 1
    const budgetScale = Math.max(0.25, Math.min(1, Number.isFinite(budgetScaleRaw) ? budgetScaleRaw : 1))
    const allowEdgePulses = budgetScale >= 0.65
    const allowSourceBurst = budgetScale >= 0.8
    const allowTargetBurst = budgetScale >= 0.7

    // Build set of particle edge keys to avoid double-animation.
    // Beam sparks already render edge glow, so don't add EdgePulses for same edges.
    const particleEdgeKeys = new Set(edges.map(e => deps.keyEdge(e.from, e.to)))

    if (step.highlight_edges && step.highlight_edges.length > 0) {
      // Filter out edges that will have beam sparks (to avoid double glow)
      const highlightOnly = step.highlight_edges.filter(e => !particleEdgeKeys.has(deps.keyEdge(e.from, e.to)))
      if (highlightOnly.length > 0) {
        // Keep edges "active" longer than the pulse itself so they fade slower.
        for (const e of highlightOnly) deps.addActiveEdge(deps.keyEdge(e.from, e.to), CLEARING_ANIMATION.highlightPulseMs + 900)

        if (allowEdgePulses) {
          const base = intensityCountPerEdge(stepIntensityKey)
          const countPerEdge = base > 1 && budgetScale < 0.85 ? 1 : base
          deps.spawnEdgePulses({
            edges: highlightOnly,
            nowMs: performance.now(),
            durationMs: CLEARING_ANIMATION.highlightPulseMs,
            color: clearingColor,
            thickness: 1.0 * k * Math.min(1, 0.85 + budgetScale * 0.15),
            seedPrefix: `clearing:highlight:${deps.effectiveEq()}:${step.at_ms}`,
            countPerEdge,
            keyEdge: deps.keyEdge,
            seedFn: deps.seedFn,
            isTestMode: false,
          })
        }
      }
    }

    for (let i = 0; i < edges.length; i++) {
      const e = edges[i]!
      const delayMs = i * microGapMs

      deps.scheduleTimeout(() => {
        if (runId !== clearingRunSeq) return

        // Keep micro edges highlighted a bit longer than the particle itself.
        deps.addActiveEdge(deps.keyEdge(e.from, e.to), microTtlMs + 900)

        if (allowSourceBurst) {
          deps.spawnNodeBursts({
            nodeIds: [e.from],
            nowMs: performance.now(),
            durationMs: CLEARING_ANIMATION.sourceBurstMs,
            color: deps.fxColorForNode(e.from, clearingColor),
            kind: 'tx-impact',
            seedPrefix: `clearing:fromGlow:${deps.effectiveEq()}:${step.at_ms}:${i}`,
            seedFn: deps.seedFn,
            isTestMode: false,
          })
        }

        deps.spawnSparks({
          edges: [e],
          nowMs: performance.now(),
          ttlMs: microTtlMs,
          colorCore: '#ffffff',
          colorTrail: clearingColor,
          thickness: 1.1 * k * Math.min(1, 0.85 + budgetScale * 0.15),
          kind: 'beam',
          seedPrefix: `clearing:micro:${deps.effectiveEq()}:${step.at_ms}:${i}`,
          countPerEdge: 1,
          keyEdge: deps.keyEdge,
          seedFn: deps.seedFn,
          isTestMode: fxTestMode,
        })
      }, delayMs)

      deps.scheduleTimeout(() => {
        if (runId !== clearingRunSeq) return

        if (allowTargetBurst) {
          deps.spawnNodeBursts({
            nodeIds: [e.to],
            nowMs: performance.now(),
            durationMs: CLEARING_ANIMATION.targetBurstMs,
            color: deps.fxColorForNode(e.to, clearingColor),
            kind: 'tx-impact',
            seedPrefix: `clearing:impact:${deps.effectiveEq()}:${step.at_ms}:${i}`,
            seedFn: deps.seedFn,
            isTestMode: false,
          })
        }

        deps.pushFloatingLabel({
          nodeId: e.to,
          id: Math.floor(performance.now()) + deps.seedFn(`lbl:${step.at_ms}:${i}:${deps.keyEdge(e.from, e.to)}`),
          text: `${formatDemoDebtAmount(e.from, e.to, step.at_ms)}`,
          color: deps.fxColorForNode(e.to, clearingColor),
          ttlMs: labelLifeMs,
          offsetYPx: -6,
          throttleKey: `clearing:${e.to}`,
          throttleMs: CLEARING_ANIMATION.labelThrottleMs,
        })
      }, delayMs + microTtlMs)
    }

    const isLast = stepIndex >= plan.steps.length - 1
    const targetBurstDurationMs = CLEARING_ANIMATION.targetBurstMs
    const particlesEndsAt = edges.length > 0 ? (edges.length - 1) * microGapMs + microTtlMs + targetBurstDurationMs : 0
    const cleanupDelayMs = Math.max(CLEARING_ANIMATION.highlightPulseMs, particlesEndsAt) + CLEARING_ANIMATION.cleanupPadMs

    deps.scheduleTimeout(() => {
      if (runId !== clearingRunSeq) return
      if (isLast && done) deps.applyPatches(done)
      deps.resetOverlays()
      opts?.onFinished?.()
    }, cleanupDelayMs)
  }

  function runClearingOnce(plan: ClearingPlanEvent, done: ClearingDoneEvent | null) {
    // In Playwright test-mode, apply the first highlight step deterministically (no timers/FX).
    const fxTestMode = deps.isTestMode() && deps.isWebDriver
    if (fxTestMode) {
      const step0 = plan.steps[0]
      for (const e of step0?.highlight_edges ?? []) deps.addActiveEdge(deps.keyEdge(e.from, e.to))
      return
    }

    const runId = ++clearingRunSeq
    const clearingColor = deps.clearingFlashFallback
    const { microTtlMs, microGapMs, labelLifeMs } = CLEARING_ANIMATION

    const formatDemoDebtAmount = (from: string, to: string, atMs: number) => {
      const h = deps.seedFn(`clearing:amt:${deps.effectiveEq()}:${atMs}:${deps.keyEdge(from, to)}`)
      const amt = 10 + (h % 197) * 5
      return `${amt} GC`
    }

    const animateEdge = (e: { from: string; to: string }, delayMs: number, stepAtMs: number, idx: number) => {
      deps.scheduleTimeout(() => {
        if (runId !== clearingRunSeq) return

        deps.addActiveEdge(deps.keyEdge(e.from, e.to), microTtlMs + 900)

        deps.spawnNodeBursts({
          nodeIds: [e.from],
          nowMs: performance.now(),
          durationMs: CLEARING_ANIMATION.sourceBurstMs,
          color: deps.fxColorForNode(e.from, clearingColor),
          kind: 'tx-impact',
          seedPrefix: `clearing:fromGlow:${deps.effectiveEq()}:${stepAtMs}:${idx}`,
          seedFn: deps.seedFn,
          isTestMode: false,
        })

        deps.spawnSparks({
          edges: [e],
          nowMs: performance.now(),
          ttlMs: microTtlMs,
          colorCore: '#ffffff',
          colorTrail: clearingColor,
          thickness: 1.1,
          kind: 'beam',
          seedPrefix: `clearing:micro:${deps.effectiveEq()}:${stepAtMs}:${idx}`,
          countPerEdge: 1,
          keyEdge: deps.keyEdge,
          seedFn: deps.seedFn,
          isTestMode: fxTestMode,
        })
      }, delayMs)

      deps.scheduleTimeout(() => {
        if (runId !== clearingRunSeq) return

        deps.spawnNodeBursts({
          nodeIds: [e.to],
          nowMs: performance.now(),
          durationMs: CLEARING_ANIMATION.targetBurstMs,
          color: deps.fxColorForNode(e.to, clearingColor),
          kind: 'tx-impact',
          seedPrefix: `clearing:impact:${deps.effectiveEq()}:${stepAtMs}:${idx}`,
          seedFn: deps.seedFn,
          isTestMode: false,
        })

        deps.pushFloatingLabel({
          nodeId: e.to,
          id: Math.floor(performance.now()) + deps.seedFn(`lbl:${stepAtMs}:${idx}:${deps.keyEdge(e.from, e.to)}`),
          text: `${formatDemoDebtAmount(e.from, e.to, stepAtMs)}`,
          color: deps.fxColorForNode(e.to, clearingColor),
          ttlMs: labelLifeMs,
          offsetYPx: -6,
          throttleKey: `clearing:${e.to}`,
          throttleMs: CLEARING_ANIMATION.labelThrottleMs,
        })
      }, delayMs + microTtlMs)
    }

    for (const step of plan.steps) {
      deps.scheduleTimeout(() => {
        if (runId !== clearingRunSeq) return

        const stepIntensityKey = step.intensity_key
        const k = intensityScale(stepIntensityKey)

        const particleEdges = step.particles_edges ?? []

        // Build set of particle edge keys to avoid double-animation.
        // Beam sparks already render edge glow, so don't add EdgePulses for same edges.
        const particleEdgeKeys = new Set(particleEdges.map(e => deps.keyEdge(e.from, e.to)))

        if (step.highlight_edges && step.highlight_edges.length > 0) {
          // Filter out edges that will have beam sparks (to avoid double glow)
          const highlightOnly = step.highlight_edges.filter(e => !particleEdgeKeys.has(deps.keyEdge(e.from, e.to)))
          if (highlightOnly.length > 0) {
            deps.spawnEdgePulses({
              edges: highlightOnly,
              nowMs: performance.now(),
              durationMs: CLEARING_ANIMATION.highlightPulseMs,
              color: clearingColor,
              thickness: 1.0 * k,
              seedPrefix: `clearing:highlight:${deps.effectiveEq()}:${step.at_ms}`,
              countPerEdge: intensityCountPerEdge(stepIntensityKey),
              keyEdge: deps.keyEdge,
              seedFn: deps.seedFn,
              isTestMode: false,
            })
          }
        }

        for (let i = 0; i < particleEdges.length; i++) {
          const e = particleEdges[i]!
          const delayMs = i * microGapMs
          // Inline version of animateEdge so we can apply intensity scaling.
          deps.scheduleTimeout(() => {
            if (runId !== clearingRunSeq) return

            deps.spawnNodeBursts({
              nodeIds: [e.from],
              nowMs: performance.now(),
              durationMs: CLEARING_ANIMATION.sourceBurstMs,
              color: deps.fxColorForNode(e.from, clearingColor),
              kind: 'tx-impact',
              seedPrefix: `clearing:fromGlow:${deps.effectiveEq()}:${step.at_ms}:${i}`,
              seedFn: deps.seedFn,
              isTestMode: false,
            })

            deps.spawnSparks({
              edges: [e],
              nowMs: performance.now(),
              ttlMs: microTtlMs,
              colorCore: '#ffffff',
              colorTrail: clearingColor,
              thickness: 1.1 * k,
              kind: 'beam',
              seedPrefix: `clearing:micro:${deps.effectiveEq()}:${step.at_ms}:${i}`,
              countPerEdge: 1,
              keyEdge: deps.keyEdge,
              seedFn: deps.seedFn,
              isTestMode: fxTestMode,
            })
          }, delayMs)

          deps.scheduleTimeout(() => {
            if (runId !== clearingRunSeq) return

            deps.spawnNodeBursts({
              nodeIds: [e.to],
              nowMs: performance.now(),
              durationMs: CLEARING_ANIMATION.targetBurstMs,
              color: deps.fxColorForNode(e.to, clearingColor),
              kind: 'tx-impact',
              seedPrefix: `clearing:impact:${deps.effectiveEq()}:${step.at_ms}:${i}`,
              seedFn: deps.seedFn,
              isTestMode: false,
            })

            deps.pushFloatingLabel({
              nodeId: e.to,
              id: Math.floor(performance.now()) + deps.seedFn(`lbl:${step.at_ms}:${i}:${deps.keyEdge(e.from, e.to)}`),
              text: `${formatDemoDebtAmount(e.from, e.to, step.at_ms)}`,
              color: deps.fxColorForNode(e.to, clearingColor),
              ttlMs: labelLifeMs,
              offsetYPx: -6,
              throttleKey: `clearing:${e.to}`,
              throttleMs: CLEARING_ANIMATION.labelThrottleMs,
            })
          }, delayMs + microTtlMs)
        }
      }, Math.max(0, step.at_ms))
    }

    const targetBurstDurationMs = CLEARING_ANIMATION.targetBurstMs
    let doneAt = 0
    for (const s of plan.steps) {
      const base = Math.max(0, s.at_ms)
      const count = s.particles_edges?.length ?? 0
      const lastBurstEndsAt = count > 0 ? (count - 1) * microGapMs + microTtlMs + targetBurstDurationMs : 0
      doneAt = Math.max(doneAt, base + CLEARING_ANIMATION.highlightPulseMs, base + lastBurstEndsAt)
    }
    const cleanupDelayMs = doneAt + CLEARING_ANIMATION.cleanupPadMs

    deps.scheduleTimeout(() => {
      if (runId !== clearingRunSeq) return
      if (done) deps.applyPatches(done)
      deps.resetOverlays()
    }, cleanupDelayMs)
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
