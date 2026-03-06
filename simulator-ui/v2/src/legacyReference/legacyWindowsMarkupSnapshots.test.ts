import { createApp, h, nextTick, type Component } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import NodeCardOverlay from '../components/NodeCardOverlay.vue'
import EdgeDetailPopup from '../components/EdgeDetailPopup.vue'
import ManualPaymentPanel from '../components/ManualPaymentPanel.vue'
import TrustlineManagementPanel from '../components/TrustlineManagementPanel.vue'
import ClearingPanel from '../components/ClearingPanel.vue'

import type { GraphNode } from '../types'
import type { ParticipantInfo, TrustlineInfo } from '../api/simulatorTypes'
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

describe('Legacy windows reference — markup snapshots', () => {
  it('NodeCardOverlay', async () => {
    const node: GraphNode = {
      id: 'bob',
      name: 'Bob',
      type: 'person',
      status: 'active',
      viz_color_key: 'unknown',
      net_balance: '42',
    }

    const html = await renderOuterHtml(NodeCardOverlay, {
      node,
      edgeStats: { outLimitText: '100', inLimitText: '50', degree: 3 },
      equivalentText: 'UAH',
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
      used: '12',
      reverseUsed: '0',
      limit: '100',
      available: '88',
      status: 'active',
      busy: false,
      forceHidden: false,
      close: () => {},
      onChangeLimit: () => {},
      onCloseLine: () => {},
      onSendPayment: () => {},
    })

    expect(html).toMatchSnapshot()
  })

  it('ManualPaymentPanel', async () => {
    const phase: InteractPhase = 'confirm-payment'
    const state: InteractState = {
      phase,
      fromPid: 'alice',
      toPid: 'bob',
      initiatedWithPrefilledFrom: true,
      selectedEdgeKey: null,
      edgeAnchor: null,
      error: null,
      lastClearing: null,
    }

    const participants: ParticipantInfo[] = [
      { pid: 'alice', name: 'Alice', type: 'person', status: 'active' },
      { pid: 'bob', name: 'Bob', type: 'person', status: 'active' },
      { pid: 'carol', name: 'Carol', type: 'person', status: 'active' },
    ]

    const html = await renderOuterHtml(ManualPaymentPanel, {
      phase,
      state,
      unit: 'UAH',
      availableCapacity: '88',
      trustlinesLoading: false,
      trustlinesLastError: null,
      paymentTargetsLoading: false,
      paymentTargetsMaxHops: 6,
      paymentTargetsLastError: null,
      paymentToTargetIds: new Set(['bob', 'carol']),
      trustlines: [],
      participants,
      setFromPid: () => {},
      setToPid: () => {},
      busy: false,
      canSendPayment: true,
      confirmPayment: () => {},
      cancel: () => {},
    })

    expect(html).toMatchSnapshot()
  })

  it('TrustlineManagementPanel', async () => {
    const phase: InteractPhase = 'editing-trustline'
    const state: InteractState = {
      phase,
      fromPid: 'alice',
      toPid: 'bob',
      initiatedWithPrefilledFrom: true,
      selectedEdgeKey: null,
      edgeAnchor: null,
      error: null,
      lastClearing: null,
    }

    const participants: ParticipantInfo[] = [
      { pid: 'alice', name: 'Alice', type: 'person', status: 'active' },
      { pid: 'bob', name: 'Bob', type: 'person', status: 'active' },
      { pid: 'carol', name: 'Carol', type: 'person', status: 'active' },
    ]

    const trustlines: TrustlineInfo[] = [
      {
        from_pid: 'alice',
        from_name: 'Alice',
        to_pid: 'bob',
        to_name: 'Bob',
        equivalent: 'UAH',
        limit: '100',
        used: '12',
        reverse_used: '0',
        available: '88',
        status: 'active',
      },
    ]

    const html = await renderOuterHtml(TrustlineManagementPanel, {
      phase,
      state,
      unit: 'UAH',
      used: '12',
      currentLimit: '100',
      available: '88',
      participants,
      trustlines,
      setFromPid: () => {},
      setToPid: () => {},
      selectTrustline: () => {},
      busy: false,
      confirmTrustlineCreate: () => {},
      confirmTrustlineUpdate: () => {},
      confirmTrustlineClose: () => {},
      cancel: () => {},
    })

    expect(html).toMatchSnapshot()
  })

  it('ClearingPanel', async () => {
    const phase: InteractPhase = 'confirm-clearing'
    const state: InteractState = {
      phase,
      fromPid: null,
      toPid: null,
      initiatedWithPrefilledFrom: false,
      selectedEdgeKey: null,
      edgeAnchor: null,
      error: null,
      lastClearing: {
        ok: true,
        equivalent: 'UAH',
        cleared_cycles: 1,
        total_cleared_amount: '10',
        cycles: [
          {
            cleared_amount: '10',
            edges: [{ from: 'alice', to: 'bob' }],
          },
        ],
      },
    }

    const html = await renderOuterHtml(ClearingPanel, {
      phase,
      state,
      busy: false,
      equivalent: 'UAH',
      confirmClearing: () => {},
      cancel: () => {},
    })

    expect(html).toMatchSnapshot()
  })
})
