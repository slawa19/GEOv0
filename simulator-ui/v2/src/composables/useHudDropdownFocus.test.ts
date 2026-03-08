import { nextTick, ref } from 'vue'
import { describe, expect, it } from 'vitest'

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
})