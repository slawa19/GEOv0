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