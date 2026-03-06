import { afterEach, describe, expect, it, vi } from 'vitest'

import { createTimerRegistry } from './timerRegistry'

type TimerWindow = Pick<Window, 'setTimeout' | 'clearTimeout'>

function createTimerWindow(): TimerWindow {
  return {
    setTimeout: globalThis.setTimeout.bind(globalThis),
    clearTimeout: globalThis.clearTimeout.bind(globalThis),
  }
}

describe('createTimerRegistry()', () => {
  const prevWindow = globalThis.window

  afterEach(() => {
    vi.useRealTimers()
    globalThis.window = prevWindow
  })

  it('tracks size and removes fired timers', () => {
    vi.useFakeTimers()

    globalThis.window = createTimerWindow() as Window & typeof globalThis

    const reg = createTimerRegistry()

    const calls: string[] = []
    reg.schedule(() => calls.push('a'), 10)

    expect(reg.size()).toBe(1)
    expect(calls).toEqual([])

    vi.advanceTimersByTime(10)

    expect(calls).toEqual(['a'])
    expect(reg.size()).toBe(0)
  })

  it('clearAll({keepCritical:true}) keeps critical timers and clears non-critical', () => {
    vi.useFakeTimers()

    globalThis.window = createTimerWindow() as Window & typeof globalThis

    const reg = createTimerRegistry()

    const calls: string[] = []

    reg.schedule(() => calls.push('non-critical'), 50)
    reg.schedule(() => calls.push('critical'), 50, { critical: true })

    expect(reg.size()).toBe(2)

    reg.clearAll({ keepCritical: true })

    expect(reg.size()).toBe(1)

    vi.advanceTimersByTime(50)

    expect(calls).toEqual(['critical'])
    expect(reg.size()).toBe(0)
  })

  it('clearAll() clears all timers', () => {
    vi.useFakeTimers()

    globalThis.window = createTimerWindow() as Window & typeof globalThis

    const reg = createTimerRegistry()

    const calls: string[] = []

    reg.schedule(() => calls.push('a'), 10)
    reg.schedule(() => calls.push('b'), 20, { critical: true })

    expect(reg.size()).toBe(2)

    reg.clearAll()

    expect(reg.size()).toBe(0)

    vi.advanceTimersByTime(100)
    expect(calls).toEqual([])
  })
})
