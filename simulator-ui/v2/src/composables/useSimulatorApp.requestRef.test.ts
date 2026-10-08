/**
 * 034 S5a, review round 1, item 2: the other message functions of the run lifecycle (`useSimulatorApp`,
 * `useSceneState`, `useCookieSessionBootstrap`) carry the server's request id as well.
 */
import { computed, reactive, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/http'
import { ensureSession } from '../api/simulatorApi'
import type { GraphSnapshot } from '../types'
import { useCookieSessionBootstrap } from './useCookieSessionBootstrap'
import { useSceneState } from './useSceneState'
import { __autoBootstrapMaybeFillUiError } from './useSimulatorApp'

vi.mock('../api/simulatorApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulatorApi')>()
  return { ...actual, ensureSession: vi.fn() }
})

const failure = () => new ApiError('HTTP 500  for /simulator/x', { status: 500, requestId: 'req-ui' })

afterEach(() => {
  vi.restoreAllMocks()
})

describe('item 2: message functions outside useSimulatorRealMode', () => {
  it('useSimulatorApp: the auto-start failure text carries the id', () => {
    const state = { error: '' }
    __autoBootstrapMaybeFillUiError({ state, real: { lastError: '' }, err: failure() })

    expect(state.error).toBe('Auto-start failed: HTTP 500  for /simulator/x (ref: req-ui)')
  })

  it('useSceneState: a failed scene load puts the id into state.error', async () => {
    const state = reactive({
      loading: false,
      error: '',
      sourcePath: '',
      snapshot: null as GraphSnapshot | null,
      selectedNodeId: null as string | null,
    })
    const noop = () => undefined
    const s = useSceneState({
      eq: ref('UAH'),
      scene: ref<'A' | 'B' | 'C'>('A'),
      layoutMode: ref<'admin-force'>('admin-force'),
      allowEqDeepLink: () => true,
      isEqAllowed: () => true,
      effectiveEq: computed(() => 'UAH'),
      state,
      loadSnapshot: async () => {
        throw failure()
      },
      clearScheduledTimeouts: noop,
      resetCamera: noop,
      resetLayoutKeyCache: noop,
      resetOverlays: noop,
      resizeAndLayout: noop,
      ensureRenderLoop: noop,
      setupResizeListener: noop,
      teardownResizeListener: noop,
      stopRenderLoop: noop,
    })

    await s.loadScene()

    expect(state.error).toContain('HTTP 500')
    expect(state.error).toContain('(ref: req-ui)')
  })

  it('useCookieSessionBootstrap: the warning that a developer greps in the console carries the id', async () => {
    vi.mocked(ensureSession).mockRejectedValueOnce(failure())
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const boot = useCookieSessionBootstrap({
      isRealMode: ref(true),
      apiBase: ref('http://x'),
      accessToken: ref(''),
    })

    await boot.tryEnsure()

    expect(warn).toHaveBeenCalledTimes(1)
    expect(String(warn.mock.calls[0]?.[1])).toContain('(ref: req-ui)')
  })
})
