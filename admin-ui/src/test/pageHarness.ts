import { mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick, type Component } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { vi } from 'vitest'

/**
 * Mounts a page the way the application does: the real Element Plus, a real (memory) router and the page at its
 * path, so a test drives and reads the public surface - DOM, route, calls to the API client - and never the
 * component's `setupState`.
 */

const STUB = { template: '<div />' }

/** Paths a page can navigate to; they exist so `router.push` resolves, they render nothing. */
const NAVIGATION_TARGETS = ['/audit-log', '/participants', '/trustlines', '/equivalents', '/integrity', '/dashboard', '/config', '/graph']

export type Mounted = { wrapper: VueWrapper; router: Router }

export async function mountPage(
  component: Component,
  path: string,
  query: Record<string, string> = {},
): Promise<Mounted> {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path, component },
      ...NAVIGATION_TARGETS.filter((p) => p !== path).map((p) => ({ path: p, component: STUB })),
    ],
  })
  await router.push({ path, query })
  await router.isReady()
  const wrapper = mount(component, { global: { plugins: [pinia, router, ElementPlus] } })
  await settle()
  return { wrapper, router }
}

/** Lets promises, Vue's scheduler and (under fake timers) timers due now run. Safe with and without fake timers. */
export async function settle(): Promise<void> {
  if (vi.isFakeTimers()) await vi.advanceTimersByTimeAsync(0)
  else await new Promise<void>((resolve) => setTimeout(resolve, 0))
  await nextTick()
  if (vi.isFakeTimers()) await vi.advanceTimersByTimeAsync(0)
  else await new Promise<void>((resolve) => setTimeout(resolve, 0))
}

export type Deferred<T> = {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason: unknown) => void
}

export function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

export function paginated<T>(items: T[], total = items.length) {
  return { items, page: 1, per_page: 20, total }
}
