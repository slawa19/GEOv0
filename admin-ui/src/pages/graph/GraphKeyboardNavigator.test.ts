import { mount } from '@vue/test-utils'
import { ElButton, ElOption, ElSelect } from 'element-plus'
import { describe, expect, it } from 'vitest'

import GraphKeyboardNavigator from './GraphKeyboardNavigator.vue'

describe('GraphKeyboardNavigator', () => {
  it('keeps selection and opening unavailable until guarded rendering is enabled', async () => {
    const wrapper = mount(GraphKeyboardNavigator, {
      props: {
        modelValue: 'node:PID_A',
        options: [],
        busy: false,
        unavailable: true,
      },
      global: {
        components: { ElButton, ElOption, ElSelect },
      },
    })

    expect(wrapper.get('[data-testid="graph-element-select"] input').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="graph-element-open"]').attributes('disabled')).toBeDefined()

    await wrapper.setProps({ unavailable: false })

    expect(wrapper.get('[data-testid="graph-element-select"] input').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[data-testid="graph-element-open"]').attributes('disabled')).toBeUndefined()
  })
})
