import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { routes } from './index'

/**
 * 032 S7 (D-7). The route table, as a router resolves it.
 *
 * Red on `01553c42`: no catch-all - a mistyped path or an old bookmark (`/liquidity`, `/advice`) rendered an empty
 * shell with the sidebar highlighting nothing and the address bar lying about where the operator is, and `/liquidity`
 * (removed with its screen in S5) was one of those. The two removed screens whose paths operators have bookmarked
 * redirect to where their function went; anything else lands on a screen that says the page does not exist.
 */

function memoryRouter() {
  return createRouter({ history: createMemoryHistory(), routes })
}

describe('route table', () => {
  it('sends the paths of removed screens to where their function went', async () => {
    const router = memoryRouter()
    for (const [from, to] of [
      ['/liquidity', '/dashboard'],
      ['/incidents', '/integrity'],
      ['/feature-flags', '/config'],
      ['/', '/dashboard'],
    ] as const) {
      await router.push(from)
      expect(router.currentRoute.value.path, from).toBe(to)
    }
  })

  it('resolves every screen of the menu to its own page', async () => {
    const router = memoryRouter()
    for (const path of ['/dashboard', '/integrity', '/trustlines', '/graph', '/participants', '/config', '/audit-log', '/equivalents']) {
      await router.push(path)
      expect(router.currentRoute.value.path).toBe(path)
      expect(router.currentRoute.value.name, path).not.toBe('NotFound')
      expect(router.currentRoute.value.meta.titleKey, path).toBeTruthy()
    }
  })

  it('shows "not found" for any other path and keeps the path the operator typed', async () => {
    const router = memoryRouter()
    for (const path of ['/nonsense', '/graph/extra', '/a/b/c']) {
      await router.push(path)
      expect(router.currentRoute.value.name, path).toBe('NotFound')
      expect(router.currentRoute.value.path).toBe(path)
      expect(router.currentRoute.value.meta.titleKey).toBe('notFound.title')
    }
  })
})
