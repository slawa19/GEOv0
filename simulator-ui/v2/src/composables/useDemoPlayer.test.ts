import { describe, expect, it, vi } from 'vitest'
import { useDemoPlayer } from './useDemoPlayer'
import type { ClearingPlanEvent } from '../types'

describe('useDemoPlayer', () => {
  it('runTxEvent spawns spark and schedules cleanup', () => {
    const scheduled: Array<{ ms: number; fn: () => void }> = []

    const deps = {
      applyPatches: vi.fn(),
      spawnSparks: vi.fn(),
      spawnNodeBursts: vi.fn(),
      spawnEdgePulses: vi.fn(),
      pushFloatingLabel: vi.fn(),
      setFlash: vi.fn(),
      resetOverlays: vi.fn(),
      fxColorForNode: vi.fn((id: string, fallback: string) => fallback),
      addActiveEdge: vi.fn(),
      scheduleTimeout: vi.fn((fn: () => void, ms: number) => {
        scheduled.push({ fn, ms })
        return 1
      }),
      clearScheduledTimeouts: vi.fn(),
      getLayoutNode: vi.fn((id: string) => ({ id, __x: 0, __y: 0 })),
      isTestMode: () => false,
      isWebDriver: false,
      effectiveEq: () => 'UAH',
      keyEdge: (a: string, b: string) => `${a}→${b}`,
      seedFn: (s: string) => s.length,
      edgeDirCaption: () => 'from→to',
      txSparkCore: '#fff',
      txSparkTrail: '#0ff',
      clearingFlashFallback: '#fbbf24',
    } as const

    const player = useDemoPlayer(deps)

    player.runTxEvent({
      event_id: 'e1',
      ts: 't',
      type: 'tx.updated',
      equivalent: 'UAH',
      ttl_ms: 1200,
      edges: [{ from: 'A', to: 'B' }],
    })

    expect(deps.spawnSparks).toHaveBeenCalledTimes(1)

    // Real-like behavior: apply patches immediately (not at end of animation).
    expect(deps.applyPatches).toHaveBeenCalledTimes(1)

    // Real-like behavior: ttl is clamped (1200 -> 900), schedule dst burst at ttl,
    // and a short completion callback shortly after.
    expect(scheduled.some((s) => s.ms === 900)).toBe(true)
    expect(scheduled.some((s) => s.ms === 900 + 60)).toBe(true)

    // Real mode does not reset overlays per tx.
    expect(deps.resetOverlays).toHaveBeenCalledTimes(0)
  })

  it('runTxEvent applies intensity_key to spark thickness', () => {
    const deps = {
      applyPatches: vi.fn(),
      spawnSparks: vi.fn(),
      spawnNodeBursts: vi.fn(),
      spawnEdgePulses: vi.fn(),
      pushFloatingLabel: vi.fn(),
      setFlash: vi.fn(),
      resetOverlays: vi.fn(),
      fxColorForNode: vi.fn((id: string, fallback: string) => fallback),
      addActiveEdge: vi.fn(),
      scheduleTimeout: vi.fn(() => 1),
      clearScheduledTimeouts: vi.fn(),
      getLayoutNode: vi.fn((id: string) => ({ id, __x: 0, __y: 0 })),
      isTestMode: () => false,
      isWebDriver: false,
      effectiveEq: () => 'UAH',
      keyEdge: (a: string, b: string) => `${a}→${b}`,
      seedFn: (s: string) => s.length,
      edgeDirCaption: () => 'from→to',
      txSparkCore: '#fff',
      txSparkTrail: '#0ff',
      clearingFlashFallback: '#fbbf24',
    } as const

    const player = useDemoPlayer(deps)

    player.runTxEvent({
      event_id: 'e2',
      ts: 't',
      type: 'tx.updated',
      equivalent: 'UAH',
      ttl_ms: 1200,
      intensity_key: 'hi',
      edges: [{ from: 'A', to: 'B' }],
    })

    expect(deps.spawnSparks).toHaveBeenCalledTimes(1)
    const call = deps.spawnSparks.mock.calls[0]![0]
    expect(call.thickness).toBeGreaterThan(1.0)
  })

  it('runClearingStep spawns edge pulses for highlight edges (real-like)', () => {
    const scheduled: Array<{ ms: number; fn: () => void }> = []

    const deps = {
      applyPatches: vi.fn(),
      spawnSparks: vi.fn(),
      spawnNodeBursts: vi.fn(),
      spawnEdgePulses: vi.fn(),
      pushFloatingLabel: vi.fn(),
      setFlash: vi.fn(),
      resetOverlays: vi.fn(),
      fxColorForNode: vi.fn((id: string, fallback: string) => fallback),
      addActiveEdge: vi.fn(),
      scheduleTimeout: vi.fn((fn: () => void, ms: number) => {
        scheduled.push({ fn, ms })
        return 1
      }),
      clearScheduledTimeouts: vi.fn(),
      getLayoutNode: vi.fn((id: string) => ({ id, __x: 0, __y: 0 })),
      isTestMode: () => false,
      isWebDriver: false,
      effectiveEq: () => 'UAH',
      keyEdge: (a: string, b: string) => `${a}→${b}`,
      seedFn: (s: string) => s.length,
      edgeDirCaption: () => 'from→to',
      txSparkCore: '#fff',
      txSparkTrail: '#0ff',
      clearingFlashFallback: '#fbbf24',
    } as const

    const player = useDemoPlayer(deps)

    // Even if highlight_edges and particles_edges overlap, real mode highlights the plan edges.
    const planSameEdges: ClearingPlanEvent = {
      event_id: 'p1',
      ts: 't',
      type: 'clearing.plan',
      equivalent: 'UAH',
      plan_id: 'p',
      steps: [
        {
          at_ms: 0,
          highlight_edges: [{ from: 'A', to: 'B' }],
          particles_edges: [{ from: 'A', to: 'B' }],
        },
      ],
    }

    player.runClearingStep(0, planSameEdges, null)

    expect(deps.spawnEdgePulses).toHaveBeenCalledTimes(1)
    expect(deps.spawnEdgePulses).toHaveBeenCalledWith(
      expect.objectContaining({
        edges: [{ from: 'A', to: 'B' }],
        countPerEdge: 1,
      }),
    )

    // schedule includes immediate micro tx timers and cleanup
    const hasCleanup = scheduled.some((s) => s.ms > 0)
    expect(hasCleanup).toBe(true)
  })

  it('runClearingStep spawns edge pulses only for highlight-only edges', () => {
    const scheduled: Array<{ ms: number; fn: () => void }> = []

    const deps = {
      applyPatches: vi.fn(),
      spawnSparks: vi.fn(),
      spawnNodeBursts: vi.fn(),
      spawnEdgePulses: vi.fn(),
      pushFloatingLabel: vi.fn(),
      setFlash: vi.fn(),
      resetOverlays: vi.fn(),
      fxColorForNode: vi.fn((id: string, fallback: string) => fallback),
      addActiveEdge: vi.fn(),
      scheduleTimeout: vi.fn((fn: () => void, ms: number) => {
        scheduled.push({ fn, ms })
        return 1
      }),
      clearScheduledTimeouts: vi.fn(),
      getLayoutNode: vi.fn((id: string) => ({ id, __x: 0, __y: 0 })),
      isTestMode: () => false,
      isWebDriver: false,
      effectiveEq: () => 'UAH',
      keyEdge: (a: string, b: string) => `${a}→${b}`,
      seedFn: (s: string) => s.length,
      edgeDirCaption: () => 'from→to',
      txSparkCore: '#fff',
      txSparkTrail: '#0ff',
      clearingFlashFallback: '#fbbf24',
    } as const

    const player = useDemoPlayer(deps)

    // Real-like behavior: highlight pulses are applied to highlight_edges (regardless of particles).
    const planDifferentEdges: ClearingPlanEvent = {
      event_id: 'p2',
      ts: 't',
      type: 'clearing.plan',
      equivalent: 'UAH',
      plan_id: 'p',
      steps: [
        {
          at_ms: 0,
          highlight_edges: [{ from: 'A', to: 'B' }, { from: 'C', to: 'D' }],
          particles_edges: [{ from: 'A', to: 'B' }],
        },
      ],
    }

    player.runClearingStep(0, planDifferentEdges, null)

    // Should spawn pulses for both highlight edges.
    expect(deps.spawnEdgePulses).toHaveBeenCalledTimes(1)
    expect(deps.spawnEdgePulses).toHaveBeenCalledWith(
      expect.objectContaining({
        edges: [{ from: 'A', to: 'B' }, { from: 'C', to: 'D' }],
      }),
    )
  })

  it('runClearingStep applies intensity_key to highlight edge pulses', () => {
    const deps = {
      applyPatches: vi.fn(),
      spawnSparks: vi.fn(),
      spawnNodeBursts: vi.fn(),
      spawnEdgePulses: vi.fn(),
      pushFloatingLabel: vi.fn(),
      setFlash: vi.fn(),
      resetOverlays: vi.fn(),
      fxColorForNode: vi.fn((id: string, fallback: string) => fallback),
      addActiveEdge: vi.fn(),
      scheduleTimeout: vi.fn(() => 1),
      clearScheduledTimeouts: vi.fn(),
      getLayoutNode: vi.fn((id: string) => ({ id, __x: 0, __y: 0 })),
      isTestMode: () => false,
      isWebDriver: false,
      effectiveEq: () => 'UAH',
      keyEdge: (a: string, b: string) => `${a}→${b}`,
      seedFn: (s: string) => s.length,
      edgeDirCaption: () => 'from→to',
      txSparkCore: '#fff',
      txSparkTrail: '#0ff',
      clearingFlashFallback: '#fbbf24',
    } as const

    const player = useDemoPlayer(deps)

    const plan: ClearingPlanEvent = {
      event_id: 'p3',
      ts: 't',
      type: 'clearing.plan',
      equivalent: 'UAH',
      plan_id: 'p',
      steps: [
        {
          at_ms: 0,
          intensity_key: 'hi',
          highlight_edges: [{ from: 'C', to: 'D' }],
          particles_edges: [{ from: 'A', to: 'B' }],
        },
      ],
    }

    player.runClearingStep(0, plan, null)

    expect(deps.spawnEdgePulses).toHaveBeenCalledTimes(1)
    expect(deps.spawnEdgePulses).toHaveBeenCalledWith(
      expect.objectContaining({
        // Real-like: no intensity-based pulse multiplicity.
        countPerEdge: 1,
      }),
    )
  })
})
