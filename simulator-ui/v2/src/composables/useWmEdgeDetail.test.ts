import { describe, expect, it } from 'vitest'

import { useWmEdgeDetail, type EdgeDetailWmPort } from './useWmEdgeDetail'

function createMockWm(): { wm: EdgeDetailWmPort; calls: any[] } {
  let id = 100
  const calls: any[] = []
  return {
    calls,
    wm: {
      open: (args) => {
        calls.push({ type: 'open', args })
        return id
      },
      close: (winId, reason) => {
        calls.push({ type: 'close', winId, reason })
      },
    },
  }
}

describe('useWmEdgeDetail (ARCH-6 state machine)', () => {
  it('closed -> live -> closed', () => {
    const ed = useWmEdgeDetail()
    const { wm, calls } = createMockWm()

    ed.syncAuto({
      fromPid: 'A',
      toPid: 'B',
      anchor: { x: 1, y: 2, space: 'host', source: 'interact-state' },
      focus: 'never',
      source: 'auto',
    })
    ed.applyToWindowManager(wm)

    expect(ed.state.value).toBe('live')
    expect(calls.filter((c) => c.type === 'open')).toHaveLength(1)

    ed.syncAuto(null)
    ed.applyToWindowManager(wm)

    expect(ed.state.value).toBe('closed')
    expect(calls.filter((c) => c.type === 'close')).toHaveLength(1)
  })

  it('live -> suppressed -> live (selection key changes)', () => {
    const ed = useWmEdgeDetail()
    const { wm, calls } = createMockWm()

    ed.syncAuto({
      fromPid: 'A',
      toPid: 'B',
      anchor: { x: 10, y: 20, space: 'host', source: 'interact-state' },
      focus: 'never',
      source: 'auto',
    })
    ed.applyToWindowManager(wm)

    ed.close({ suppress: true })
    ed.applyToWindowManager(wm, { closeReason: 'action' })

    expect(ed.state.value).toBe('suppressed')
    expect(calls.filter((c) => c.type === 'close')).toHaveLength(1)

    // Same selection key: must remain suppressed and NOT reopen.
    ed.syncAuto({
      fromPid: 'A',
      toPid: 'B',
      anchor: { x: 10, y: 20, space: 'host', source: 'interact-state' },
      focus: 'never',
      source: 'auto',
    })
    ed.applyToWindowManager(wm)
    expect(calls.filter((c) => c.type === 'open')).toHaveLength(1)

    // Anchor changes: suppression lifts and it reopens.
    ed.syncAuto({
      fromPid: 'A',
      toPid: 'B',
      anchor: { x: 11, y: 21, space: 'host', source: 'interact-state' },
      focus: 'never',
      source: 'auto',
    })
    ed.applyToWindowManager(wm)

    expect(ed.state.value).toBe('live')
    expect(calls.filter((c) => c.type === 'open')).toHaveLength(2)
  })

  it('live -> keepAlive -> closed', () => {
    const ed = useWmEdgeDetail()
    const { wm, calls } = createMockWm()

    ed.syncAuto({
      fromPid: 'A',
      toPid: 'B',
      anchor: { x: 1, y: 2, space: 'host', source: 'interact-state' },
      focus: 'never',
      source: 'auto',
    })
    ed.applyToWindowManager(wm)
    expect(ed.state.value).toBe('live')

    ed.allowKeepAlive({ frozenLink: null })
    expect(ed.state.value).toBe('keepAlive')

    // Leaving auto context must NOT close while keepAlive.
    ed.syncAuto(null)
    ed.applyToWindowManager(wm)
    expect(calls.filter((c) => c.type === 'close')).toHaveLength(0)

    ed.releaseKeepAlive()
    ed.applyToWindowManager(wm)
    expect(ed.state.value).toBe('closed')
    expect(calls.filter((c) => c.type === 'close')).toHaveLength(1)
  })
})

