import { describe, expect, it, vi } from 'vitest'

import { useWindowManager } from './useWindowManager'
import type { WindowInstance } from './types'

function last<T>(a: T[]): T {
  return a[a.length - 1]!
}

function fakeKeyEv(target: EventTarget | null = null): KeyboardEvent {
  return { target } as unknown as KeyboardEvent
}

describe('useWindowManager (MVP)', () => {
  it('open() creates window with policy/group/constraints', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const id = wm.open({
      type: 'interact-panel',
      data: { panel: 'payment', phase: 'x' },
      anchor: null,
    })

    const w = wm.windows.value.find((x) => x.id === id)!
    expect(w.type).toBe('interact-panel')
    expect(w.policy.group).toBe('interact')
    expect(w.policy.singleton).toBe('reuse')
    expect(w.constraints.minWidth).toBeGreaterThan(0)
  })

  it("singleton='reuse': повторный open того же type обновляет data/constraints, не создаёт новый id и поднимает в фокус", () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const id1 = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' } })
    const z1 = wm.windows.value.find((w) => w.id === id1)!.z

    const id2 = wm.open({
      type: 'interact-panel',
      data: { panel: 'trustline', phase: 'x' },
      anchor: { x: 10, y: 20, space: 'host', source: 'test' },
    })

    expect(id2).toBe(id1)

    const w = wm.windows.value.find((x) => x.id === id2)!
    expect(w.data).toEqual({ panel: 'trustline', phase: 'x' })
    expect(w.constraints.preferredWidth).toBe(380)
    expect(w.z).toBeGreaterThan(z1)
    expect(last(wm.windows.value).id).toBe(id2)
    expect(last(wm.windows.value).active).toBe(true)
  })

  it("singleton='reuse': после user-drag повторный open() без смены anchor не сбрасывает rect.left/top", () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const id1 = wm.open({
      type: 'interact-panel',
      data: { panel: 'payment', phase: 'x' },
      anchor: { x: 10, y: 20, space: 'host', source: 'test' },
    })

    // emulate: window exists in DOM and was measured
    wm.updateMeasuredSize(id1, { width: 420, height: 260 })
    wm.reclamp(id1)

    // emulate: user dragged window to a new place (within viewport)
    // NOTE: wm.reclamp() snaps to 8px grid, so keep test values snap-aligned.
    const w1 = wm.windows.value.find((w) => w.id === id1)!
    w1.rect.left = 120
    w1.rect.top = 232

    const id2 = wm.open({
      type: 'interact-panel',
      data: { panel: 'trustline', phase: 'x' },
      // Same anchor value => must NOT reset left/top
      anchor: { x: 10, y: 20, space: 'host', source: 'test' },
    })

    expect(id2).toBe(id1)
    const w2 = wm.windows.value.find((w) => w.id === id2)!
    expect(w2.rect.left).toBe(120)
    expect(w2.rect.top).toBe(232)
  })

  it("closeGroup('interact') закрывает только interact и не трогает inspector", () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const nodeId = wm.open({ type: 'node-card', data: { nodeId: 'n1' } })
    const edgeId = wm.open({ type: 'edge-detail', data: { fromPid: 'a', toPid: 'b' } })
    wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' } })

    wm.closeGroup('interact', 'programmatic')

    const ids = new Set(wm.windows.value.map((w) => w.id))
    expect(ids.has(nodeId)).toBe(true)
    expect(ids.has(edgeId)).toBe(true)
    expect(wm.windows.value.some((w) => w.policy.group === 'interact')).toBe(false)
  })

  it('Audit fix C-1: trustline panel preferredWidth=380 и estimate использует 380', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const id = wm.open({ type: 'interact-panel', data: { panel: 'trustline', phase: 'x' } })
    const w = wm.windows.value.find((x) => x.id === id)!

    expect(w.constraints.preferredWidth).toBe(380)
    expect(w.rect.width).toBe(380)
  })

  it('updateMeasuredSize() + reclamp() держат окно в viewport и синхронизируют rect из measured', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 300, height: 200 })

    const id = wm.open({ type: 'interact-panel', data: { panel: 'trustline', phase: 'x' } })

    wm.updateMeasuredSize(id, { width: 380, height: 260 })
    wm.reclamp(id)
    const w = wm.windows.value.find((x) => x.id === id)!

    // viewport меньше окна → прижимаем к pad (12)
    expect(w.rect.left).toBe(12)
    expect(w.rect.top).toBe(12)

    expect(w.rect.width).toBe(380)
    expect(w.rect.height).toBe(260)
  })

  it('handleEsc(): form guard → не закрывает', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })
    wm.open({ type: 'edge-detail', data: { fromPid: 'a', toPid: 'b' } })

    const consumed = wm.handleEsc(fakeKeyEv({} as EventTarget), {
      isFormLikeTarget: () => true,
      dispatchWindowEsc: () => false,
    })

    expect(consumed).toBe(false)
    expect(wm.windows.value.length).toBe(1)
  })

  it('handleEsc(): nested consume → не закрывает', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })
    const id = wm.open({ type: 'edge-detail', data: { fromPid: 'a', toPid: 'b' } })

    const dispatchWindowEsc = vi.fn(() => true)
    const consumed = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc,
    })

    expect(consumed).toBe(true)
    expect(dispatchWindowEsc).toHaveBeenCalledTimes(1)
    expect(wm.windows.value.some((w) => w.id === id)).toBe(true)
  })

  it("handleEsc(): escBehavior='close' → close(id,'esc')", () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })
    const id = wm.open({ type: 'edge-detail', data: { fromPid: 'a', toPid: 'b' } })

    const consumed = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc: () => false,
    })

    expect(consumed).toBe(true)
    expect(wm.windows.value.some((w) => w.id === id)).toBe(false)
  })

  it("handleEsc(): escBehavior='ignore' → возвращает false и не закрывает", () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })
    const id = wm.open({ type: 'node-card', data: { nodeId: 'n1' } })
    wm.focus(id)

    const consumed = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc: () => false,
    })

    expect(consumed).toBe(false)
    expect(wm.windows.value.some((w) => w.id === id)).toBe(true)
  })

  it("handleEsc(): escBehavior='back-then-close' → onEsc='consumed' не закрывает; onEsc='pass' закрывает", () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const idConsumed = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'back' } })
    const r1 = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc: () => false,
    })
    expect(r1).toBe(true)
    expect(wm.windows.value.some((w) => w.id === idConsumed)).toBe(true)

    // смена фазы → onEsc='pass' → close
    const idPass = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' } })
    expect(idPass).toBe(idConsumed)
    const r2 = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc: () => false,
    })
    expect(r2).toBe(true)
    expect(wm.windows.value.some((w) => w.id === idConsumed)).toBe(false)
  })

  it('policy: closeOnOutsideClick interact-panel=false, edge-detail=true', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })
    const iid = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' } })
    const eid = wm.open({ type: 'edge-detail', data: { fromPid: 'a', toPid: 'b' } })

    const i = wm.windows.value.find((w) => w.id === iid) as WindowInstance
    const e = wm.windows.value.find((w) => w.id === eid) as WindowInstance
    expect(i.policy.closeOnOutsideClick).toBe(false)
    expect(e.policy.closeOnOutsideClick).toBe(true)
  })

  it('Acceptance A1 / R6: 2 окна (interact + inspector) → ESC закрывает interact, затем inspector', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const inspectorId = wm.open({ type: 'edge-detail', data: { fromPid: 'a', toPid: 'b' } })
    const interactId = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' } })

    // ESC #1: topmost = interact-panel
    const r1 = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc: () => false,
    })
    expect(r1).toBe(true)
    expect(wm.windows.value.some((w) => w.id === interactId)).toBe(false)
    expect(wm.windows.value.some((w) => w.id === inspectorId)).toBe(true)

    // ESC #2: now topmost = inspector
    const r2 = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc: () => false,
    })
    expect(r2).toBe(true)
    expect(wm.windows.value.some((w) => w.id === inspectorId)).toBe(false)
    expect(wm.windows.value.length).toBe(0)
  })

  it('handleEsc() при 0 окнах: возвращает false и не бросает', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const consumed = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc: () => false,
    })

    expect(consumed).toBe(false)
    expect(wm.windows.value.length).toBe(0)
  })

  it('close()/focus() с несуществующим id: no-op', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const id = wm.open({ type: 'edge-detail', data: { fromPid: 'a', toPid: 'b' } })
    const before = wm.windows.value.find((w) => w.id === id)!

    wm.close(999, 'action')
    wm.focus(999)

    const after = wm.windows.value.find((w) => w.id === id)!
    expect(wm.windows.value.length).toBe(1)
    expect(after.id).toBe(before.id)
    expect(after.z).toBe(before.z)
  })

  it('setViewport() + reclampAll(): держит все окна внутри новых границ', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 500, height: 400 })

    const edgeId = wm.open({
      type: 'edge-detail',
      anchor: { x: 480, y: 380, space: 'host', source: 'test' },
      data: { fromPid: 'a', toPid: 'b' },
    })
    const interactId = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' } })

    // Ensure sizes are known and exceed the next viewport.
    wm.updateMeasuredSize(edgeId, { width: 420, height: 320 })
    wm.updateMeasuredSize(interactId, { width: 480, height: 420 })

    wm.setViewport({ width: 300, height: 200 })
    wm.reclampAll()

    const edge = wm.windows.value.find((w) => w.id === edgeId)!
    const interact = wm.windows.value.find((w) => w.id === interactId)!

    // viewport меньше окна → прижимаем к pad (12)
    expect(edge.rect.left).toBe(12)
    expect(edge.rect.top).toBe(12)
    expect(interact.rect.left).toBe(12)
    expect(interact.rect.top).toBe(12)
  })
})

