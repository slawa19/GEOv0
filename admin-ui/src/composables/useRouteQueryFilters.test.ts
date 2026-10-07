import { mount } from '@vue/test-utils'
import { defineComponent, nextTick, ref, type Ref } from 'vue'
import { createMemoryHistory, createRouter, useRoute, useRouter, type Router } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'

import { useRouteQueryFilters } from './useRouteQueryFilters'

/**
 * 032 S7 (D-10): one implementation of "page filters <-> route query", replacing five hand-written copies.
 * Observed through the route and the callbacks, with a real memory router.
 */

type Harness = {
  router: Router
  q: Ref<string>
  threshold: Ref<string>
  kept: Ref<string>
  onRouteChange: ReturnType<typeof vi.fn>
  onUserChange: ReturnType<typeof vi.fn>
  applyRoute: () => boolean
  unmount: () => void
}

async function harness(initial: Record<string, string> = {}): Promise<Harness> {
  const onRouteChange = vi.fn()
  const onUserChange = vi.fn()
  const q = ref('')
  const threshold = ref('0.10')
  const kept = ref('')
  let applyRoute!: () => boolean
  const page = defineComponent({
    setup() {
      const filters = useRouteQueryFilters({
        route: useRoute(),
        router: useRouter(),
        path: '/page',
        filters: {
          q: { model: q, fromQuery: (raw) => raw.trim(), toQuery: (v) => v.trim() },
          kept: { model: kept, keepWhenAbsent: true },
          threshold: {
            model: threshold,
            fromQuery: (raw) => raw.trim() || '0.10',
            toQuery: (v) => (v.trim() !== '0.10' ? v.trim() : ''),
            reloads: false,
          },
        },
        onRouteChange,
        onUserChange,
      })
      applyRoute = filters.applyRoute
      return () => null
    },
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/page', component: page },
      { path: '/elsewhere', component: { template: '<div />' } },
    ],
  })
  await router.push({ path: '/page', query: initial })
  await router.isReady()
  const wrapper = mount({ template: '<router-view />' }, { global: { plugins: [router] } })
  await nextTick()
  return { router, q, threshold, kept, onRouteChange, onUserChange, applyRoute: () => applyRoute(), unmount: () => wrapper.unmount() }
}

async function flush() {
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
}

describe('useRouteQueryFilters', () => {
  it('hydrates the filters from the query without reporting a user edit', async () => {
    const h = await harness({ q: ' abc ', threshold: '0.5' })
    expect(h.applyRoute()).toBe(true)
    await flush()
    expect(h.q.value).toBe('abc')
    expect(h.threshold.value).toBe('0.5')
    expect(h.onUserChange).not.toHaveBeenCalled()
    expect(h.router.currentRoute.value.query).toEqual({ q: ' abc ', threshold: '0.5' })
    h.unmount()
  })

  it('writes an edit to the query, keeps the other keys, drops a default and reports it once per edit', async () => {
    const h = await harness({ other: 'kept' })
    h.q.value = 'xyz'
    await flush()
    expect(h.router.currentRoute.value.query).toEqual({ other: 'kept', q: 'xyz' })
    expect(h.onUserChange).toHaveBeenCalledTimes(1)

    h.q.value = ''
    await flush()
    expect(h.router.currentRoute.value.query).toEqual({ other: 'kept' })
    h.unmount()
  })

  it('a filter marked reloads:false is written to the query but never reported', async () => {
    const h = await harness()
    h.threshold.value = '0.3'
    await flush()
    expect(h.router.currentRoute.value.query).toEqual({ threshold: '0.3' })
    expect(h.onUserChange).not.toHaveBeenCalled()
    h.threshold.value = '0.10'
    await flush()
    expect(h.router.currentRoute.value.query).toEqual({})
    h.unmount()
  })

  it('a change of the route from outside moves the filter and is reported as a route change', async () => {
    const h = await harness()
    await h.router.replace({ query: { q: 'from link' } })
    await flush()
    expect(h.q.value).toBe('from link')
    expect(h.onRouteChange).toHaveBeenCalledTimes(1)
    expect(h.onUserChange).not.toHaveBeenCalled()

    // A key that disappears from the route puts its filter back to the default.
    await h.router.replace({ query: {} })
    await flush()
    expect(h.q.value).toBe('')
    expect(h.onRouteChange).toHaveBeenCalledTimes(2)
    h.unmount()
  })

  it('a filter marked keepWhenAbsent keeps the value the page chose when the route does not carry the key', async () => {
    const h = await harness()
    h.kept.value = 'UAH'
    await flush()
    expect(h.router.currentRoute.value.query).toEqual({ kept: 'UAH' })

    await h.router.replace({ query: { q: 'x' } })
    await flush()
    expect(h.kept.value).toBe('UAH')
    expect(h.q.value).toBe('x')
    // And the route still wins when it does carry the key.
    await h.router.replace({ query: { kept: 'EUR' } })
    await flush()
    expect(h.kept.value).toBe('EUR')
    h.unmount()
  })

  it('does not rewrite what the operator is typing when the route only echoes it (a trailing space survives)', async () => {
    const h = await harness()
    h.q.value = 'two '
    await flush()
    expect(h.router.currentRoute.value.query).toEqual({ q: 'two' })
    expect(h.q.value).toBe('two ')
    h.q.value = 'two w'
    await flush()
    expect(h.q.value).toBe('two w')
    h.unmount()
  })

  it('writes nothing to the query of the page the operator has already left (page still mounted)', async () => {
    const q = ref('')
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/page', component: { template: '<div />' } },
        { path: '/elsewhere', component: { template: '<div />' } },
      ],
    })
    await router.push('/page')
    await router.isReady()
    const page = defineComponent({
      setup() {
        useRouteQueryFilters({ route: useRoute(), router: useRouter(), path: '/page', filters: { q: { model: q } } })
        return () => null
      },
    })
    // Mounted outside the router view: it outlives the navigation, as a page does while a transition runs.
    const wrapper = mount(page, { global: { plugins: [router] } })
    await router.push('/elsewhere')
    q.value = 'late'
    await flush()
    expect(router.currentRoute.value.path).toBe('/elsewhere')
    expect(router.currentRoute.value.query).toEqual({})
    wrapper.unmount()
  })
})
