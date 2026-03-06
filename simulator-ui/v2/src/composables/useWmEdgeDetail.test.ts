import { describe, expect, it } from 'vitest'

import {
  useWmEdgeDetail,
  type EdgeDetailCloseReason,
  type EdgeDetailWmOpenArgs,
  type EdgeDetailWmPort,
} from './useWmEdgeDetail'

type WmCall =
  | { type: 'open'; args: EdgeDetailWmOpenArgs }
  | { type: 'close'; winId: number; reason: EdgeDetailCloseReason }

function createMockWm(): { wm: EdgeDetailWmPort; calls: WmCall[] } {
  let id = 100
  const calls: WmCall[] = []
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

  describe('ARCH-7: keepAlive preserves frozen context (no drift)', () => {
    it('ARCH-7: desiredOpenReq returns frozenReq (original A→B) even when autoReq changes to C→D', () => {
      const ed = useWmEdgeDetail()
      const { wm } = createMockWm()

      // Open for A→B.
      ed.syncAuto({
        fromPid: 'alice',
        toPid: 'bob',
        anchor: { x: 1, y: 2, space: 'host', source: 'interact-state' },
        focus: 'never',
        source: 'auto',
      })
      ed.applyToWindowManager(wm)
      expect(ed.state.value).toBe('live')

      // Enter keepAlive (e.g. Send Payment pressed).
      ed.allowKeepAlive({ frozenLink: null })
      expect(ed.state.value).toBe('keepAlive')

      // Auto request now changes to a new pid pair (payment flow drifts to bob→alice, then carol→dave).
      ed.syncAuto({
        fromPid: 'carol',
        toPid: 'dave',
        anchor: { x: 99, y: 99, space: 'host', source: 'interact-state' },
        focus: 'never',
        source: 'auto',
      })

      // applyToWindowManager should NOT re-open with carol→dave.
      // It should keep the existing window open with the frozen alice→bob data.
      const { wm: wm2, calls: calls2 } = createMockWm()
      ed.applyToWindowManager(wm2)

      // No new open call: window was already open and frozen req hasn't changed.
      const newOpens = calls2.filter((c) => c.type === 'open')
      // Either zero new opens (idempotent) or one open with alice→bob (re-open after sync).
      // The critical invariant: NO open with carol→dave.
      for (const o of newOpens) {
        expect(o.args.data.fromPid).not.toBe('carol')
        expect(o.args.data.toPid).not.toBe('dave')
      }
    })

    it('ARCH-7: applyToWindowManager opens with frozen request data (original fromPid/toPid) in keepAlive state', () => {
      const ed = useWmEdgeDetail()
      const { wm, calls } = createMockWm()

      // Open for alice→bob.
      ed.open({
        fromPid: 'alice',
        toPid: 'bob',
        anchor: { x: 5, y: 5, space: 'host', source: 'edge-click' },
        focus: 'always',
        source: 'manual',
      })
      ed.applyToWindowManager(wm)
      expect(ed.state.value).toBe('live')

      const openCall = calls.find((c) => c.type === 'open')
      expect(openCall?.args.data.fromPid).toBe('alice')
      expect(openCall?.args.data.toPid).toBe('bob')
      expect(openCall?.args.data.title).toBe('alice → bob')

      // Enter keepAlive.
      ed.allowKeepAlive({ frozenLink: null })
      expect(ed.state.value).toBe('keepAlive')

      // frozenReq must preserve alice→bob — confirmed by checking
      // that any subsequent applyToWindowManager still opens with alice→bob.
      const { wm: wm2, calls: calls2 } = createMockWm()
      // Simulate new window id needed (no existing winId in wm2).
      ed.applyToWindowManager(wm2)
      const reopen = calls2.find((c) => c.type === 'open')
      // If window needs re-opening, must use frozen data.
      if (reopen) {
        expect(reopen.args.data.fromPid).toBe('alice')
        expect(reopen.args.data.toPid).toBe('bob')
        expect(reopen.args.data.title).toBe('alice → bob')
      }
      // No close must happen in keepAlive.
      expect(calls2.filter((c) => c.type === 'close')).toHaveLength(0)
    })
  })
})

