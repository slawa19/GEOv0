import { createApp, h, nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import WindowShell from './WindowShell.vue'
import type { WindowInstance } from '../composables/windowManager/types'

function makeInstance(overrides?: Partial<WindowInstance>): WindowInstance {
  return {
    id: 1,
    type: 'interact-panel',
    data: { panel: 'payment', phase: 'confirm-payment' },
    rect: { left: 100, top: 80, width: 400, height: 300 },
    z: 10,
    effectiveZ: 10,
    active: true,
    policy: {
      group: 'interact',
      singleton: 'reuse',
      escBehavior: 'close',
      closeOnOutsideClick: false,
    },
    constraints: { minWidth: 320, minHeight: 200, preferredWidth: 420, preferredHeight: 280 },
    anchor: null,
    placement: 'docked-right',
    measured: null,
    ...overrides,
  } as WindowInstance
}

function mountShell(
  props: Record<string, unknown>,
  slotHtml?: string,
): { host: HTMLDivElement; app: ReturnType<typeof createApp> } {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const app = createApp({
    render: () =>
      h(WindowShell as any, props, slotHtml ? { default: () => h('div', { class: 'test-content' }, slotHtml) } : undefined),
  })

  app.mount(host)
  return { host, app }
}

function cleanup(host: HTMLDivElement, app: ReturnType<typeof createApp>) {
  app.unmount()
  host.remove()
}

describe('WindowShell', () => {
  describe('frameless mode', () => {
    it('does not render ws-header or ws-close button', async () => {
      const { host, app } = mountShell({
        instance: makeInstance(),
        title: 'Test Window',
        frameless: true,
        showHeader: true,
      })
      await nextTick()

      expect(host.querySelector('.ws-header')).toBeFalsy()
      expect(host.querySelector('button.ws-close')).toBeFalsy()

      cleanup(host, app)
    })

    it('does not apply ws-shell--framed class', async () => {
      const { host, app } = mountShell({
        instance: makeInstance(),
        frameless: true,
      })
      await nextTick()

      const shell = host.querySelector('[data-win-id]') as HTMLElement
      expect(shell).toBeTruthy()
      expect(shell.classList.contains('ws-shell')).toBe(true)
      expect(shell.classList.contains('ws-shell--framed')).toBe(false)

      cleanup(host, app)
    })

    it('inline style has left/top/zIndex but no width/height', async () => {
      const inst = makeInstance({ rect: { left: 50, top: 120, width: 400, height: 300 } })
      const { host, app } = mountShell({ instance: inst, frameless: true })
      await nextTick()

      const shell = host.querySelector('[data-win-id]') as HTMLElement
      const style = shell.style
      expect(style.left).toBe('50px')
      expect(style.top).toBe('120px')
      expect(style.zIndex).toBe(String(inst.effectiveZ))
      expect(style.width).toBe('')
      expect(style.height).toBe('')

      cleanup(host, app)
    })

    it('renders slot content inside ws-body', async () => {
      const { host, app } = mountShell(
        { instance: makeInstance(), frameless: true },
        'Hello',
      )
      await nextTick()

      const body = host.querySelector('.ws-body') as HTMLElement
      expect(body).toBeTruthy()
      expect(body.querySelector('.test-content')?.textContent).toBe('Hello')

      cleanup(host, app)
    })

    it('does not apply overflow:hidden (allows legacy clip-path/scanline effects)', async () => {
      const { host, app } = mountShell({ instance: makeInstance(), frameless: true })
      await nextTick()

      const shell = host.querySelector('[data-win-id]') as HTMLElement
      const computed = window.getComputedStyle(shell)
      // frameless shell must NOT clip — overflow:hidden is only for framed mode
      expect(computed.overflow).not.toBe('hidden')

      cleanup(host, app)
    })
  })

  describe('defaults', () => {
    it('defaults to framed mode (frameless=false) for forward-safety', async () => {
      const { host, app } = mountShell({
        instance: makeInstance(),
        // no frameless prop — should default to false
      })
      await nextTick()

      const shell = host.querySelector('[data-win-id]') as HTMLElement
      expect(shell.classList.contains('ws-shell--framed')).toBe(true)

      cleanup(host, app)
    })
  })

  describe('framed mode', () => {
    it('renders ws-header and ws-close when showHeader=true', async () => {
      const { host, app } = mountShell({
        instance: makeInstance(),
        title: 'Framed',
        frameless: false,
        showHeader: true,
      })
      await nextTick()

      expect(host.querySelector('.ws-header')).toBeTruthy()
      expect(host.querySelector('button.ws-close')).toBeTruthy()
      expect(host.querySelector('.ws-title')?.textContent).toBe('Framed')

      cleanup(host, app)
    })

    it('applies ws-shell--framed class', async () => {
      const { host, app } = mountShell({
        instance: makeInstance(),
        frameless: false,
      })
      await nextTick()

      const shell = host.querySelector('[data-win-id]') as HTMLElement
      expect(shell.classList.contains('ws-shell')).toBe(true)
      expect(shell.classList.contains('ws-shell--framed')).toBe(true)

      cleanup(host, app)
    })

    it('inline style includes width/height from rect', async () => {
      const inst = makeInstance({ rect: { left: 50, top: 120, width: 400, height: 300 } })
      const { host, app } = mountShell({ instance: inst, frameless: false })
      await nextTick()

      const shell = host.querySelector('[data-win-id]') as HTMLElement
      expect(shell.style.width).toBe('400px')
      expect(shell.style.height).toBe('300px')

      cleanup(host, app)
    })
  })

  describe('a11y dialog', () => {
    it('sets role=dialog and aria-label (from title fallback)', async () => {
      const { host, app } = mountShell({
        instance: makeInstance(),
        title: 'My Window',
        frameless: false,
      })
      await nextTick()

      const shell = host.querySelector('[data-win-id]') as HTMLElement
      expect(shell.getAttribute('role')).toBe('dialog')
      expect(shell.getAttribute('aria-label')).toBe('My Window')

      cleanup(host, app)
    })
  })

  describe('PERF-2: ResizeObserver coalesce', () => {
    it('coalesces a burst of RO callbacks into <= 1 measured emit / 16ms and keeps trailing size', async () => {
      vi.useFakeTimers()

      const updateMeasured = vi.fn()
      const reclamp = vi.fn()
      const onMeasured = vi.fn((s: { width: number; height: number }) => {
        updateMeasured(s)
        reclamp()
      })
      let roCb: ((entries: any[]) => void) | null = null

      const prevRO = (globalThis as any).ResizeObserver
      ;(globalThis as any).ResizeObserver = class ResizeObserverMock {
        private cb: (entries: any[]) => void
        constructor(cb: (entries: any[]) => void) {
          this.cb = cb
          roCb = cb
        }
        observe() {}
        disconnect() {}
        unobserve() {}
      }

      try {
        const { host, app } = mountShell({
          instance: makeInstance(),
          onMeasured,
        })
        await nextTick()

        expect(typeof roCb).toBe('function')

        // 10 quick callbacks in the same macrotask: should collapse to 1 emit after 16ms.
        for (let i = 0; i < 10; i += 1) {
          roCb!([
            {
              borderBoxSize: [{ inlineSize: 100 + i, blockSize: 200 + i }],
            },
          ])
        }

        expect(onMeasured).toHaveBeenCalledTimes(0)
        expect(reclamp).toHaveBeenCalledTimes(0)

        vi.advanceTimersByTime(15)
        expect(onMeasured).toHaveBeenCalledTimes(0)
        expect(reclamp).toHaveBeenCalledTimes(0)

        vi.advanceTimersByTime(1)
        expect(onMeasured).toHaveBeenCalledTimes(1)
        expect(updateMeasured).toHaveBeenCalledTimes(1)
        expect(reclamp).toHaveBeenCalledTimes(1)

        // Trailing size must win.
        expect(updateMeasured).toHaveBeenLastCalledWith({ width: 109, height: 209 })

        cleanup(host, app)
      } finally {
        ;(globalThis as any).ResizeObserver = prevRO
        vi.useRealTimers()
      }
    })
  })
})
