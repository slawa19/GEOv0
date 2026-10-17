import { nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { useHudDropdownFocus } from './useHudDropdownFocus'

function makeKeydown(target: HTMLElement, key: string, shiftKey = false): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key, shiftKey, bubbles: true, cancelable: true })
  Object.defineProperty(event, 'target', { value: target })
  return event
}

function makeToggle(details: HTMLDetailsElement): Event {
  const event = new Event('toggle')
  Object.defineProperty(event, 'target', { value: details })
  return event
}

function setupHarness() {
  const open = ref(false)
  const api = useHudDropdownFocus(open)

  const details = document.createElement('details')
  const summary = document.createElement('button')
  const surface = document.createElement('div')
  const firstButton = document.createElement('button')
  const secondButton = document.createElement('button')

  summary.type = 'button'
  summary.textContent = 'Open'
  firstButton.type = 'button'
  firstButton.textContent = 'First'
  secondButton.type = 'button'
  secondButton.textContent = 'Second'
  surface.tabIndex = -1
  surface.append(firstButton, secondButton)

  document.body.append(details, summary, surface)

  api.detailsRef.value = details
  api.summaryRef.value = summary
  api.surfaceRef.value = surface

  return { open, api, details, summary, surface, firstButton, secondButton }
}

function cleanupHarness(elements: HTMLElement[]) {
  for (const element of elements) element.remove()
}

describe('useHudDropdownFocus', () => {
  it('moves keyboard-opened focus inside on Space and restores summary focus on Escape', async () => {
    const { open, api, details, summary, firstButton, secondButton } = setupHarness()

    try {
      summary.focus()
      api.onDetailsKeydown(makeKeydown(summary, ' '))

      details.open = true
      api.onToggle(makeToggle(details))
      await nextTick()

      expect(open.value).toBe(true)
      expect(document.activeElement).toBe(firstButton)

      secondButton.focus()
      api.onDetailsKeydown(makeKeydown(secondButton, 'Escape'))
      await nextTick()

      expect(open.value).toBe(false)
      expect(details.open).toBe(false)
      expect(document.activeElement).toBe(summary)
    } finally {
      cleanupHarness([details, summary, api.surfaceRef.value!])
    }
  })

  it('keeps Escape and Tab when the focus has fallen out of the open menu to body, and leaves them alone when the focus is elsewhere (038 S2)', async () => {
    const { open, api, details, summary, surface, firstButton } = setupHarness()
    const elsewhere = document.createElement('button')
    document.body.append(elsewhere)
    // A browser announces every move of the focus with a bubbling `focusin`; the test DOM may not, so it is said here.
    const focus = (el: HTMLElement) => {
      el.focus()
      el.dispatchEvent(new FocusEvent('focusin', { bubbles: true }))
    }
    const key = (k: string) => {
      const event = new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true })
      ;(document.activeElement ?? document.body).dispatchEvent(event)
      return event
    }

    try {
      details.open = true
      api.onToggle(makeToggle(details))
      await nextTick()
      expect(open.value).toBe(true)

      // A focus that sits on a real element outside the menu: the keys are not the menu's.
      focus(elsewhere)
      expect(key('Escape').defaultPrevented).toBe(false)
      expect(open.value).toBe(true)

      // The user went elsewhere and the focus ended on body there (a node card opened from the canvas): still not the
      // menu's keys, although the menu is open and the focus is on body (038 S2, D2).
      elsewhere.blur()
      expect(document.activeElement).toBe(document.body)
      expect(key('Escape').defaultPrevented).toBe(false)
      expect(key('Tab').defaultPrevented).toBe(false)
      expect(open.value).toBe(true)

      // A press outside the menu forgets a focus that was inside it.
      focus(firstButton)
      document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
      firstButton.blur()
      expect(key('Escape').defaultPrevented).toBe(false)

      // The focused control of the menu was disabled under the focus: the browser leaves the focus on body.
      focus(firstButton)
      firstButton.blur()
      expect(document.activeElement).toBe(document.body)
      expect(key('Tab').defaultPrevented).toBe(true)
      expect(document.activeElement).toBe(firstButton)

      firstButton.blur()
      expect(key('Escape').defaultPrevented).toBe(true)
      await nextTick()
      expect(open.value).toBe(false)
      expect(document.activeElement).toBe(summary)

      // Closed: the document listener is gone, Escape on body is nobody's.
      api.onToggle(makeToggle(details))
      summary.blur()
      expect(key('Escape').defaultPrevented).toBe(false)
    } finally {
      cleanupHarness([details, summary, surface, elsewhere])
    }
  })

  it('moves keyboard-opened focus inside on ArrowDown and traps Tab within the dropdown surface', async () => {
    const { open, api, details, summary, firstButton, secondButton } = setupHarness()

    try {
      summary.focus()
      api.onDetailsKeydown(makeKeydown(summary, 'ArrowDown'))

      details.open = true
      api.onToggle(makeToggle(details))
      await nextTick()

      expect(open.value).toBe(true)
      expect(document.activeElement).toBe(firstButton)

      secondButton.focus()
      api.onDetailsKeydown(makeKeydown(secondButton, 'Tab'))
      expect(document.activeElement).toBe(firstButton)

      firstButton.focus()
      api.onDetailsKeydown(makeKeydown(firstButton, 'Tab', true))
      expect(document.activeElement).toBe(secondButton)
    } finally {
      cleanupHarness([details, summary, api.surfaceRef.value!])
    }
  })

  it('043 S3: an open menu that grows past the left edge is shifted in again; closed, it is no longer watched', async () => {
    const { details, api, summary, surface } = setupHarness()
    const observers: Array<{ cb: () => void; observed: Element[]; disconnected: boolean }> = []
    vi.stubGlobal('ResizeObserver', class {
      rec: { cb: () => void; observed: Element[]; disconnected: boolean }
      constructor(cb: () => void) {
        this.rec = { cb, observed: [], disconnected: false }
        observers.push(this.rec)
      }
      observe(el: Element) { this.rec.observed.push(el) }
      disconnect() { this.rec.disconnected = true }
    })
    surface.style.setProperty('--ds-wm-clamp-pad', '14px')
    vi.spyOn(document.documentElement, 'clientWidth', 'get').mockReturnValue(1366)
    let left = 100
    const width = 500
    vi.spyOn(surface, 'getBoundingClientRect').mockImplementation(() => {
      const shift = Number.parseFloat(surface.style.translate || '0') || 0
      return { left: left + shift, right: left + shift + width, top: 0, bottom: 10, width, height: 10, x: left + shift, y: 0, toJSON: () => ({}) } as DOMRect
    })
    try {
      details.open = true
      api.onToggle(makeToggle(details))
      expect(surface.style.translate, 'it fits when it opens: not shifted').toBe('')
      expect(observers.map((o) => o.observed), 'the open menu is watched').toEqual([[surface]])

      left = -20 // the content arrived: the right-anchored menu grew to the left, past the edge
      observers[0]!.cb()
      expect(surface.style.translate, 'shifted in by exactly the overflow to the pad').toBe('34px 0')

      details.open = false
      api.onToggle(makeToggle(details))
      expect(observers[0]!.disconnected, 'a closed menu is not watched').toBe(true)
    } finally {
      vi.unstubAllGlobals()
      vi.restoreAllMocks()
      cleanupHarness([details, summary, surface])
    }
  })
})
