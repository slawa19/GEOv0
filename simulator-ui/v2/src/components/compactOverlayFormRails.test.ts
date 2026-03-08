import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import ManualPaymentPanel from './ManualPaymentPanel.vue'
import TrustlineManagementPanel from './TrustlineManagementPanel.vue'

const manualPaymentPanelComponent: Component = ManualPaymentPanel
const trustlineManagementPanelComponent: Component = TrustlineManagementPanel

describe('shared compact overlay form rails', () => {
  it('keeps compact-row and suffix primitives in the shared design-system rail contract', () => {
    const cssSource = readFileSync(resolve(process.cwd(), 'src/ui-kit/designSystem.primitives.css'), 'utf8')
    const tokenSource = readFileSync(resolve(process.cwd(), 'src/ui-kit/designSystem.tokens.css'), 'utf8')
    const overlaySelectSource = readFileSync(resolve(process.cwd(), 'src/components/common/OverlaySelect.vue'), 'utf8')

    expect(cssSource).toContain('.ds-controls__row--compact > .ds-input,')
    expect(cssSource).toContain('.ds-controls__row--compact > .ds-select,')
    expect(cssSource).toContain('.ds-controls__row--compact > .ds-controls__suffix {')
    expect(cssSource).toContain('width: min(100%, var(--ds-controls-field-max-w));')
    expect(cssSource).toContain('.ds-controls__suffix {')
    expect(cssSource).toContain('width: min(100%, var(--ds-controls-suffix-max-w));')
    expect(tokenSource).toContain('--ds-controls-interact-select-max-w: 320px;')
    expect(overlaySelectSource).toContain('var(--ds-controls-interact-select-max-w)')
  })

  it('keeps ManualPaymentPanel on shared compact-row primitives without local width clamps', async () => {
    const source = readFileSync(resolve(process.cwd(), 'src/components/ManualPaymentPanel.vue'), 'utf8')
    expect(source).toContain('class="ds-controls__row ds-controls__row--compact"')
    expect(source).toContain('class="ds-controls__suffix mp-amount-row"')
    expect(source).not.toContain('mp-amount-input {')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
          phase: 'confirm-payment',
          state: reactive({ fromPid: 'alice', toPid: 'bob' }),
          unit: 'EQ',
          availableCapacity: '10',
          trustlinesLoading: false,
          paymentTargetsLoading: false,
          paymentToTargetIds: new Set(['bob']),
          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob with a very long label for compact rail checks' },
          ],
          busy: false,
          canSendPayment: true,
          confirmPayment: vi.fn(),
          cancel: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    const panel = host.querySelector('[data-testid="manual-payment-panel"]') as HTMLElement | null
    const row = host.querySelector('.ds-controls__row.ds-controls__row--compact') as HTMLElement | null
    const suffix = host.querySelector('.mp-amount-row') as HTMLElement | null

    expect(panel).toBeTruthy()
    expect(panel?.style.width).toBe('')
    expect(panel?.style.maxWidth).toBe('')
    expect(row).toBeTruthy()
    expect(suffix?.classList.contains('ds-controls__suffix')).toBe(true)

    app.unmount()
    host.remove()
  })

  it('keeps TrustlineManagementPanel on shared compact-row primitives without local width clamps', async () => {
    const source = readFileSync(resolve(process.cwd(), 'src/components/TrustlineManagementPanel.vue'), 'utf8')
    expect(source).toContain('class="ds-controls__row ds-controls__row--compact"')
    expect(source).toContain('class="ds-controls__suffix tl-input-row"')
    expect(source).not.toContain('--ds-tlmp-select-max-w')
    expect(source).not.toContain('--ds-tlmp-limit-input-w')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'editing-trustline',
      fromPid: 'alice',
      toPid: 'bob',
      selectedEdgeKey: null,
      edgeAnchor: null,
      error: null,
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(trustlineManagementPanelComponent, {
          phase: 'editing-trustline',
          state,
          unit: 'EQ',
          used: '1',
          currentLimit: '10',
          available: '9',
          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob with a very long trustline label for compact rail checks' },
          ],
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

    const panel = host.querySelector('[data-testid="trustline-panel"]') as HTMLElement | null
    const row = host.querySelector('.ds-controls__row.ds-controls__row--compact') as HTMLElement | null
    const suffix = host.querySelector('.tl-input-row') as HTMLElement | null

    expect(panel).toBeTruthy()
    expect(panel?.style.width).toBe('')
    expect(panel?.style.maxWidth).toBe('')
    expect(row).toBeTruthy()
    expect(suffix?.classList.contains('ds-controls__suffix')).toBe(true)

    app.unmount()
    host.remove()
  })
})