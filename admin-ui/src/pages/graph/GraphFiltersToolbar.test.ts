import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { describe, expect, it } from 'vitest'

import GraphFiltersToolbar from './GraphFiltersToolbar.vue'
import { t } from '../../i18n'

/**
 * 032 S7 (D-15): the toolbar edits the page state through `v-model`s and reports its three actions as events.
 * Observed on the mounted toolbar: what the operator types or presses, and what the parent receives.
 */

const MODELS = {
  toolbarTab: 'filters' as const,
  eq: 'UAH',
  statusFilter: ['active'],
  threshold: '0.10',
  typeFilter: ['person'],
  minDegree: 0,
  layoutName: 'fcose' as const,
  layoutSpacing: 1,
  businessLabelParts: ['name' as const],
  personLabelParts: ['name' as const],
  showLabels: true,
  autoLabelsByZoom: true,
  hideIsolates: true,
  showLegend: false,
  searchQuery: '',
  focusPid: '',
  focusMode: false,
  focusDepth: 1 as const,
}

function mountToolbar(over: Record<string, unknown> = {}) {
  return mount(GraphFiltersToolbar, {
    props: {
      ...MODELS,
      availableEquivalents: ['UAH', 'EUR'],
      statuses: [{ label: 'Active', value: 'active' }],
      layoutOptions: [{ label: 'fcose', value: 'fcose' }],
      fetchSuggestions: (_q: string, cb: (r: never[]) => void) => cb([]),
      canFind: true,
      focusRootPid: '',
      canUseSelectedForFocus: true,
      ...over,
    },
    global: { plugins: [ElementPlus] },
  })
}

function thresholdInput(wrapper: ReturnType<typeof mountToolbar>) {
  return wrapper.find(`input[placeholder="${t('graph.filters.bottleneckPlaceholder')}"]`)
}

function buttonByText(wrapper: ReturnType<typeof mountToolbar>, text: string) {
  const found = wrapper.findAll('button').find((b) => b.text() === text)
  if (!found) throw new Error(`no button "${text}"`)
  return found
}

describe('GraphFiltersToolbar', () => {
  it('reports an edit of a filter as an update of that model, and only that one', async () => {
    const wrapper = mountToolbar()
    await thresholdInput(wrapper).setValue('0.25')
    const updates = wrapper.emitted('update:threshold') ?? []
    expect(updates[updates.length - 1]).toEqual(['0.25'])
    expect(wrapper.emitted('update:eq')).toBeUndefined()
    wrapper.unmount()
  })

  it('shows the value of a model it is given', () => {
    const wrapper = mountToolbar({ threshold: '0.33' })
    expect((thresholdInput(wrapper).element as HTMLInputElement).value).toBe('0.33')
    wrapper.unmount()
  })

  it('marks a threshold that is not a number from 0 to 1 as invalid', () => {
    expect(thresholdInput(mountToolbar({ threshold: '0.5' })).attributes('aria-invalid')).toBe('false')
    expect(thresholdInput(mountToolbar({ threshold: '5' })).attributes('aria-invalid')).toBe('true')
    expect(thresholdInput(mountToolbar({ threshold: 'abc' })).attributes('aria-invalid')).toBe('true')
  })

  it('emits the focus actions as events instead of calling callbacks', async () => {
    const wrapper = mountToolbar({ focusMode: true, focusRootPid: 'PID_X' })
    await buttonByText(wrapper, t('graph.navigate.useSelected')).trigger('click')
    await buttonByText(wrapper, t('graph.navigate.clear')).trigger('click')
    await buttonByText(wrapper, t('graph.navigate.find')).trigger('click')
    expect(wrapper.emitted('useSelectedForFocus')).toHaveLength(1)
    expect(wrapper.emitted('clearFocus')).toHaveLength(1)
    expect(wrapper.emitted('focusSearch')).toHaveLength(1)
    wrapper.unmount()
  })

  it('does not offer to clear the focus when it is not on, nor to use a selection when there is none', () => {
    const wrapper = mountToolbar({ focusMode: false, canUseSelectedForFocus: false })
    expect(buttonByText(wrapper, t('graph.navigate.clear')).attributes('disabled')).toBeDefined()
    expect(buttonByText(wrapper, t('graph.navigate.useSelected')).attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })
})
