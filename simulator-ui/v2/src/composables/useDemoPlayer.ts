import { reactive } from 'vue'
import type { ClearingDoneEvent, ClearingPlanEvent, DemoEvent, TxUpdatedEvent } from '../types'

export type SceneId = 'A' | 'B' | 'C' | 'D' | 'E'

export type LayoutNode = { id: string; __x: number; __y: number }

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
  addActiveEdge: (key: string) => void

  // Timing
  scheduleTimeout: (fn: () => void, ms: number) => number
  clearScheduledTimeouts: () => void

  // Layout access (read-only)
  getLayoutNode: (id: string) => LayoutNode | undefined

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

    if (fxTestMode) {
      for (const e of evt.edges) deps.addActiveEdge(deps.keyEdge(e.from, e.to))
    }

    const ttl = Math.max(250, evt.ttl_ms || 1200)

    deps.spawnSparks({
      edges: evt.edges,
      nowMs: performance.now(),
      ttlMs: ttl,
      colorCore: deps.txSparkCore,
      colorTrail: deps.txSparkTrail,
      thickness: 1.0,
      kind: 'beam',
      seedPrefix: `tx:${deps.effectiveEq()}`,
      countPerEdge: 1,
      keyEdge: deps.keyEdge,
      seedFn: deps.seedFn,
      isTestMode: fxTestMode,
    })

    if (!fxTestMode && evt.edges.length > 0) {
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

    if (!fxTestMode && evt.edges.length > 0) {
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
            text: `${deps.edgeDirCaption()}\n+125 GC`,
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
    const clearingGold = '#fbbf24'
    const microTtlMs = 780
    const microGapMs = 110
    const labelLifeMs = 2200

    const formatDemoDebtAmount = (from: string, to: string, seedAtMs: number) => {
      const h = deps.seedFn(`clearing:amt:${deps.effectiveEq()}:${seedAtMs}:${deps.keyEdge(from, to)}`)
      const amt = 10 + (h % 197) * 5
      return `${amt} GC`
    }

    if (step.highlight_edges && step.highlight_edges.length > 0) {
      deps.spawnEdgePulses({
        edges: step.highlight_edges,
        nowMs: performance.now(),
        durationMs: 650,
        color: clearingGold,
        thickness: 1.0,
        seedPrefix: `clearing:highlight:${deps.effectiveEq()}:${step.at_ms}`,
        countPerEdge: 1,
        keyEdge: deps.keyEdge,
        seedFn: deps.seedFn,
        isTestMode: false,
      })
    }

    const edges = step.particles_edges ?? []
    for (let i = 0; i < edges.length; i++) {
      const e = edges[i]!
      const delayMs = i * microGapMs

      deps.scheduleTimeout(() => {
        if (runId !== clearingRunSeq) return

        deps.spawnNodeBursts({
          nodeIds: [e.from],
          nowMs: performance.now(),
          durationMs: 360,
          color: deps.fxColorForNode(e.from, clearingGold),
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
          colorTrail: clearingGold,
          thickness: 1.1,
          kind: 'beam',
          seedPrefix: `clearing:micro:${deps.effectiveEq()}:${step.at_ms}:${i}`,
          countPerEdge: 1,
          keyEdge: deps.keyEdge,
          seedFn: deps.seedFn,
          isTestMode: deps.isTestMode(),
        })
      }, delayMs)

      deps.scheduleTimeout(() => {
        if (runId !== clearingRunSeq) return

        deps.spawnNodeBursts({
          nodeIds: [e.to],
          nowMs: performance.now(),
          durationMs: 520,
          color: deps.fxColorForNode(e.to, clearingGold),
          kind: 'tx-impact',
          seedPrefix: `clearing:impact:${deps.effectiveEq()}:${step.at_ms}:${i}`,
          seedFn: deps.seedFn,
          isTestMode: false,
        })

        deps.pushFloatingLabel({
          nodeId: e.to,
          id: Math.floor(performance.now()) + deps.seedFn(`lbl:${step.at_ms}:${i}:${deps.keyEdge(e.from, e.to)}`),
          text: `${deps.edgeDirCaption()}\n${formatDemoDebtAmount(e.from, e.to, step.at_ms)}`,
          color: deps.fxColorForNode(e.to, clearingGold),
          ttlMs: labelLifeMs,
          offsetYPx: -6,
          throttleKey: `clearing:${e.to}`,
          throttleMs: 80,
        })
      }, delayMs + microTtlMs)
    }

    const isLast = stepIndex >= plan.steps.length - 1
    const targetBurstDurationMs = 520
    const particlesEndsAt = edges.length > 0 ? (edges.length - 1) * microGapMs + microTtlMs + targetBurstDurationMs : 0
    const cleanupDelayMs = Math.max(650, particlesEndsAt) + 50

    deps.scheduleTimeout(() => {
      if (runId !== clearingRunSeq) return
      if (isLast && done) deps.applyPatches(done)
      deps.resetOverlays()
      opts?.onFinished?.()
    }, cleanupDelayMs)
  }

  function runClearingOnce(plan: ClearingPlanEvent, done: ClearingDoneEvent | null) {
    // In test-mode, apply the first highlight step deterministically (no timers/FX).
    if (deps.isTestMode()) {
      const step0 = plan.steps[0]
      for (const e of step0?.highlight_edges ?? []) deps.addActiveEdge(deps.keyEdge(e.from, e.to))
      return
    }

    const runId = ++clearingRunSeq
    const clearingGold = '#fbbf24'
    const microTtlMs = 780
    const microGapMs = 110
    const labelLifeMs = 2200

    const formatDemoDebtAmount = (from: string, to: string, atMs: number) => {
      const h = deps.seedFn(`clearing:amt:${deps.effectiveEq()}:${atMs}:${deps.keyEdge(from, to)}`)
      const amt = 10 + (h % 197) * 5
      return `${amt} GC`
    }

    const animateEdge = (e: { from: string; to: string }, delayMs: number, stepAtMs: number, idx: number) => {
      deps.scheduleTimeout(() => {
        if (runId !== clearingRunSeq) return

        deps.spawnNodeBursts({
          nodeIds: [e.from],
          nowMs: performance.now(),
          durationMs: 360,
          color: deps.fxColorForNode(e.from, clearingGold),
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
          colorTrail: clearingGold,
          thickness: 1.1,
          kind: 'beam',
          seedPrefix: `clearing:micro:${deps.effectiveEq()}:${stepAtMs}:${idx}`,
          countPerEdge: 1,
          keyEdge: deps.keyEdge,
          seedFn: deps.seedFn,
          isTestMode: deps.isTestMode(),
        })
      }, delayMs)

      deps.scheduleTimeout(() => {
        if (runId !== clearingRunSeq) return

        deps.spawnNodeBursts({
          nodeIds: [e.to],
          nowMs: performance.now(),
          durationMs: 520,
          color: deps.fxColorForNode(e.to, clearingGold),
          kind: 'tx-impact',
          seedPrefix: `clearing:impact:${deps.effectiveEq()}:${stepAtMs}:${idx}`,
          seedFn: deps.seedFn,
          isTestMode: false,
        })

        deps.pushFloatingLabel({
          nodeId: e.to,
          id: Math.floor(performance.now()) + deps.seedFn(`lbl:${stepAtMs}:${idx}:${deps.keyEdge(e.from, e.to)}`),
          text: `${deps.edgeDirCaption()}\n${formatDemoDebtAmount(e.from, e.to, stepAtMs)}`,
          color: deps.fxColorForNode(e.to, clearingGold),
          ttlMs: labelLifeMs,
          offsetYPx: -6,
          throttleKey: `clearing:${e.to}`,
          throttleMs: 80,
        })
      }, delayMs + microTtlMs)
    }

    for (const step of plan.steps) {
      deps.scheduleTimeout(() => {
        if (runId !== clearingRunSeq) return

        if (step.highlight_edges && step.highlight_edges.length > 0) {
          deps.spawnEdgePulses({
            edges: step.highlight_edges,
            nowMs: performance.now(),
            durationMs: 650,
            color: clearingGold,
            thickness: 1.0,
            seedPrefix: `clearing:highlight:${deps.effectiveEq()}:${step.at_ms}`,
            countPerEdge: 1,
            keyEdge: deps.keyEdge,
            seedFn: deps.seedFn,
            isTestMode: false,
          })
        }

        if (step.particles_edges) {
          const edges = step.particles_edges
          for (let i = 0; i < edges.length; i++) {
            animateEdge(edges[i]!, i * microGapMs, step.at_ms, i)
          }
        }
      }, Math.max(0, step.at_ms))
    }

    const targetBurstDurationMs = 520
    let doneAt = 0
    for (const s of plan.steps) {
      const base = Math.max(0, s.at_ms)
      const count = s.particles_edges?.length ?? 0
      const lastBurstEndsAt = count > 0 ? (count - 1) * microGapMs + microTtlMs + targetBurstDurationMs : 0
      doneAt = Math.max(doneAt, base + 650, base + lastBurstEndsAt)
    }
    const cleanupDelayMs = doneAt + 50

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
