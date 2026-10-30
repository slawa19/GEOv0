// 054-D-6: while `PATCH /admin/config` is in flight the draft cannot change, so the reload that follows a successful
// save cannot overwrite an edit the operator made meanwhile (and silently reset "Dirty"). Real ConfigPage + Element
// Plus + router; the API is mocked only at `src/api`.
import { describe, expect, it, vi } from 'vitest'

import ConfigPage from './ConfigPage.vue'
import { deferred, mountPage, settle } from '../test/pageHarness'

const apiMock = vi.hoisted(() => ({ getConfig: vi.fn(), patchConfig: vi.fn() }))
vi.mock('../api', () => ({ api: apiMock }))

const MUTABLE = {
  CLEARING_ENABLED: true,
  FEATURE_FLAGS_MULTIPATH_ENABLED: true,
  RATE_LIMIT_ENABLED: false,
  ROUTING_MAX_HOPS: 6,
  ROUTING_MAX_PATHS: 3,
  SOME_TEXT_KEY: 'abc',
  SOME_JSON_KEY: { a: 1 },
}

type Wrapper = Awaited<ReturnType<typeof mountPage>>['wrapper']

function rowOf(wrapper: Wrapper, key: string) {
  const row = wrapper.findAll('tr').find((tr) => tr.text().includes(key))
  if (!row) throw new Error(`row ${key} not found`)
  return row
}

function switchState(wrapper: Wrapper, key: string): boolean {
  return rowOf(wrapper, key).find('.el-switch').classes().includes('is-checked')
}

/** Every control in the value column that edits the draft: switches, numbers, text and JSON inputs. */
function draftControls(wrapper: Wrapper) {
  const switches = wrapper.findAll('.el-switch')
  const fields = wrapper.findAll('.cfgValueRow input:not(.el-switch__input), .cfgValueRow textarea')
  return { switches, fields }
}

function expectDraftLocked(wrapper: Wrapper, locked: boolean) {
  const { switches, fields } = draftControls(wrapper)
  expect(switches.length).toBe(3)
  expect(fields.length).toBe(4) // two numbers, one text, one JSON textarea
  for (const s of switches) expect(s.classes().includes('is-disabled')).toBe(locked)
  for (const f of fields) expect((f.element as HTMLInputElement).disabled).toBe(locked)
}

function saveButton(wrapper: Wrapper) {
  const btn = wrapper.findAll('button').find((b) => b.classes().includes('el-button--primary'))
  if (!btn) throw new Error('save button not found')
  return btn
}

describe('054-D-6 config draft is locked while PATCH is in flight', () => {
  it('controls are disabled during PATCH, an in-flight edit cannot be made, controls return after success', async () => {
    const patch = deferred<{ updated: string[] }>()
    apiMock.getConfig.mockReset().mockResolvedValueOnce({ ...MUTABLE })
    apiMock.patchConfig.mockReset().mockReturnValue(patch.promise)
    const { wrapper } = await mountPage(ConfigPage, '/config')

    expectDraftLocked(wrapper, false)
    await rowOf(wrapper, 'RATE_LIMIT_ENABLED').find('.el-switch').trigger('click')
    await settle()
    expect(switchState(wrapper, 'RATE_LIMIT_ENABLED')).toBe(true)
    await saveButton(wrapper).trigger('click')
    await settle()
    expect(apiMock.patchConfig).toHaveBeenCalledTimes(1)
    expect(apiMock.patchConfig.mock.calls[0]?.[0]).toEqual({ RATE_LIMIT_ENABLED: true })

    // In flight: every draft control is disabled and a click does not change the draft.
    expectDraftLocked(wrapper, true)
    await rowOf(wrapper, 'CLEARING_ENABLED').find('.el-switch').trigger('click')
    await settle()
    expect(switchState(wrapper, 'CLEARING_ENABLED')).toBe(true)

    apiMock.getConfig.mockResolvedValueOnce({ ...MUTABLE, RATE_LIMIT_ENABLED: true })
    patch.resolve({ updated: ['RATE_LIMIT_ENABLED'] })
    await settle()
    await settle()

    expect(apiMock.getConfig).toHaveBeenCalledTimes(2)
    expectDraftLocked(wrapper, false)
    expect(switchState(wrapper, 'CLEARING_ENABLED')).toBe(true)
    expect(switchState(wrapper, 'RATE_LIMIT_ENABLED')).toBe(true)
    wrapper.unmount()
  })

  it('a refused PATCH unlocks the controls and keeps the unsaved draft', async () => {
    const patch = deferred<{ updated: string[] }>()
    apiMock.getConfig.mockReset().mockResolvedValueOnce({ ...MUTABLE })
    apiMock.patchConfig.mockReset().mockReturnValue(patch.promise)
    const { wrapper } = await mountPage(ConfigPage, '/config')

    await rowOf(wrapper, 'RATE_LIMIT_ENABLED').find('.el-switch').trigger('click')
    await settle()
    await saveButton(wrapper).trigger('click')
    await settle()
    expectDraftLocked(wrapper, true)

    patch.reject(new Error('refused'))
    await settle()
    await settle()

    expect(apiMock.getConfig).toHaveBeenCalledTimes(1)
    expectDraftLocked(wrapper, false)
    expect(switchState(wrapper, 'RATE_LIMIT_ENABLED')).toBe(true)
    wrapper.unmount()
  })
})
