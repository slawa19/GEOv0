import { describe, expect, it, vi } from 'vitest'

import ConfigPage from './ConfigPage.vue'
import { t } from '../i18n'
import { mountPage } from '../test/pageHarness'

/**
 * 032 S7 (D-16): what the Config screen shows is what the operator can change.
 *
 * Red on `01553c42`: a "Scope" column that said "Runtime" on every row of every section (a constant - a column that
 * cannot tell rows apart tells nothing), and section titles for logging and integrity-checkpoint keys that the page
 * is never given: `GET /admin/config` marks them `mutable: false` and the client facade drops them
 * (`flattenAdminConfig`), so those sections and their labels were unreachable.
 */

const apiMock = vi.hoisted(() => ({ getConfig: vi.fn(), patchConfig: vi.fn() }))
vi.mock('../api', () => ({ api: apiMock }))

const MUTABLE = {
  CLEARING_ENABLED: true,
  FEATURE_FLAGS_MULTIPATH_ENABLED: true,
  RATE_LIMIT_ENABLED: false,
  ROUTING_MAX_HOPS: 6,
  ROUTING_MAX_PATHS: 3,
}

describe('Config screen', () => {
  it('has a name column and a value column, and no constant "applies" column', async () => {
    apiMock.getConfig.mockResolvedValue(MUTABLE)
    const { wrapper } = await mountPage(ConfigPage, '/config')
    const headers = wrapper.findAll('.el-table__header th').map((th) => th.text())
    expect(headers).toEqual([t('config.columns.key'), t('common.value')])
    expect(wrapper.text()).not.toContain('Runtime')
    wrapper.unmount()
  })

  it('groups the keys the server lets an operator change into their sections, in order', async () => {
    apiMock.getConfig.mockResolvedValue(MUTABLE)
    const { wrapper } = await mountPage(ConfigPage, '/config')
    const titles = wrapper.findAll('.cfgSection__title').map((el) => el.text())
    expect(titles).toEqual([
      t('config.sections.featureFlags'),
      t('config.sections.rateLimit'),
      t('config.sections.routing'),
    ])
    wrapper.unmount()
  })

  it('lists a key the page does not know under "Other" instead of dropping it', async () => {
    apiMock.getConfig.mockResolvedValue({ ...MUTABLE, SOME_NEW_SWITCH: true })
    const { wrapper } = await mountPage(ConfigPage, '/config')
    const other = wrapper.findAll('.cfgSection').find((s) => s.find('.cfgSection__title').text() === t('config.sections.other'))
    expect(other?.text()).toContain('SOME_NEW_SWITCH')
    wrapper.unmount()
  })

  it('shows the load failure alone, not an empty list beside it', async () => {
    apiMock.getConfig.mockRejectedValue(new Error('config down'))
    const { wrapper } = await mountPage(ConfigPage, '/config')
    expect(wrapper.find('.el-alert--error').text()).toContain('config down')
    expect(wrapper.find('.el-empty').exists()).toBe(false)
    expect(wrapper.find('.cfgSection').exists()).toBe(false)
    wrapper.unmount()
  })
})
