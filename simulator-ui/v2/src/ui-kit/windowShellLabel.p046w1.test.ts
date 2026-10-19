/**
 * 046 wave 1 (wave 2 finding 9): the accessible name of an action window's shell is in the language of the interface,
 * the same words as the panel inside it. On `9c729faf` the shell of the payment window was "Manual payment panel" under
 * `?lang=ru` while the panel was «Окно платежа». The English names are byte for byte the earlier ones.
 *
 * LIMITS: the two inspector windows (line window, participant card) are English as a whole until wave 3, and so are
 * their shells (checked below, so that a half-translated inspector is not introduced here); the e2e
 * `p046-w1-first-screen.spec.ts` reads the attribute on a window opened in the browser.
 */
import { afterEach, describe, expect, it } from 'vitest'

import type { WindowInstance } from '../composables/windowManager/types'
import { setUiLocale } from '../i18n/locale'
import { t } from '../i18n/t'
import { resolveWindowSurfaceDescriptor } from './overlaySurfaceCatalog'

afterEach(() => setUiLocale('en'))

const win = (type: WindowInstance['type'], data: unknown) => ({ id: 1, type, data }) as unknown as WindowInstance
const panel = (p: 'payment' | 'trustline' | 'clearing') => win('interact-panel', { panel: p, phase: 'x' })

describe('046 wave 1 - the window shell is named in the interface language', () => {
  it('en: the earlier names, byte for byte', () => {
    setUiLocale('en')
    const got = (['payment', 'trustline', 'clearing'] as const).map((p) => [resolveWindowSurfaceDescriptor(panel(p)).title, resolveWindowSurfaceDescriptor(panel(p)).ariaLabel])
    expect(got).toEqual([
      ['Manual payment', 'Manual payment panel'],
      ['Trustline', 'Trustline management panel'],
      ['Clearing', 'Clearing panel'],
    ])
  })

  it('ru: the shell says what the panel inside it says', () => {
    setUiLocale('ru')
    const got = (['payment', 'trustline', 'clearing'] as const).map((p) => resolveWindowSurfaceDescriptor(panel(p)).ariaLabel)
    expect(got).toEqual(['Окно платежа', 'Окно линии доверия', 'Окно клиринга'])
    expect(got).toEqual([t('payment.panelAria'), t('trustline.panelAria'), t('clearing.panelAria')])
    expect(resolveWindowSurfaceDescriptor(panel('clearing')).title).toBe('Клиринг')
    expect(resolveWindowSurfaceDescriptor(win('unknown-kind' as WindowInstance['type'], {})).ariaLabel).toBe('Окно')
  })

  it('a switch renames the shell at the next read (no cached English name)', () => {
    setUiLocale('en')
    const w = panel('payment')
    expect(resolveWindowSurfaceDescriptor(w).ariaLabel).toBe('Manual payment panel')
    setUiLocale('ru')
    expect(resolveWindowSurfaceDescriptor(w).ariaLabel).toBe('Окно платежа')
  })

  // 046 wave 3 (2026-10-19): the inspector windows moved with their content - their shells are named in the language of the
  // interface (until wave 3 this held them English in ru). English stays byte for byte.
  it('the inspector windows are named in the language of the interface (wave 3); English as before', () => {
    setUiLocale('ru')
    expect(resolveWindowSurfaceDescriptor(win('edge-detail', { fromPid: 'alice', toPid: 'bob' })).ariaLabel).toBe('Сведения о линии доверия: alice → bob')
    expect(resolveWindowSurfaceDescriptor(win('node-card', { nodeId: 'bob' }), { getNodeName: () => 'Bob' }).ariaLabel).toBe('Карточка участника: Bob')
    setUiLocale('en')
    expect(resolveWindowSurfaceDescriptor(win('edge-detail', { fromPid: 'alice', toPid: 'bob' })).ariaLabel).toBe('Trustline details: alice to bob')
    expect(resolveWindowSurfaceDescriptor(win('node-card', { nodeId: 'bob' }), { getNodeName: () => 'Bob' }).ariaLabel).toBe('Node details: Bob')
  })
})
