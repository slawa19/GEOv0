import { createApp, h, nextTick, ref } from 'vue'
import { describe, expect, it } from 'vitest'

import OverlaySelect from './OverlaySelect.vue'

async function settle(): Promise<void> {
  await nextTick()
  await nextTick()
}

describe('OverlaySelect', () => {
  it('keeps a hidden native mirror in sync with trigger selection', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const value = ref<string | null>(null)
    const app = createApp({
      render: () => h('div', [
        h(OverlaySelect, {
          id: 'overlay-select-test',
          modelValue: value.value,
          options: [
            { value: 'alice', label: 'Alice' },
            { value: 'bob', label: 'Bob' },
          ],
          triggerLabel: 'Participant',
          'onUpdate:modelValue': (next: string | null) => {
            value.value = next
          },
        }),
      ]),
    })

    app.mount(host)
    await settle()

    const mirror = host.querySelector('#overlay-select-test') as HTMLSelectElement | null
    const trigger = host.querySelector('#overlay-select-test__trigger') as HTMLButtonElement | null
    expect(mirror).toBeTruthy()
    expect(trigger?.textContent).toContain('—')

    trigger?.click()
    await settle()

    const option = document.body.querySelector('[data-option-value="bob"]') as HTMLButtonElement | null
    option?.click()
    await settle()

    expect(value.value).toBe('bob')
    expect(mirror?.value).toBe('bob')
    expect(trigger?.textContent).toContain('Bob')

    app.unmount()
    host.remove()
  })

  it('opens from keyboard, moves focus inside, and restores trigger focus on Escape', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const value = ref<string | null>('alice')
    const app = createApp({
      render: () => h('div', [
        h(OverlaySelect, {
          id: 'overlay-select-focus',
          modelValue: value.value,
          options: [
            { value: 'alice', label: 'Alice' },
            { value: 'bob', label: 'Bob' },
          ],
          triggerLabel: 'Participant focus',
          'onUpdate:modelValue': (next: string | null) => {
            value.value = next
          },
        }),
      ]),
    })

    app.mount(host)
    await settle()

    const trigger = host.querySelector('#overlay-select-focus__trigger') as HTMLButtonElement | null
    expect(trigger).toBeTruthy()

    trigger?.focus()
    trigger?.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true }))
    await settle()

    const selectedOption = document.body.querySelector('[data-dropdown-selected="1"]') as HTMLButtonElement | null
    expect(selectedOption).toBeTruthy()
    expect(document.activeElement).toBe(selectedOption)

    selectedOption?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await settle()

    expect(document.activeElement).toBe(trigger)
    expect(document.body.querySelector('#overlay-select-focus__surface')).toBeNull()

    app.unmount()
    host.remove()
  })

  it('a list opened with the mouse: an open key on the trigger moves the focus inside, Escape closes the list and does not go further (038 S2)', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const escapesSeenAbove: KeyboardEvent[] = []
    const above = (e: KeyboardEvent) => { if (e.key === 'Escape') escapesSeenAbove.push(e) }
    window.addEventListener('keydown', above)

    const app = createApp({
      render: () => h('div', [
        h(OverlaySelect, {
          id: 'overlay-select-mouse',
          modelValue: 'alice',
          options: [{ value: 'alice', label: 'Alice' }, { value: 'bob', label: 'Bob' }],
          triggerLabel: 'Participant mouse',
        }),
      ]),
    })
    app.mount(host)
    await settle()

    try {
      const trigger = host.querySelector('#overlay-select-mouse__trigger') as HTMLButtonElement
      trigger.focus()
      trigger.click() // the mouse: the list opens, the focus stays on the trigger
      await settle()
      expect(document.body.querySelector('#overlay-select-mouse__surface')).toBeTruthy()
      expect(document.activeElement).toBe(trigger)

      trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }))
      await settle()
      expect(document.activeElement).toBe(document.body.querySelector('[data-dropdown-selected="1"]'))

      trigger.focus()
      trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
      await settle()
      expect(document.body.querySelector('#overlay-select-mouse__surface')).toBeNull()
      expect(document.activeElement).toBe(trigger)
      // The window manager listens on `window`: an Escape that reaches it closes the panel around the list.
      expect(escapesSeenAbove).toHaveLength(0)

      // Counter-check: with the list closed, Escape on the trigger is not swallowed - it belongs to the window.
      trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
      expect(escapesSeenAbove).toHaveLength(1)
      expect(escapesSeenAbove[0]!.defaultPrevented).toBe(false)
    } finally {
      window.removeEventListener('keydown', above)
      app.unmount()
      host.remove()
    }
  })

  it('uses labelledBy on the visible trigger and closes on outside pointerdown', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const value = ref<string | null>('alice')
    const app = createApp({
      render: () => h('div', [
        h('label', { id: 'overlay-select-label', for: 'overlay-select-global__trigger' }, 'Participant label'),
        h(OverlaySelect, {
          id: 'overlay-select-global',
          modelValue: value.value,
          options: [
            { value: 'alice', label: 'Alice' },
            { value: 'bob', label: 'Bob' },
          ],
          labelledBy: 'overlay-select-label',
          triggerLabel: 'Participant global',
          'onUpdate:modelValue': (next: string | null) => {
            value.value = next
          },
        }),
      ]),
    })

    app.mount(host)
    await settle()

    const trigger = host.querySelector('#overlay-select-global__trigger') as HTMLButtonElement | null
    expect(trigger?.getAttribute('aria-labelledby')).toBe('overlay-select-label')
    expect(trigger?.getAttribute('aria-label')).toBeNull()

    trigger?.click()
    await settle()

    expect(document.body.querySelector('#overlay-select-global__surface')).toBeTruthy()

    document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
    await settle()

    expect(document.body.querySelector('#overlay-select-global__surface')).toBeNull()

    app.unmount()
    host.remove()
  })
})