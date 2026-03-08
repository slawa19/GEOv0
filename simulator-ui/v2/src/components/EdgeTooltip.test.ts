import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createApp, h, nextTick, type Component } from 'vue'
import { describe, expect, it } from 'vitest'

import EdgeTooltip from './EdgeTooltip.vue'

const edgeTooltipComponent: Component = EdgeTooltip
const edgeTooltipSource = readFileSync(resolve(process.cwd(), 'src/components/EdgeTooltip.vue'), 'utf8')
const overlaysSource = readFileSync(resolve(process.cwd(), 'src/ui-kit/designSystem.overlays.css'), 'utf8')
const tokensSource = readFileSync(resolve(process.cwd(), 'src/ui-kit/designSystem.tokens.css'), 'utf8')

describe('EdgeTooltip', () => {
  it('renders interact detail rows using the shared tooltip contract', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({
      render: () =>
        h(edgeTooltipComponent, {
          edge: {
            key: 'a->b',
            fromId: 'A',
            toId: 'B',
            amountText: '10 UAH',
            trustLimit: '20',
            used: '10',
            available: '10',
            edgeStatus: 'active',
          },
          style: { left: '10px', top: '20px' },
          interactMode: true,
          getNodeName: (id: string) => id,
        }),
    })

    try {
      app.mount(host)
      await nextTick()

      const tooltip = host.querySelector('.ds-ov-tooltip') as HTMLElement | null
      expect(tooltip).toBeTruthy()
      expect(tooltip?.textContent ?? '').toContain('A → B')
      expect(tooltip?.textContent ?? '').toContain('Limit')
      expect(tooltip?.textContent ?? '').toContain('Used')
      expect(tooltip?.textContent ?? '').toContain('Avail')
      expect(tooltip?.textContent ?? '').toContain('Status')
    } finally {
      app.unmount()
      host.remove()
    }
  })

  it('keeps tooltip micro-layout in the DS overlay layer instead of scoped literals', () => {
    expect(edgeTooltipSource).not.toContain('gap: 10px;')
    expect(edgeTooltipSource).not.toContain('font-size: 0.78rem;')
    expect(edgeTooltipSource).not.toContain('margin: 4px 0;')

    expect(overlaysSource).toContain('.ds-ov-tooltip__divider {')
    expect(overlaysSource).toContain('margin: var(--ds-ov-tooltip-divider-margin-y) 0;')
    expect(overlaysSource).toContain('.ds-ov-tooltip__row {')
    expect(overlaysSource).toContain('gap: var(--ds-ov-tooltip-row-gap);')
    expect(overlaysSource).toContain('font-size: var(--ds-ov-tooltip-row-font-size);')
    expect(tokensSource).toContain('--ds-ov-tooltip-row-gap: 10px;')
    expect(tokensSource).toContain('--ds-ov-tooltip-row-font-size: 0.78rem;')
  })
})