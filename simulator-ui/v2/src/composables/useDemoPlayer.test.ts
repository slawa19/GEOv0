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

    // schedule: destination burst at ttl, cleanup at ttl+520+50
    expect(scheduled.some((s) => s.ms === 1200)).toBe(true)
    expect(scheduled.some((s) => s.ms === 1200 + 520 + 50)).toBe(true)

    const cleanup = scheduled.find((s) => s.ms === 1200 + 520 + 50)
    expect(cleanup).toBeTruthy()

    cleanup!.fn()
    expect(deps.applyPatches).toHaveBeenCalledTimes(1)
    expect(deps.resetOverlays).toHaveBeenCalledTimes(1)
  })

  it('runTxEvent applies intensity_key to spark thickness', () => {
    const deps = {
      applyPatches: vi.fn(),
      spawnSparks: vi.fn(),
      spawnNodeBursts: vi.fn(),
      spawnEdgePulses: vi.fn(),
      pushFloatingLabel: vi.fn(),
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

  it('runClearingStep skips edge pulses for edges with beam sparks (avoid double glow)', () => {
    const scheduled: Array<{ ms: number; fn: () => void }> = []

    const deps = {
      applyPatches: vi.fn(),
      spawnSparks: vi.fn(),
      spawnNodeBursts: vi.fn(),
      spawnEdgePulses: vi.fn(),
      pushFloatingLabel: vi.fn(),
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

    // When highlight_edges and particles_edges have the same edges,
    // spawnEdgePulses should NOT be called (beam sparks already render edge glow)
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

    // Should NOT spawn edge pulses for edges that have beam sparks
    expect(deps.spawnEdgePulses).toHaveBeenCalledTimes(0)

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

    // When highlight_edges has edges NOT in particles_edges,
    // spawnEdgePulses should be called for those edges only
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
          particles_edges: [{ from: 'A', to: 'B' }],  // only A→B has beam spark
        },
      ],
    }

    player.runClearingStep(0, planDifferentEdges, null)

    // Should spawn edge pulse only for C→D (which has no beam spark)
    expect(deps.spawnEdgePulses).toHaveBeenCalledTimes(1)
    expect(deps.spawnEdgePulses).toHaveBeenCalledWith(
      expect.objectContaining({
        edges: [{ from: 'C', to: 'D' }],
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
        countPerEdge: 2,
      }),
    )
  })
})
