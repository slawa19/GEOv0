/**
 * 054-B-13: «New limit» was pre-filled only when the edit phase began (or the pair changed). When the trust-lines source had
 * not answered yet - the first open of a line, or right after an update (`useInteractMode.ts` patches the cache and then
 * invalidates it, so the answer is gone until the forced refresh returns) - the panel is fail-closed (`F-013-7`: no figures),
 * the field was pre-filled empty, and the answer that came a moment later never reached it.
 * Real panel; the props change as the parent's do when the source answers (`figuresSource` and the answered row).
 */
import { createApp, h, nextTick, reactive } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import TrustlineManagementPanel from './TrustlineManagementPanel.vue'
import type { TrustlineFiguresSource } from '../composables/interact/trustlinesSourceState'

afterEach(() => { document.body.innerHTML = '' })

const ROW = { from_pid: 'alice', to_pid: 'bob', equivalent: 'UAH', limit: '150.00', used: '20.00', available: '130.00', status: 'active' }

function stand() {
  const state = reactive({
    phase: 'editing-trustline', fromPid: 'alice' as string | null, toPid: 'bob' as string | null,
    selectedEdgeKey: null, edgeAnchor: null, error: null, lastClearing: null,
  })
  const props = reactive({
    figuresSource: { kind: 'loading' } as TrustlineFiguresSource,
    trustlines: [] as Array<typeof ROW>,
  })
  const host = document.createElement('div')
  document.body.appendChild(host)
  createApp({
    render: () => h(TrustlineManagementPanel as never, {
      phase: 'editing-trustline', state, unit: 'UAH', used: '20.00', currentLimit: '100.00', available: '80.00',
      participants: [{ pid: 'alice', name: 'Alice' }, { pid: 'bob', name: 'Bob' }],
      figuresSource: props.figuresSource, trustlines: props.trustlines, busy: false,
      confirmTrustlineCreate: vi.fn(), confirmTrustlineUpdate: vi.fn(), confirmTrustlineClose: vi.fn(), cancel: vi.fn(),
    }),
  }).mount(host)
  const input = () => host.querySelector('#tl-new-limit') as HTMLInputElement
  const answer = async () => { props.trustlines = [ROW]; props.figuresSource = { kind: 'row' }; await nextTick(); await nextTick() }
  return { input, answer }
}

describe('054-B-13: the «New limit» pre-fill', () => {
  it('takes the limit of the source answer that arrives after the edit began', async () => {
    const s = stand()
    await nextTick()
    expect(s.input().value, 'stand: fail-closed before the answer - no figure is offered').toBe('')
    await s.answer()
    expect(s.input().value).toBe('150.00')
  })

  it('counter-check: a value the user typed before the answer is kept', async () => {
    const s = stand()
    await nextTick()
    s.input().value = '500'
    s.input().dispatchEvent(new Event('input'))
    await nextTick()
    await s.answer()
    expect(s.input().value).toBe('500')
  })
})
