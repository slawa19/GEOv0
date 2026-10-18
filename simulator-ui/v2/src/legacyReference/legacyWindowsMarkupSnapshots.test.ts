import { createApp, h, nextTick, type Component } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import NodeCardOverlay from '../components/NodeCardOverlay.vue'
import EdgeDetailPopup from '../components/EdgeDetailPopup.vue'

import type { GraphNode } from '../types'
import { DATA_PALETTE_DEFAULT } from '../vizMapping'
import type { TrustlineInfo } from '../api/simulatorTypes'
import type { InteractPhase, InteractState } from '../composables/useInteractMode'

function normalizeHtml(raw: string): string {
  return raw
    .replace(/\sdata-v-[a-f0-9]+(="")?/g, '')
    .replace(/\s+/g, ' ')
    .replace(/>\s+</g, '><')
    .trim()
}

async function renderOuterHtml(component: Component, props: Record<string, unknown>): Promise<string> {
  vi.stubGlobal('ResizeObserver', undefined)

  const host = document.createElement('div')
  document.body.appendChild(host)

  const app = createApp({ render: () => h(component, props) })
  try {
    app.mount(host)
    await nextTick()
    await nextTick()

    const el = host.firstElementChild as HTMLElement | null
    expect(el).toBeTruthy()
    return normalizeHtml(el!.outerHTML)
  } finally {
    app.unmount()
    host.remove()
    vi.unstubAllGlobals()
  }
}

// 045 S2 (2026-10-17): the snapshots of the three action panels (ManualPaymentPanel, TrustlineManagementPanel, ClearingPanel)
// were removed with the markup they froze - the slice changed that markup on purpose, and regenerating a snapshot is not a
// check (spec 045, Verification plan 4; 041 F-041-9). Those panels are held by their mount tests by `data-testid` and by
// `e2e/p045-s2-panels.spec.ts`. The two inspectors below (S3 surface) keep theirs until S3 changes them.
describe('Legacy windows reference — markup snapshots', () => {
  it('NodeCardOverlay', async () => {
    // 012 / `T1208`, `C-B4-3-002`. This node used to carry `net_balance: '42'` — a
    // major-unit field the fixture pipeline does not produce and `src/fixtures.ts` does not
    // even read, so the only assertion in the whole suite that observed a rendered balance
    // exercised a branch that does not exist in production, and the atoms branch (the
    // `F-012-6` defect: raw atoms on screen, wrong by 10^precision) stayed unobservable.
    // The node now carries what the pipeline actually produces: magnitude atoms plus a sign.
    const node: GraphNode = {
      id: 'bob',
      name: 'Bob',
      type: 'person',
      status: 'active',
      viz_color_key: 'unknown',
      net_balance_atoms: '4200',
      net_sign: 1,
    }

    const html = await renderOuterHtml(NodeCardOverlay, {
      node,
      edgeStats: { outLimitText: '100', inLimitText: '50', degree: 3 },
      equivalentText: 'UAH',
      // 039 slice B (2026-10-12): the card takes the resolved data palette; its markup gained the role line and its
      // balance is coloured by the role (creditor here) - the stored snapshot of THIS component was regenerated.
      palette: DATA_PALETTE_DEFAULT,
      showPinActions: true,
      isPinned: false,
      pin: () => {},
      unpin: () => {},
      interactMode: true,
      interactBusy: false,
      trustlinesLoading: false,
      interactTrustlines: [
        {
          from_pid: 'bob',
          from_name: 'Bob',
          to_pid: 'alice',
          to_name: 'Alice',
          equivalent: 'UAH',
          limit: '100',
          used: '12',
          reverse_used: '0',
          available: '88',
          status: 'active',
        } satisfies TrustlineInfo,
      ],
      onInteractSendPayment: () => {},
      onInteractNewTrustline: () => {},
      onInteractEditTrustline: () => {},
    })

    expect(html).toMatchSnapshot()
  })

  it('EdgeDetailPopup', async () => {
    const phase: InteractPhase = 'editing-trustline'
    const state: InteractState = {
      phase,
      fromPid: 'alice',
      toPid: 'bob',
      initiatedWithPrefilledFrom: false,
      selectedEdgeKey: 'alice→bob',
      edgeAnchor: { x: 100, y: 200 },
      error: null,
      lastClearing: null,
    }

    const html = await renderOuterHtml(EdgeDetailPopup, {
      phase,
      state,
      unit: 'UAH',
      // `F-013-7`: этот снимок раньше монтировал компонент БЕЗ основания и получал разрешённую
      // мутацию по умолчанию — тот самый fail-open. Разметка снимка от этой строки НЕ меняется:
      // источник ответил строкой, и попап выглядит так же, как раньше.
      figuresSource: { kind: 'row' } as const,
      used: '12',
      reverseUsed: '0',
      limit: '100',
      available: '88',
      status: 'active',
      busy: false,
      close: () => {},
      onChangeLimit: () => {},
      onCloseLine: () => {},
      onSendPayment: () => {},
    })

    expect(html).toMatchSnapshot()
  })
})
