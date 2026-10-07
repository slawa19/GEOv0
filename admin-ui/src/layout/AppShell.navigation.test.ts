import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AppShell from './AppShell.vue'
import { setLocale } from '../i18n'

/**
 * 032 S7 (D-8): the sidebar navigates once per click, and a browser that refuses to store the theme does not take
 * the shell down.
 *
 * Red on `01553c42`: `<el-menu router>` already pushed the item's index, and every item ALSO called
 * `navigate(item.path)` on click - two pushes per click (the second one a redundant navigation to the same place);
 * and the theme watcher called `localStorage.setItem` outside any `try`, so with storage blocked (private window,
 * blocked site data - the accessor throws) mounting the shell threw during setup and the application showed nothing.
 */

const apiMock = vi.hoisted(() => ({
  health: vi.fn(),
  healthDb: vi.fn(),
  migrations: vi.fn(),
  getConfig: vi.fn(),
}))
vi.mock('../api', () => ({ api: apiMock }))

function routerWithPages() {
  const page = { template: '<div />' }
  return createRouter({
    history: createMemoryHistory(),
    routes: ['/dashboard', '/integrity', '/trustlines', '/graph', '/participants', '/config', '/audit-log', '/equivalents'].map((path) => ({
      path,
      component: page,
      meta: { titleKey: 'nav.dashboard.label' },
    })),
  })
}

beforeEach(() => {
  delete (globalThis as { __GEO_HEALTH_POLL_TIMER__?: number }).__GEO_HEALTH_POLL_TIMER__
  setLocale('en')
  apiMock.health.mockResolvedValue({ status: 'ok' })
  apiMock.healthDb.mockResolvedValue({ status: 'ok', dialect: 'postgresql' })
  apiMock.migrations.mockResolvedValue({ current_revision: 'a', head_revision: 'a', is_up_to_date: true })
  apiMock.getConfig.mockResolvedValue({})
})

afterEach(() => {
  delete (globalThis as { __GEO_HEALTH_POLL_TIMER__?: number }).__GEO_HEALTH_POLL_TIMER__
})

describe('AppShell navigation', () => {
  it('navigates once per click on a menu item, to the item', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = routerWithPages()
    await router.push('/dashboard')
    await router.isReady()
    const push = vi.spyOn(router, 'push')
    const wrapper = mount(AppShell, { global: { plugins: [pinia, router, ElementPlus] } })
    await flushPromises()

    const item = wrapper.findAll('.el-menu-item').find((el) => el.text() === 'Trustlines')
    expect(item, 'the menu has a Trustlines item').toBeDefined()
    await item!.trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/trustlines')
    expect(push).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('does not fail to mount when the browser refuses to store the theme', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage denied')
    })
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = routerWithPages()
    await router.push('/dashboard')
    await router.isReady()

    const wrapper = mount(AppShell, { global: { plugins: [pinia, router, ElementPlus] } })
    await flushPromises()
    await nextTick()

    expect(wrapper.find('.app-root').exists()).toBe(true)
    // The choice still applies for this session.
    await wrapper.find('.el-switch').trigger('click')
    await nextTick()
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    wrapper.unmount()
  })
})
