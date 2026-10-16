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
      const text = (tooltip?.textContent ?? '').replace(/\s+/g, ' ')
      // 043 S1: the line in words - creditor `from` trusts debtor `to` up to the limit; `to` owes `from` the used amount.
      expect(text).toContain('A доверяет B до 20')
      expect(text).toContain('долг B перед A — 10 · доступно 10')
      expect(text).toContain('Status')
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

describe('EdgeTooltip - 043 S1: the line in words and numbers, while watching too', () => {
  function mountTip(props: Record<string, unknown>) {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const names: Record<string, string> = { m: 'Марта', o: 'Олег' }
    const app = createApp({ render: () => h(edgeTooltipComponent, { style: {}, getNodeName: (id: string) => names[id] ?? null, ...props }) })
    app.mount(host)
    return { host, done: () => { app.unmount(); host.remove() } }
  }
  const textOf = (host: HTMLElement) => (host.textContent ?? '').replace(/\s+/g, ' ').trim()

  it('outside Interact the figures are a sentence with the unit, and the direction is creditor -> debtor', async () => {
    const { host, done } = mountTip({
      edge: { key: 'm->o', fromId: 'm', toId: 'o', amountText: 'from: 120.00 / to: 500.00 UAH', trustLimit: '500.00', used: '120.00', available: '380.00' },
      unit: 'UAH',
    })
    await nextTick()
    const text = textOf(host)
    expect(text).toContain('Марта доверяет Олег до 500.00 UAH')
    expect(text).toContain('долг Олег перед Марта — 120.00 UAH · доступно 380.00 UAH')
    // The bare "from: / to:" line is replaced, not shown beside the sentence.
    expect(text).not.toContain('from: 120.00')
    done()
  })

  it('a negative "available" reads as the excess over the limit; without figures the amount line stays', async () => {
    const over = mountTip({ edge: { key: 'm->o', fromId: 'm', toId: 'o', amountText: '', trustLimit: '100', used: '130', available: '-30' }, unit: 'UAH' })
    await nextTick()
    expect(textOf(over.host)).toContain('сверх лимита на 30 UAH')
    over.done()

    const bare = mountTip({ edge: { key: 'm->o', fromId: 'm', toId: 'o', amountText: 'from: — / to: — UAH' } })
    await nextTick()
    expect(textOf(bare.host)).toContain('from: — / to: — UAH')
    expect(textOf(bare.host)).not.toContain('доверяет')
    bare.done()
  })
})
