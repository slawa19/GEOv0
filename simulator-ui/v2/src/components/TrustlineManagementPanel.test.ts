import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import TrustlineManagementPanel from './TrustlineManagementPanel.vue'

type TrustlinePanelState = {
  phase: string
  fromPid: string | null
  toPid: string | null
  selectedEdgeKey: string | null
  edgeAnchor: { x: number; y: number } | null
  error: string | null
  lastClearing: null
}

const trustlineManagementPanelComponent: Component = TrustlineManagementPanel

function baseState(partial?: Partial<TrustlinePanelState>) {
  // Minimal InteractState shape used by the panel.
  return reactive({
    phase: 'idle',
    fromPid: null as string | null,
    toPid: null as string | null,
    selectedEdgeKey: null as string | null,
    edgeAnchor: null as { x: number; y: number } | null,
    error: null as string | null,
    lastClearing: null,
    ...(partial ?? {}),
  })
}

describe('TrustlineManagementPanel', () => {
  it('TL-1a: createValid accepts 0 (Create enabled for valid from/to + limit=0)', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = baseState({ fromPid: 'alice', toPid: 'bob' })

    const app = createApp({
      render: () =>
        h(trustlineManagementPanelComponent, {
          phase: 'confirm-trustline-create',
          state,
          unit: 'EQ',
          used: '0',
          currentLimit: null,
          available: null,
          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
          ],
          // `F-013-7`: основание для чисел — обязательный проп; здесь источник ответил.
          figuresSource: { kind: 'no-row' } as const,
          trustlines: [],
          busy: false,
          confirmTrustlineCreate: vi.fn(),
          confirmTrustlineUpdate: vi.fn(),
          confirmTrustlineClose: vi.fn(),
          cancel: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    expect(host.textContent ?? '').toContain('ESC to close')

    const input = host.querySelector('#tl-limit') as HTMLInputElement | null
    expect(input).toBeTruthy()

    input!.value = '0'
    input!.dispatchEvent(new Event('input'))
    await nextTick()

    const btn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Create') as HTMLButtonElement | undefined
    expect(btn).toBeTruthy()
    expect(btn!.disabled).toBe(false)

    app.unmount()
    host.remove()
  })

  // INTENTIONAL, 026 `T2602` (owner 2026-09-29): a limit below used was blocked here with a warning; it is now a
  // trust change and is allowed. A negative `available` is shown as the excess over trust, not as an amount.
  it('TL-1: newLimit < used is allowed, and a negative available reads as the excess over trust', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = baseState({ fromPid: 'alice', toPid: 'bob' })

    const app = createApp({
      render: () =>
        h(trustlineManagementPanelComponent, {
          phase: 'editing-trustline',
          state,
          unit: 'EQ',
          used: '10',
          currentLimit: '0',
          available: '-10',
          participants: [],
          // Внешнее ревью 013 (P2): панель ПРАВИТ существующую линию, значит основание для её
          // чисел — `row` (источник ответил, и строка для пары есть). Прежнее `no-row` описывало
          // здесь состояние «линии у этой пары нет» — то есть тест правил несуществующую линию.
          figuresSource: { kind: 'row' } as const,
          trustlines: [],
          busy: false,
          confirmTrustlineCreate: vi.fn(),
          confirmTrustlineUpdate: vi.fn(),
          confirmTrustlineClose: vi.fn(),
          cancel: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    const input = host.querySelector('#tl-new-limit') as HTMLInputElement | null
    expect(input).toBeTruthy()

    input!.value = '5'
    input!.dispatchEvent(new Event('input'))
    await nextTick()

    expect(host.querySelector('[data-testid="tl-limit-too-low"]')).toBeNull()
    expect(host.textContent ?? '').toContain('over limit by 10 EQ')
    expect(host.textContent ?? '').not.toContain('-10')

    const btn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Update') as HTMLButtonElement | undefined
    expect(btn).toBeTruthy()
    expect(btn!.disabled).toBe(false)

    app.unmount()
    host.remove()
  })

  // INTENTIONAL, 026 `T2603.2` (owner В1 2026-09-29): TL-2 and AC-TL-10 asserted that a debt either way disabled
  // Close with "Reduce debt to 0 first". A close with the debt the line supports (`used`) is now a REQUEST (limit
  // 0, the line closes when that debt is repaid) and says so; the reverse debt is the other line's and is silent.
  function mountEdit(used: string, extra: Record<string, unknown> = {}) {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const trustline = { from_pid: 'alice', from_name: 'Alice', to_pid: 'bob', to_name: 'Bob', equivalent: 'EQ',
      limit: '10.00', used, reverse_used: '0.01', available: '9.00', status: 'active', ...extra }
    const app = createApp({
      render: () =>
        h(trustlineManagementPanelComponent, {
          phase: 'editing-trustline', state: baseState({ fromPid: 'alice', toPid: 'bob' }), unit: 'EQ',
          used, currentLimit: '10', available: '9', participants: [], figuresSource: { kind: 'row' } as const,
          trustlines: [trustline], busy: false, confirmTrustlineCreate: vi.fn(), confirmTrustlineUpdate: vi.fn(),
          confirmTrustlineClose: vi.fn(), cancel: vi.fn(),
        }),
    })
    app.mount(host)
    const btn = () => host.querySelector('[data-testid="trustline-close-btn"]') as HTMLButtonElement
    return { host, btn, done: () => { app.unmount(); host.remove() } }
  }

  it('TL-2 (026): a close with the supported debt is allowed and announced as a request', async () => {
    const { host, btn, done } = mountEdit('1.00')
    await nextTick()
    expect(btn().disabled).toBe(false)
    expect(host.querySelector('[data-testid="tl-close-blocked"]')).toBeNull()
    expect(host.querySelector('[data-testid="tl-close-request-note"]')?.textContent ?? '').toContain('used: 1.00 EQ')
    done()
  })

  it('AC-TL-10 (026): the reverse debt neither blocks Close nor is announced', async () => {
    const { host, btn, done } = mountEdit('0.00')
    await nextTick()
    expect(btn().disabled).toBe(false)
    expect(host.querySelector('[data-testid="tl-close-request-note"]')).toBeNull()
    done()
  })

  it('TL-5 (026): a requested close is shown as such and is not requested again', async () => {
    const { host, btn, done } = mountEdit('1.00', { limit: '0.00', close_requested_at: '2026-10-02T08:00:00Z' })
    await nextTick()
    expect(host.querySelector('[data-testid="tl-close-requested"]')?.textContent ?? '').toContain('Close requested')
    expect(btn().disabled).toBe(true)
    done()
  })

  it("TL-1/TL-1a: normalizes amount before sending ('1,5' -> '1.5')", async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = baseState({ fromPid: 'alice', toPid: 'bob' })

    const confirmTrustlineCreate = vi.fn()
    const confirmTrustlineUpdate = vi.fn()

    const ui = reactive({
      phase: 'confirm-trustline-create' as 'confirm-trustline-create' | 'editing-trustline',
      used: '0',
      // Внешнее ревью 013 (P2): основание МЕНЯЕТСЯ вместе с фазой, как оно меняется и в приложении.
      // Пока линии нет — `no-row`, и это ровно то состояние, в котором создание разрешено. После
      // создания линия существует, источник отвечает строкой — `row`, и только тогда её правят.
      figuresSource: { kind: 'no-row' } as { kind: 'no-row' } | { kind: 'row' },
    })

    const app = createApp({
      render: () =>
        h(trustlineManagementPanelComponent, {
          phase: ui.phase,
          state,
          unit: 'EQ',
          used: ui.used,
          currentLimit: '10',
          available: '10',
          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
          ],
          figuresSource: ui.figuresSource,
          trustlines: [],
          busy: false,
          confirmTrustlineCreate,
          confirmTrustlineUpdate,
          confirmTrustlineClose: vi.fn(),
          cancel: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    // Create
    {
      const input = host.querySelector('#tl-limit') as HTMLInputElement
      input.value = '1,5'
      input.dispatchEvent(new Event('input'))
      await nextTick()

      const btn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Create') as HTMLButtonElement
      expect(btn.disabled).toBe(false)
      btn.click()
      expect(confirmTrustlineCreate).toHaveBeenCalledTimes(1)
      expect(confirmTrustlineCreate).toHaveBeenCalledWith('1.5')
    }

    // Switch to edit: линия создана, и теперь источник отвечает по ней строкой.
    ui.phase = 'editing-trustline'
    ui.used = '0'
    ui.figuresSource = { kind: 'row' }
    await nextTick()
    await nextTick()

    {
      const input = host.querySelector('#tl-new-limit') as HTMLInputElement
      input.value = '1,5'
      input.dispatchEvent(new Event('input'))
      await nextTick()

      const btn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Update') as HTMLButtonElement
      expect(btn.disabled).toBe(false)
      btn.click()
      expect(confirmTrustlineUpdate).toHaveBeenCalledTimes(1)
      expect(confirmTrustlineUpdate).toHaveBeenCalledWith('1.5')
    }

    app.unmount()
    host.remove()
  })

  /**
   * `F-013-7`, fail-open умолчание на fail-closed гарде.
   *
   * До починки основание для чисел приезжало двумя НЕОБЯЗАТЕЛЬНЫМИ флагами со значением
   * по умолчанию «источник устоялся»: чтобы гарда не стало, достаточно было их не передать.
   * Именно так был смонтирован legacy-снимок разметки и каждый тест в этом файле.
   *
   * Теперь это один ОБЯЗАТЕЛЬНЫЙ проп, и его отсутствие читается как «оснований нет» — типы ловят
   * такого вызывающего на сборке, а этот тест — в рантайме.
   */
  it('F-013-7: a caller that passes no ground gets a refusal, not a permission', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = baseState({ fromPid: 'alice', toPid: 'bob' })

    const app = createApp({
      render: () =>
        h(trustlineManagementPanelComponent, {
          phase: 'editing-trustline',
          state,
          unit: 'EQ',
          // Числа есть, и долга по ним нет — то есть каскад «долга нет → можно закрывать» сработал бы.
          used: '0',
          currentLimit: '100',
          available: '100',
          participants: [],
          trustlines: [],
          busy: false,
          confirmTrustlineCreate: vi.fn(),
          confirmTrustlineUpdate: vi.fn(),
          confirmTrustlineClose: vi.fn(),
          cancel: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    const closeBtn = host.querySelector('[data-testid="trustline-close-btn"]') as HTMLButtonElement | null
    expect(closeBtn).toBeTruthy()
    expect(
      closeBtn?.disabled,
      'панель без переданного основания разрешает закрытие линии',
    ).toBe(true)
    expect(host.querySelector('[data-testid="tl-source-unavailable"]')).toBeTruthy()

    app.unmount()
    host.remove()
  })

  it('TL-3: marks existing trustlines as (exists) in create-flow To dropdown', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = baseState({ fromPid: 'alice', toPid: null })

    const app = createApp({
      render: () =>
        h(trustlineManagementPanelComponent, {
          phase: 'confirm-trustline-create',
          state,
          unit: 'EQ',
          used: '0',
          currentLimit: null,
          available: null,
          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
            { pid: 'carol', name: 'Carol' },
          ],
          // `F-013-7`: основание для чисел — обязательный проп; здесь источник ответил.
          figuresSource: { kind: 'row' } as const,
          trustlines: [
            {
              from_pid: 'alice',
              from_name: 'Alice',
              to_pid: 'bob',
              to_name: 'Bob',
              equivalent: 'EQ',
              limit: '10.00',
              used: '0.00',
              available: '10.00',
              status: 'active',
            },
          ],
          busy: false,
          confirmTrustlineCreate: vi.fn(),
          confirmTrustlineUpdate: vi.fn(),
          confirmTrustlineClose: vi.fn(),
          cancel: vi.fn(),
          setFromPid: vi.fn(),
          setToPid: vi.fn(),
          selectTrustline: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    const toSelect = host.querySelector('#tl-to') as HTMLSelectElement | null
    expect(toSelect).toBeTruthy()
    expect(toSelect!.disabled).toBe(false)

    const optionsText = Array.from(toSelect!.querySelectorAll('option')).map((o) => (o.textContent ?? '').trim())
    expect(optionsText.some((t) => t.includes('(exists)'))).toBe(true)

    app.unmount()
    host.remove()
  })

  it('AC-TL-7: newLimit="0" with used="0" enables Update and sends "0"', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = baseState({ fromPid: 'alice', toPid: 'bob' })
    const confirmTrustlineUpdate = vi.fn()

    const app = createApp({
      render: () =>
        h(trustlineManagementPanelComponent, {
          phase: 'editing-trustline',
          state,
          unit: 'EQ',
          used: '0',
          currentLimit: '10',
          available: '10',
          participants: [],
          // Внешнее ревью 013 (P2): панель ПРАВИТ существующую линию, значит основание для её
          // чисел — `row` (источник ответил, и строка для пары есть). Прежнее `no-row` описывало
          // здесь состояние «линии у этой пары нет» — то есть тест правил несуществующую линию.
          figuresSource: { kind: 'row' } as const,
          trustlines: [],
          busy: false,
          confirmTrustlineCreate: vi.fn(),
          confirmTrustlineUpdate,
          confirmTrustlineClose: vi.fn(),
          cancel: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    const input = host.querySelector('#tl-new-limit') as HTMLInputElement | null
    expect(input).toBeTruthy()

    input!.value = '0'
    input!.dispatchEvent(new Event('input'))
    await nextTick()

    // Update button should be enabled (0 >= 0 used).
    const btn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Update') as HTMLButtonElement | undefined
    expect(btn).toBeTruthy()
    expect(btn!.disabled).toBe(false)

    // No limit-too-low warning.
    const warn = host.querySelector('[data-testid="tl-limit-too-low"]') as HTMLElement | null
    expect(warn).toBeNull()

    // Click sends normalized "0".
    btn!.click()
    expect(confirmTrustlineUpdate).toHaveBeenCalledTimes(1)
    expect(confirmTrustlineUpdate).toHaveBeenCalledWith('0')

    app.unmount()
    host.remove()
  })

  it('keeps disabled reason on the To trigger when From is not selected', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = baseState({ fromPid: null, toPid: null })

    const app = createApp({
      render: () =>
        h(trustlineManagementPanelComponent, {
          phase: 'confirm-trustline-create',
          state,
          unit: 'EQ',
          used: '0',
          currentLimit: null,
          available: null,
          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
          ],
          // `F-013-7`: основание для чисел — обязательный проп; здесь источник ответил.
          figuresSource: { kind: 'no-row' } as const,
          trustlines: [],
          busy: false,
          confirmTrustlineCreate: vi.fn(),
          confirmTrustlineUpdate: vi.fn(),
          confirmTrustlineClose: vi.fn(),
          cancel: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    const label = host.querySelector('#tl-to-label') as HTMLLabelElement | null
    const trigger = host.querySelector('#tl-to__trigger') as HTMLButtonElement | null

    expect(label?.htmlFor).toBe('tl-to__trigger')
    expect(trigger?.disabled).toBe(true)
    expect(trigger?.title).toBe("Select 'From' participant first")
    expect(trigger?.getAttribute('aria-labelledby')).toBe('tl-to-label')

    app.unmount()
    host.remove()
  })

  it('TL-4: pre-fills newLimit from effectiveLimit (trustlines list) rather than props.currentLimit', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = baseState({ fromPid: 'alice', toPid: 'bob' })

    const app = createApp({
      render: () =>
        h(trustlineManagementPanelComponent, {
          phase: 'editing-trustline',
          state,
          unit: 'EQ',
          // Intentionally conflicting snapshot-like prop.
          currentLimit: '111.00',
          used: '0.00',
          available: '111.00',
          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
          ],
          // `F-013-7`: основание для чисел — обязательный проп; здесь источник ответил.
          figuresSource: { kind: 'row' } as const,
          trustlines: [
            {
              from_pid: 'alice',
              from_name: 'Alice',
              to_pid: 'bob',
              to_name: 'Bob',
              equivalent: 'EQ',
              limit: '10.00',
              used: '0.00',
              available: '10.00',
              status: 'active',
            },
          ],
          busy: false,
          confirmTrustlineCreate: vi.fn(),
          confirmTrustlineUpdate: vi.fn(),
          confirmTrustlineClose: vi.fn(),
          cancel: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    const input = host.querySelector('#tl-new-limit') as HTMLInputElement | null
    expect(input).toBeTruthy()
    expect(input!.value).toBe('10.00')

    app.unmount()
    host.remove()
  })

  it('Batch 2a: long trustline labels keep the consumer on the expected select + suffix-row structure', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = baseState({ fromPid: 'alice', toPid: 'bob' })
    const longName = 'Very long trustline label '.repeat(10).trim()

    const app = createApp({
      render: () =>
        h(trustlineManagementPanelComponent, {
          phase: 'editing-trustline',
          state,
          unit: 'EQ',
          used: '0.00',
          currentLimit: '10.00',
          available: '10.00',
          participants: [
            { pid: 'alice', name: longName },
            { pid: 'bob', name: longName },
          ],
          // `F-013-7`: основание для чисел — обязательный проп; здесь источник ответил.
          figuresSource: { kind: 'row' } as const,
          trustlines: [
            {
              from_pid: 'alice',
              from_name: longName,
              to_pid: 'bob',
              to_name: longName,
              equivalent: 'EQ',
              limit: '10.00',
              used: '0.00',
              available: '10.00',
              status: 'active',
            },
          ],
          busy: false,
          confirmTrustlineCreate: vi.fn(),
          confirmTrustlineUpdate: vi.fn(),
          confirmTrustlineClose: vi.fn(),
          cancel: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    const select = host.querySelector('#tl-pick') as HTMLSelectElement | null
    const inputRow = host.querySelector('.tl-input-row') as HTMLElement | null

    expect(select).toBeTruthy()
    expect(inputRow).toBeTruthy()

      expect(inputRow!.classList.contains('ds-controls__suffix')).toBe(true)
    expect((select!.querySelector('option[value="alice|bob"]')?.textContent ?? '')).toContain('Very long trustline label')

    app.unmount()
    host.remove()
  })

  it('Batch 2b: uses shared compact form primitives and removes local select/input width clamps', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/components/TrustlineManagementPanel.vue'), 'utf8')

    expect(source).toContain('ds-ov-panel ds-ov-panel--compact ds-panel ds-panel--elevated')
    expect(source).toContain('class="ds-controls__row ds-controls__row--compact"')
    expect(source).toContain('class="ds-controls__suffix tl-input-row"')
    expect(source).not.toContain('--ds-tlmp-select-max-w')
    expect(source).not.toContain('--ds-tlmp-limit-input-w')
  })
})

