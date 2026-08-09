import { mount } from '@vue/test-utils'
import { ElButton, ElOption, ElSelect } from 'element-plus'
import { describe, expect, it } from 'vitest'

import GraphKeyboardNavigator from './GraphKeyboardNavigator.vue'

describe('GraphKeyboardNavigator', () => {
  it('remains searchable with no pre-mounted options and exposes the guard hint', async () => {
    const wrapper = mount(GraphKeyboardNavigator, {
      props: {
        modelValue: '',
        options: [],
        busy: false,
        hintId: 'graph-keyboard-guard-hint',
      },
      global: {
        components: { ElButton, ElOption, ElSelect },
      },
    })

    const input = wrapper.get('[data-testid="graph-element-select"] input')
    expect(input.attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[role="group"]').attributes('aria-describedby')).toBe('graph-keyboard-guard-hint')
    expect(wrapper.get('[data-testid="graph-element-open"]').attributes('disabled')).toBeDefined()

    const remoteMethod = wrapper.getComponent(ElSelect).props('remoteMethod') as (query: string) => void
    remoteMethod('PI')

    expect(wrapper.emitted('search')).toContainEqual(['PI'])
  })

  it('disables details when a filtered graph no longer contains the selection', async () => {
    const wrapper = mount(GraphKeyboardNavigator, {
      props: {
        modelValue: 'edge:stale',
        options: [],
        busy: false,
      },
      global: {
        components: { ElButton, ElOption, ElSelect },
      },
    })

    expect(wrapper.get('[data-testid="graph-element-open"]').attributes('disabled')).toBeDefined()

    await wrapper.setProps({
      options: [{ key: 'edge:stale', kind: 'edge', label: 'A → B' }],
    })

    expect(wrapper.get('[data-testid="graph-element-open"]').attributes('disabled')).toBeUndefined()
  })
})
