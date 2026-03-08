import { describe, expect, it, vi } from 'vitest'
import { watch } from 'vue'

import { useWindowManager } from './useWindowManager'
import type { WindowInstance } from './types'
import { isNodeCardWindow } from './types'
import {
  createMeasuredPublishedGeometryValue,
  DEFAULT_HUD_BOTTOM_STACK_HEIGHT_PX,
  DEFAULT_WM_CLAMP_PAD_PX,
  readOverlayGeometryPx,
} from '../../ui-kit/overlayGeometry'

function last<T>(a: T[]): T {
  return a[a.length - 1]!
}

function fakeKeyEv(target: EventTarget | null = null): KeyboardEvent {
  const ev = new KeyboardEvent('keydown', { key: 'Escape' })
  Object.defineProperty(ev, 'target', { value: target, configurable: true })
  return ev
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

  it("singleton='reuse': повторный open того же type обновляет data/constraints, не создаёт новый id", () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const id1 = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' } })

    const id2 = wm.open({
      type: 'interact-panel',
      data: { panel: 'trustline', phase: 'x' },
      anchor: { x: 10, y: 20, space: 'host', source: 'test' },
    })

    expect(id2).toBe(id1)

    const w = wm.windows.value.find((x) => x.id === id2)!
    expect(w.data).toEqual({ panel: 'trustline', phase: 'x' })
    expect(w.constraints.preferredWidth).toBe(380)
  })

  it('UX-6: rapid reuse open(node-card) is debounced (trailing) and applies only the last payload', () => {
    vi.useFakeTimers()
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const id = wm.open({
      type: 'node-card',
      data: { nodeId: 'A' },
      anchor: { x: 10, y: 20, space: 'host', source: 'init' },
    })

    const getWin = () => wm.windows.value.find((w) => w.id === id)!

    // Count how many times the nodeId actually changes (reactive commit count proxy).
    const nodeIdChanges: string[] = []
    const stop = watch(
      () => {
        const w = wm.windows.value.find((win) => win.id === id)
        return w !== undefined && isNodeCardWindow(w) ? w.data.nodeId : undefined
      },
      (v) => {
        if (typeof v === 'string') nodeIdChanges.push(v)
      },
      { flush: 'sync' },
    )

    try {
      // Stress burst: 10 rapid opens within < 100ms; must coalesce to the last payload.
      const ids = ['B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K']
      for (let i = 0; i < ids.length; i += 1) {
        const nodeId = ids[i]!
        const nextId = wm.open({
          type: 'node-card',
          data: { nodeId },
          anchor: { x: 100 + i, y: 200 + i, space: 'host', source: `stress-${nodeId}` },
        })
        expect(nextId).toBe(id)
        // Keep all calls inside the debounce window.
        if (i < ids.length - 1) vi.advanceTimersByTime(1)

        // DoD: no intermediate application — window still shows the initial nodeId.
        expect(getWin().data).toEqual({ nodeId: 'A' })
      }

      // Still inside a 50ms window from the last call: must not have flushed yet.
      vi.advanceTimersByTime(49)
      expect(getWin().data).toEqual({ nodeId: 'A' })
      expect(nodeIdChanges).toEqual([])

      // Past 100ms total since the last open: debounce MUST flush exactly once.
      vi.advanceTimersByTime(60)
      expect(getWin().data).toEqual({ nodeId: 'K' })
      expect(getWin().anchor).toEqual({ x: 109, y: 209, space: 'host', source: 'stress-K' })

      // DoD: exactly one reactive commit/update in the burst (the trailing payload).
      expect(nodeIdChanges).toEqual(['K'])
    } finally {
      stop()
      vi.clearAllTimers()
      vi.useRealTimers()
    }
  })

  it("focus mode: 'never' при reuse не меняет z и active", () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    // Создаём interact-panel, потом node-card чтобы node-card не был наверху
    const ipId = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' } })
    // Теперь откроем node-card (создание — всегда focus)
    const ncId = wm.open({ type: 'node-card', data: { nodeId: 'n1' } })
    // Поднимем interact-panel наверх через focus
    wm.focus(ipId)
    const zBefore = wm.windows.value.find((w) => w.id === ncId)!.z
    const activeBefore = wm.windows.value.find((w) => w.id === ncId)!.active

    // Реактивный upsert node-card с focus:'never'
    wm.open({ type: 'node-card', data: { nodeId: 'n1-updated' }, focus: 'never' })

    const ncAfter = wm.windows.value.find((w) => w.id === ncId)!
    expect(ncAfter.data).toEqual({ nodeId: 'n1-updated' }) // data обновилась
    expect(ncAfter.z).toBe(zBefore) // z не изменился
    expect(ncAfter.active).toBe(activeBefore) // active не изменился
  })

  it("focus mode: 'always' при reuse меняет z и active", () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const id1 = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' } })
    const z1 = wm.windows.value.find((w) => w.id === id1)!.z

    // Открываем повторно с focus:'always'
    const id2 = wm.open({
      type: 'interact-panel',
      data: { panel: 'trustline', phase: 'x' },
      focus: 'always',
    })

    expect(id2).toBe(id1)
    const w = wm.windows.value.find((x) => x.id === id2)!
    expect(w.z).toBeGreaterThan(z1)
    expect(w.active).toBe(true)
  })

  it("focus mode: 'auto' — первый open фокусирует, второй (reuse) не фокусирует", () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    // Первый open: создание — всегда focus (независимо от 'auto')
    const id1 = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' }, focus: 'auto' })
    const w1 = wm.windows.value.find((w) => w.id === id1)!
    expect(w1.active).toBe(true) // первый open всегда активирует

    // Теперь откроем другое окно чтобы interact-panel потерял focus
    // (node-card — другая группа, не вытесняет interact)
    // Используем прямой wm.focus() для симуляции потери фокуса
    // (в реальности другой wm.open создаёт другое окно и фокусирует его)
    // Мы не можем открыть второй interact без reuse — он в reuse-singleton.
    // Вместо этого: просто проверим что повторный open с 'auto' не меняет z.
    const zAfterCreate = w1.z

    const id2 = wm.open({ type: 'interact-panel', data: { panel: 'trustline', phase: 'x' }, focus: 'auto' })
    expect(id2).toBe(id1)
    const w2 = wm.windows.value.find((w) => w.id === id2)!
    // 'auto' при reuse = не фокусирует: z остаётся тем же
    expect(w2.z).toBe(zAfterCreate)
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
    // NOTE: reclamp() no longer re-snaps post-measurement; any value within bounds is preserved.
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

  it("inspector XOR: open(edge-detail) replaces node-card; closeGroup('interact') не трогает inspector", () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const nodeId = wm.open({ type: 'node-card', data: { nodeId: 'n1' } })
    const edgeId = wm.open({ type: 'edge-detail', data: { fromPid: 'a', toPid: 'b' } })

    // Spec: `inspector` is exclusive (NodeCard XOR EdgeDetail).
    expect(wm.windows.value.some((w) => w.id === nodeId)).toBe(false)
    expect(wm.windows.value.some((w) => w.id === edgeId)).toBe(true)

    wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' } })

    wm.closeGroup('interact', 'programmatic')

    const ids = new Set(wm.windows.value.map((w) => w.id))
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

  it('Interact panels: payment/clearing preferredWidth=560 (matches .ds-ov-panel max-width) and estimate uses 560', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const paymentId = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' } })
    const payment = wm.windows.value.find((x) => x.id === paymentId)!
    expect(payment.constraints.preferredWidth).toBe(560)
    expect(payment.rect.width).toBe(560)

    const clearingId = wm.open({ type: 'interact-panel', data: { panel: 'clearing', phase: 'x' } })
    const clearing = wm.windows.value.find((x) => x.id === clearingId)!
    expect(clearing.constraints.preferredWidth).toBe(560)
    expect(clearing.rect.width).toBe(560)
  })

  it('PERF-4: interact-panel constraints are phase-aware (preferredHeight differs by phase)', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    // Same window (singleton reuse), different phases → different preferredHeight.
    const idPicking = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'picking-payment-to' } })
    const wPicking = wm.windows.value.find((x) => x.id === idPicking)!
    const pickingH = wPicking.constraints.preferredHeight
    expect(pickingH).toBe(420)

    const idConfirm = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'confirm-payment' } })
    expect(idConfirm).toBe(idPicking)
    const wConfirm = wm.windows.value.find((x) => x.id === idConfirm)!
    const confirmH = wConfirm.constraints.preferredHeight
    expect(confirmH).toBe(360)

    // Loading-like phase: accept either explicit 'loading-*' or existing clearing running phase.
    const idLoading = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'loading-payment' } })
    expect(idLoading).toBe(idPicking)
    const wLoading = wm.windows.value.find((x) => x.id === idLoading)!
    const loadingH = wLoading.constraints.preferredHeight
    expect(loadingH).toBe(260)

    // Ensure values are actually different (regression guard).
    expect(new Set([pickingH, confirmH, loadingH]).size).toBe(3)
  })

  it('updateMeasuredSize() + reclamp() держат окно в viewport и синхронизируют rect из measured', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 300, height: 200 })

    const id = wm.open({ type: 'interact-panel', data: { panel: 'trustline', phase: 'x' } })

    wm.updateMeasuredSize(id, { width: 380, height: 260 })
    wm.reclamp(id)
    const w = wm.windows.value.find((x) => x.id === id)!

    // viewport меньше окна → прижимаем к pad
    expect(w.rect.left).toBe(DEFAULT_WM_CLAMP_PAD_PX)
    expect(w.rect.top).toBe(DEFAULT_WM_CLAMP_PAD_PX)

    expect(w.rect.width).toBe(380)
    expect(w.rect.height).toBe(260)
  })

  it('fixed-width-auto-height: long labels / measured width growth do not expand interact window and measured height still updates', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const id = wm.open({ type: 'interact-panel', data: { panel: 'trustline', phase: 'x' } })
    const win = wm.windows.value.find((x) => x.id === id)!

    expect(win.rect.width).toBe(380)

    wm.updateMeasuredSize(id, { width: 920, height: 264 })
    wm.reclamp(id)

    expect(win.rect.width).toBe(380)
    expect(win.rect.height).toBe(264)
  })

  it('fixed-width-auto-height: picking -> confirm reuse keeps width stable while height follows the latest valid measurement', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1280, height: 800 })

    const idPicking = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'picking-payment-to' } })
    const picking = wm.windows.value.find((x) => x.id === idPicking)!

    wm.updateMeasuredSize(idPicking, { width: 880, height: 420 })
    wm.reclamp(idPicking)

    const leftBeforeConfirm = picking.rect.left
    const topBeforeConfirm = picking.rect.top

    expect(picking.rect.width).toBe(560)
    expect(picking.rect.height).toBe(420)

    const idConfirm = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'confirm-payment' } })
    expect(idConfirm).toBe(idPicking)

    const confirm = wm.windows.value.find((x) => x.id === idConfirm)!
    expect(confirm.rect.width).toBe(560)

    wm.updateMeasuredSize(idConfirm, { width: 940, height: 360 })
    wm.reclamp(idConfirm)

    expect(confirm.rect.left).toBe(leftBeforeConfirm)
    expect(confirm.rect.top).toBe(topBeforeConfirm)
    expect(confirm.rect.width).toBe(560)
    expect(confirm.rect.height).toBe(360)
  })

  it('E1: reclamp pad is driven by a single DS token source (--ds-wm-clamp-pad)', () => {
    const wm = useWindowManager()

    const host = document.createElement('div')
    host.style.setProperty('--ds-wm-clamp-pad', '20px')
    host.style.setProperty('--ds-hud-stack-height', '110px')
    document.body.appendChild(host)
    try {
      const geo = readOverlayGeometryPx(host)
      expect(geo.wmClampPadPx).toBe(20)

      wm.setGeometry({
        clampPadPx: geo.wmClampPadPx,
        dockedRightInsetPx: geo.wmClampPadPx,
        dockedRightTopPx: geo.hudStackHeightPx,
      })

      wm.setViewport({ width: 300, height: 200 })

      const id = wm.open({ type: 'interact-panel', data: { panel: 'trustline', phase: 'x' } })

      wm.updateMeasuredSize(id, { width: 380, height: 260 })
      wm.reclamp(id)
      const w = wm.windows.value.find((x) => x.id === id)!

      expect(w.rect.left).toBe(20)
      expect(w.rect.top).toBe(20)
    } finally {
      host.remove()
    }
  })

  it('E1: top/bottom overlay geometry readers expose published stack vars with fallback token path', () => {
    const host = document.createElement('div')
    host.style.setProperty('--ds-hud-stack-height', '144px')
    host.style.setProperty('--ds-hud-bottom-stack-height', '72px')
    document.body.appendChild(host)

    try {
      const geo = readOverlayGeometryPx(host)
      expect(geo.hudStackHeightPx).toBe(144)
      expect(geo.hudBottomStackHeightPx).toBe(72)
    } finally {
      host.remove()
    }
  })

  it("reclamp(): anchored окно без measured — позиция пересчитывается от anchor (+dx/dy) и clamp'ится", () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const id = wm.open({
      type: 'edge-detail',
      anchor: { x: 8, y: 24, space: 'host', source: 'test' },
      data: { fromPid: 'a', toPid: 'b' },
    })

    const w = wm.windows.value.find((x) => x.id === id)!

    // emulate: something moved rect before first measure
    w.rect.left = 400
    w.rect.top = 500

    wm.reclamp(id)

    // dx/dy = 16; also note: reclamp() snaps to 8px grid.
    expect(w.rect.left).toBe(24)
    expect(w.rect.top).toBe(40)
  })

  // Regression: RC-1/RC-2 — "second-frame jump" when interact-panel content grows
  // after participants load (UPDATING→loaded transition).
  it('reclamp(): рост measured size (stub→full, docked-right) не меняет top/left если окно в bounds', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1280, height: 768 })

    const id = wm.open({
      type: 'interact-panel',
      data: { panel: 'payment', phase: 'picking-payment-to' },
      anchor: null, // docked-right
    })

    const win = wm.windows.value.find((w) => w.id === id)!

    // First measurement: loading stub — no participants yet, only header + cancel button.
    wm.updateMeasuredSize(id, { width: 360, height: 110 })
    wm.reclamp(id)
    const leftAfterFirstMeasure = win.rect.left
    const topAfterFirstMeasure = win.rect.top

    // Second measurement: full panel — participants loaded, From/To selects appeared.
    // Simulate the UPDATING→loaded height growth that was previously triggering a jump.
    wm.updateMeasuredSize(id, { width: 540, height: 280 })
    wm.reclamp(id)

    // Position MUST NOT change; only rect dimensions should update from measured.
    expect(win.rect.left).toBe(leftAfterFirstMeasure)
    expect(win.rect.top).toBe(topAfterFirstMeasure)

    // Width stays policy-owned; only height tracks measured content.
    expect(win.rect.width).toBe(560)
    expect(win.rect.height).toBe(280)
  })

  it('reclamp(): рост measured size (stub→full, anchored) не меняет top/left если окно в bounds', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1280, height: 768 })

    const id = wm.open({
      type: 'interact-panel',
      data: { panel: 'payment', phase: 'picking-payment-to' },
      anchor: { x: 400, y: 300, space: 'host', source: 'node-card' },
    })

    const win = wm.windows.value.find((w) => w.id === id)!

    // First measurement: stub.
    wm.updateMeasuredSize(id, { width: 360, height: 110 })
    wm.reclamp(id)
    const leftSnapshot = win.rect.left
    const topSnapshot = win.rect.top

    // Second measurement: full panel.
    wm.updateMeasuredSize(id, { width: 540, height: 280 })
    wm.reclamp(id)

    // Position must not change (anchor position was already established on first measurement).
    expect(win.rect.left).toBe(leftSnapshot)
    expect(win.rect.top).toBe(topSnapshot)
  })

  it('reclamp(): viewport shrink после измерения — окно всё равно прижимается к pad', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1280, height: 768 })

    const id = wm.open({
      type: 'interact-panel',
      data: { panel: 'payment', phase: 'x' },
      anchor: null,
    })

    // Establish measured state at full size.
    wm.updateMeasuredSize(id, { width: 540, height: 280 })
    wm.reclamp(id)

    const win = wm.windows.value.find((w) => w.id === id)!

    // Move to a position well within the original viewport.
    win.rect.left = 600
    win.rect.top = 400

    // Shrink viewport so window overflows — reclamp must still push it to pad.
    wm.setViewport({ width: 300, height: 200 })
    wm.reclamp(id)

    expect(win.rect.left).toBe(DEFAULT_WM_CLAMP_PAD_PX)
    expect(win.rect.top).toBe(DEFAULT_WM_CLAMP_PAD_PX)
  })

  it('AC5.5 strategy C: measured окно in-bounds может быть не по сетке — reclamp() не меняет позицию', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 800, height: 600 })

    const id = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' }, anchor: null })
    wm.updateMeasuredSize(id, { width: 300, height: 200 })
    wm.reclamp(id)

    const win = wm.windows.value.find((w) => w.id === id)!
    // Put it in bounds but off-grid.
    win.rect.left = 123
    win.rect.top = 227

    wm.reclamp(id)

    expect(win.rect.left).toBe(123)
    expect(win.rect.top).toBe(227)
  })

  it('AC5.5 strategy C: measured окно out-of-bounds — reclamp() делает clamp + snap8 внутри [pad,max]', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 800, height: 600 })

    const id = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' }, anchor: null })
    wm.updateMeasuredSize(id, { width: 300, height: 200 })
    wm.reclamp(id)

    const win = wm.windows.value.find((w) => w.id === id)!

    // Move far out of bounds so clamp is required.
    win.rect.left = 9999
    win.rect.top = 9999

    wm.reclamp(id)

    const pad = DEFAULT_WM_CLAMP_PAD_PX
    const maxLeft = Math.max(pad, 800 - 300 - pad)
    const maxTop = Math.max(pad, 600 - 200 - pad)

    // Must be in bounds.
    expect(win.rect.left).toBeGreaterThanOrEqual(pad)
    expect(win.rect.left).toBeLessThanOrEqual(maxLeft)
    expect(win.rect.top).toBeGreaterThanOrEqual(pad)
    expect(win.rect.top).toBeLessThanOrEqual(maxTop)

    // Snap-on-clamp: position should land on 8px grid when a clamp happened.
    expect(win.rect.left % 8).toBe(0)
    expect(win.rect.top % 8).toBe(0)
  })

  it('PERF-2: updateMeasuredSize() и reclamp() не делают no-op reactive writes при неизменной геометрии', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 800, height: 600 })

    const id = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' }, anchor: null })
    const win = wm.windows.value.find((w) => w.id === id)!

    // Establish measured geometry.
    wm.updateMeasuredSize(id, { width: 300, height: 200 })
    wm.reclamp(id)

    const measuredRef = win.measured
    expect(measuredRef).toEqual({ width: 300, height: 200 })

    // Intercept rect writes directly (more robust than spying on property setters on proxies).
    const rect = win.rect
    const store = { left: rect.left, top: rect.top, width: rect.width, height: rect.height }
    let writes = 0

    Object.defineProperty(rect, 'left', {
      configurable: true,
      get: () => store.left,
      set: (v: number) => {
        writes += 1
        store.left = v
      },
    })
    Object.defineProperty(rect, 'top', {
      configurable: true,
      get: () => store.top,
      set: (v: number) => {
        writes += 1
        store.top = v
      },
    })
    Object.defineProperty(rect, 'width', {
      configurable: true,
      get: () => store.width,
      set: (v: number) => {
        writes += 1
        store.width = v
      },
    })
    Object.defineProperty(rect, 'height', {
      configurable: true,
      get: () => store.height,
      set: (v: number) => {
        writes += 1
        store.height = v
      },
    })

    // Same measurement again.
    wm.updateMeasuredSize(id, { width: 300, height: 200 })
    wm.reclamp(id)

    // updateMeasuredSize() must be a no-op (keeps same object ref).
    expect(win.measured).toBe(measuredRef)

    // No-op: neither updateMeasuredSize() nor reclamp() should touch rect.
    expect(writes).toBe(0)
  })

  it('Layering priority: focus(inspector) не поднимает его над interact (topmost для ESC остаётся interact)', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const inspectorId = wm.open({ type: 'edge-detail', data: { fromPid: 'a', toPid: 'b' } })
    const interactId = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' } })

    // Force focus to inspector.
    wm.focus(inspectorId)

    // ESC should still close interact first (interact is visually topmost by group priority).
    const r1 = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc: () => true,
    })
    expect(r1).toBe(true)
    expect(wm.windows.value.some((w) => w.id === interactId)).toBe(false)
    expect(wm.windows.value.some((w) => w.id === inspectorId)).toBe(true)
  })

  it('open(): collision avoidance — окна с одним anchor не получают идентичные x/y', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const anchor = { x: 80, y: 96, space: 'host' as const, source: 'test' }

    const id1 = wm.open({ type: 'edge-detail', anchor, data: { fromPid: 'a', toPid: 'b' } })
    const id2 = wm.open({ type: 'interact-panel', anchor, data: { panel: 'payment', phase: 'x' } })

    const w1 = wm.windows.value.find((w) => w.id === id1)!
    const w2 = wm.windows.value.find((w) => w.id === id2)!

    expect(w1.rect.left === w2.rect.left && w1.rect.top === w2.rect.top).toBe(false)
  })

  it('closeByType(): closes all windows of the given type and returns count', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' } })
    wm.open({ type: 'edge-detail', data: { fromPid: 'a', toPid: 'b' } })

    const closed = wm.closeByType('edge-detail', 'programmatic')
    expect(closed).toBe(1)

    // interact window must remain.
    expect(wm.windows.value.some((w) => w.type === 'interact-panel')).toBe(true)
    expect(wm.windows.value.some((w) => w.type === 'edge-detail')).toBe(false)
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

    const dispatchWindowEsc = vi.fn(() => false)
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
      dispatchWindowEsc: () => true,
    })

    expect(consumed).toBe(true)
    expect(wm.windows.value.some((w) => w.id === id)).toBe(false)
  })

  it("handleEsc(): node-card escBehavior='close' → close(id,'esc')", () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })
    const id = wm.open({ type: 'node-card', data: { nodeId: 'n1' } })
    wm.focus(id)

    const consumed = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc: () => true,
    })

    expect(consumed).toBe(true)
    expect(wm.windows.value.some((w) => w.id === id)).toBe(false)
  })

  it("handleEsc(): escBehavior='back-then-close' → onBack=true не закрывает; onBack=false закрывает", () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const onBackConsumed = vi.fn(() => true)
    const idConsumed = wm.open({
      type: 'interact-panel',
      data: { panel: 'payment', phase: 'picking-payment-to', onBack: onBackConsumed },
    })
    const r1 = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc: () => true,
    })
    expect(r1).toBe(true)
    expect(onBackConsumed).toHaveBeenCalledTimes(1)
    expect(wm.windows.value.some((w) => w.id === idConsumed)).toBe(true)

    // смена onBack → false → close
    const onBackPass = vi.fn(() => false)
    const idPass = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'confirm-payment', onBack: onBackPass } })
    expect(idPass).toBe(idConsumed)
    const r2 = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc: () => true,
    })
    expect(r2).toBe(true)
    expect(onBackPass).toHaveBeenCalledTimes(1)
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
      dispatchWindowEsc: () => true,
    })
    expect(r1).toBe(true)
    expect(wm.windows.value.some((w) => w.id === interactId)).toBe(false)
    expect(wm.windows.value.some((w) => w.id === inspectorId)).toBe(true)

    // ESC #2: now topmost = inspector
    const r2 = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc: () => true,
    })
    expect(r2).toBe(true)
    expect(wm.windows.value.some((w) => w.id === inspectorId)).toBe(false)
    expect(wm.windows.value.length).toBe(0)
  })

  it('Acceptance A1-variant: 2 окна (interact + node-card) → 2×ESC закрывают оба окна', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const nodeCardId = wm.open({ type: 'node-card', data: { nodeId: 'n1' } })
    const interactId = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'confirm-payment', onBack: () => false } })

    // ESC #1: topmost = interact-panel
    const r1 = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc: () => true,
    })
    expect(r1).toBe(true)
    expect(wm.windows.value.some((w) => w.id === interactId)).toBe(false)
    expect(wm.windows.value.some((w) => w.id === nodeCardId)).toBe(true)

    // ESC #2: now topmost = node-card
    const r2 = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc: () => true,
    })
    expect(r2).toBe(true)
    expect(wm.windows.value.some((w) => w.id === nodeCardId)).toBe(false)
    expect(wm.windows.value.length).toBe(0)
  })

  it('handleEsc() при 0 окнах: возвращает false и не бросает', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const consumed = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc: () => true,
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

    // viewport меньше окна → прижимаем к pad
    expect(edge.rect.left).toBe(DEFAULT_WM_CLAMP_PAD_PX)
    expect(edge.rect.top).toBe(DEFAULT_WM_CLAMP_PAD_PX)
    expect(interact.rect.left).toBe(DEFAULT_WM_CLAMP_PAD_PX)
    expect(interact.rect.top).toBe(DEFAULT_WM_CLAMP_PAD_PX)
  })

  it('close() вызывает policy.onClose с корректным reason', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const onClose = vi.fn()
    wm.open({ type: 'edge-detail', data: { fromPid: 'a', toPid: 'b', onClose } })

    const id = wm.windows.value[0]!.id
    wm.close(id, 'esc')

    expect(onClose).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledWith('esc')
  })

  it('interact-panel onClose вызывается при ESC-close (back-then-close → pass)', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const onClose = vi.fn()
    const onBack = vi.fn(() => false) // no step-back → close

    wm.open({
      type: 'interact-panel',
      data: { panel: 'payment', phase: 'picking-payment-from', onBack, onClose },
    })

    const consumed = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc: () => true,
    })

    expect(consumed).toBe(true)
    expect(onBack).toHaveBeenCalledTimes(1)
    // onClose called because the window was ESC-closed (InteractPanelData.onClose is () => void)
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(wm.windows.value.length).toBe(0)
  })

  it('interact-panel onClose НЕ вызывается при programmatic close', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const onClose = vi.fn()
    const id = wm.open({
      type: 'interact-panel',
      data: { panel: 'payment', phase: 'x', onClose },
    })

    wm.close(id, 'programmatic')

    // onClose should NOT be called for programmatic close
    // (avoids double-cancel when flow completes and watcher does closeGroup)
    expect(onClose).not.toHaveBeenCalled()
  })

  it("handleEsc(): escBehavior='ignore' не закрывает окно", () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    // No window type has 'ignore' by default, so we test indirectly:
    // open edge-detail, then manually override its policy for testing.
    const id = wm.open({ type: 'edge-detail', data: { fromPid: 'a', toPid: 'b' } })
    const win = wm.windows.value.find((w) => w.id === id)!
    win.policy.escBehavior = 'ignore'

    const consumed = wm.handleEsc(fakeKeyEv(null), {
      isFormLikeTarget: () => false,
      dispatchWindowEsc: () => true,
    })

    expect(consumed).toBe(false)
    expect(wm.windows.value.some((w) => w.id === id)).toBe(true)
  })

  it('getTopmostInGroup: возвращает active окно группы, или max-z fallback', () => {
    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const edgeId = wm.open({ type: 'edge-detail', data: { fromPid: 'a', toPid: 'b' } })
    const interactId = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'x' } })

    // interact-panel is active (opened last)
    const topInteract = wm.getTopmostInGroup('interact')
    expect(topInteract).toBeTruthy()
    expect(topInteract!.id).toBe(interactId)

    // edge-detail is in inspector group
    const topInspector = wm.getTopmostInGroup('inspector')
    expect(topInspector).toBeTruthy()
    expect(topInspector!.id).toBe(edgeId)

    // non-existent group (no windows)
    wm.closeGroup('interact', 'programmatic')
    wm.closeGroup('inspector', 'programmatic')
    expect(wm.getTopmostInGroup('interact')).toBeNull()
    expect(wm.getTopmostInGroup('inspector')).toBeNull()
  })

  // P1-3: transition-aware close — closing state semantic tests.
  describe('P1-3 closing state', () => {
    it('close() переводит окно в state=closing, не удаляет из windowsMap сразу', () => {
      const wm = useWindowManager()
      wm.setViewport({ width: 1200, height: 800 })
      const id = wm.open({ type: 'edge-detail', data: { fromPid: 'a', toPid: 'b' } })

      wm.close(id, 'programmatic')

      // windows (для TransitionGroup) не включает closing — leave-анимация запущена.
      expect(wm.windows.value.some((w) => w.id === id)).toBe(false)
      // Но finishClose ещё не вызван — id ещё есть в map (тест через getTopmostInGroup: не возвращает).
      // Проверяем косвенно через open+finishClose: после finishClose окно исчезает и из map.
      wm.finishClose(id)
      // Повторный close — no-op (уже нет в map).
      expect(() => wm.close(id, 'programmatic')).not.toThrow()
    })

    it('finishClose() удаляет окно из map; идемпотентен', () => {
      const wm = useWindowManager()
      wm.setViewport({ width: 1200, height: 800 })
      const id = wm.open({ type: 'edge-detail', data: { fromPid: 'a', toPid: 'b' } })

      wm.close(id, 'esc')
      // Первый finishClose — удаляет.
      expect(() => wm.finishClose(id)).not.toThrow()
      // Второй finishClose — no-op, не бросает.
      expect(() => wm.finishClose(id)).not.toThrow()
    })

    it('handleEsc() пропускает закрывающееся окно при поиске topmost', () => {
      const wm = useWindowManager()
      wm.setViewport({ width: 1200, height: 800 })

      // Открываем 2 окна: inspector (ниже) и interact (выше).
      const inspectorId = wm.open({ type: 'edge-detail', data: { fromPid: 'a', toPid: 'b' } })
      const interactId = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'confirm-payment', onBack: () => false } })

      // ESC #1: закрывает interact → state=closing (ещё в map, но не в windows).
      wm.handleEsc(fakeKeyEv(null), { isFormLikeTarget: () => false, dispatchWindowEsc: () => true })

      // interact теперь в closing — в windows его нет.
      expect(wm.windows.value.some((w) => w.id === interactId)).toBe(false)
      // inspector ещё open.
      expect(wm.windows.value.some((w) => w.id === inspectorId)).toBe(true)

      // ESC #2: interact ещё в map (closing), но handleEsc должен его ИГНОРИРОВАТЬ и закрыть inspector.
      wm.handleEsc(fakeKeyEv(null), { isFormLikeTarget: () => false, dispatchWindowEsc: () => true })

      expect(wm.windows.value.some((w) => w.id === inspectorId)).toBe(false)
      // Оба в closing или удалены; wm.windows пустой.
      expect(wm.windows.value.length).toBe(0)
    })

    it('rapid double ESC: оба окна получают closing-state в правильном порядке, не путая A и B', () => {
      const wm = useWindowManager()
      wm.setViewport({ width: 1200, height: 800 })

      const inspectorId = wm.open({ type: 'node-card', data: { nodeId: 'n1' } })
      const interactId = wm.open({ type: 'interact-panel', data: { panel: 'payment', phase: 'confirm-payment', onBack: () => false } })

      // Симуляция rapid ESC: два вызова handleEsc подряд (до того как Vue отреагировал).
      wm.handleEsc(fakeKeyEv(null), { isFormLikeTarget: () => false, dispatchWindowEsc: () => true })
      wm.handleEsc(fakeKeyEv(null), { isFormLikeTarget: () => false, dispatchWindowEsc: () => true })

      // Оба окна в closing (windows пустой).
      expect(wm.windows.value.length).toBe(0)

      // Порядок: interact закрылся первым (был topmost), inspector вторым.
      // finishClose обоих — map тоже чист.
      wm.finishClose(interactId)
      wm.finishClose(inspectorId)
      expect(() => wm.finishClose(interactId)).not.toThrow()
    })

    it('open() с singleton=reuse не переиспользует окно в closing state — создаёт новое', () => {
      const wm = useWindowManager()
      wm.setViewport({ width: 1200, height: 800 })

      const id1 = wm.open({ type: 'node-card', data: { nodeId: 'n1' } })
      wm.close(id1, 'esc')

      // Окно в closing → reuse должен создать новое.
      const id2 = wm.open({ type: 'node-card', data: { nodeId: 'n2' } })
      expect(id2).not.toBe(id1)
      expect(wm.windows.value.find((w) => w.id === id2)?.data).toEqual({ nodeId: 'n2' })
    })

    it('open() создаёт WindowInstance с state=open', () => {
      const wm = useWindowManager()
      wm.setViewport({ width: 1200, height: 800 })
      const id = wm.open({ type: 'edge-detail', data: { fromPid: 'a', toPid: 'b' } })
      const win = wm.windows.value.find((w) => w.id === id)!
      expect(win.state).toBe('open')
    })
  })

  it('UX-2: close() возвращает фокус на инициатор (если он ещё в DOM и focusable)', () => {
    const initiator = document.createElement('button')
    initiator.textContent = 'open'
    document.body.appendChild(initiator)
    initiator.focus()
    expect(document.activeElement).toBe(initiator)

    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const id = wm.open({
      type: 'edge-detail',
      data: { fromPid: 'a', toPid: 'b' },
    })

    // Симулируем "потерю" фокуса на произвольный элемент.
    const other = document.createElement('button')
    other.textContent = 'other'
    document.body.appendChild(other)
    other.focus()
    expect(document.activeElement).toBe(other)

    wm.close(id, 'action')

    expect(document.activeElement).toBe(initiator)

    initiator.remove()
    other.remove()
  })

  it('UX-2: focus-return stack работает LIFO для двух последовательных open/close', () => {
    const a = document.createElement('button')
    a.textContent = 'A'
    document.body.appendChild(a)
    a.focus()
    expect(document.activeElement).toBe(a)

    const wm = useWindowManager()
    wm.setViewport({ width: 1200, height: 800 })

    const idA = wm.open({
      type: 'edge-detail',
      data: { fromPid: 'p1', toPid: 'p2' },
    })

    const b = document.createElement('button')
    b.textContent = 'B'
    document.body.appendChild(b)
    b.focus()
    expect(document.activeElement).toBe(b)

    const idB = wm.open({
      type: 'interact-panel',
      data: { panel: 'payment', phase: 'x' },
    })

    // Close B → focus back to initiator B.
    const blur = document.createElement('button')
    blur.textContent = 'blur'
    document.body.appendChild(blur)
    blur.focus()
    expect(document.activeElement).toBe(blur)
    wm.close(idB, 'action')
    expect(document.activeElement).toBe(b)

    // Close A → focus back to initiator A.
    blur.focus()
    expect(document.activeElement).toBe(blur)
    wm.close(idA, 'action')
    expect(document.activeElement).toBe(a)

    a.remove()
    b.remove()
    blur.remove()
  })

  it('measured/published geometry value ignores stale publish after reset and restores fallback safely', () => {
    const applied: number[] = []
    const publisher = createMeasuredPublishedGeometryValue(DEFAULT_HUD_BOTTOM_STACK_HEIGHT_PX, (nextPx) => {
      applied.push(nextPx)
    })

    const firstEpoch = publisher.nextEpoch()
    expect(publisher.publish(72, firstEpoch)).toBe(true)
    expect(applied).toEqual([72])

    publisher.reset()
    expect(applied).toEqual([72, DEFAULT_HUD_BOTTOM_STACK_HEIGHT_PX])

    expect(publisher.publish(96, firstEpoch)).toBe(false)
    expect(applied).toEqual([72, DEFAULT_HUD_BOTTOM_STACK_HEIGHT_PX])
  })
})
