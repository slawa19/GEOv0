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

  it('runClearingStep spawns highlight pulse and schedules cleanup', () => {
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

    const plan: ClearingPlanEvent = {
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

    player.runClearingStep(0, plan, null)

    expect(deps.spawnEdgePulses).toHaveBeenCalledTimes(1)

    // schedule includes immediate micro tx timers and cleanup
    const hasCleanup = scheduled.some((s) => s.ms > 0)
    expect(hasCleanup).toBe(true)
  })
})
