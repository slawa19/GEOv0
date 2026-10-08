/**
 * 034 S5a, review round 1, item 4: a snapshot read that TIMES OUT on a live run must not silently swap the scene
 * for the scenario preview. The scene already shown stays (the scene loader keeps it when the load throws) and the
 * error reaches the user through the scene's own error channel. Every other failure keeps the existing contract:
 * 404 resets the stale run, anything else falls back to the preview.
 */
import { computed, reactive, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, API_TIMEOUT_CODE, isTimeoutError } from '../api/http'
import { getSnapshot } from '../api/simulatorApi'
import type { GraphSnapshot } from '../types'
import { useSceneState } from './useSceneState'
import { loadActiveRunSnapshot } from './useSimulatorApp'

vi.mock('../api/simulatorApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulatorApi')>()
  return { ...actual, getSnapshot: vi.fn() }
})

const INPUT = { apiBase: 'http://sim.test/api/v1', accessToken: 't', runId: 'run-1', equivalent: 'UAH' }

afterEach(() => {
  vi.restoreAllMocks()
  vi.mocked(getSnapshot).mockReset()
})

describe('loadActiveRunSnapshot()', () => {
  it('a timeout is thrown, not turned into a preview fallback, and does not reset the run', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const timeout = new ApiError('GET /x -> timeout after 30000ms', { status: 0, code: API_TIMEOUT_CODE })
    vi.mocked(getSnapshot).mockRejectedValueOnce(timeout)
    const onStaleRun = vi.fn()

    const result = await loadActiveRunSnapshot({ ...INPUT, onStaleRun }).then(
      (value) => ({ value }),
      (error: unknown) => ({ error }),
    )

    expect('error' in result && isTimeoutError(result.error), 'the timeout did not reach the caller').toBe(true)
    expect(onStaleRun).not.toHaveBeenCalled()
  })

  it('a 404 still resets the stale run and falls back (existing contract)', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    vi.mocked(getSnapshot).mockRejectedValueOnce(new ApiError('HTTP 404', { status: 404 }))
    const onStaleRun = vi.fn()

    await expect(loadActiveRunSnapshot({ ...INPUT, onStaleRun })).resolves.toBeNull()
    expect(onStaleRun).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['a 503', new ApiError('HTTP 503', { status: 503 })],
    ['a network error', new TypeError('Failed to fetch')],
  ])('%s still falls back to the preview (existing contract, unchanged)', async (_label, failure) => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    vi.mocked(getSnapshot).mockRejectedValueOnce(failure)
    const onStaleRun = vi.fn()

    await expect(loadActiveRunSnapshot({ ...INPUT, onStaleRun })).resolves.toBeNull()
    expect(onStaleRun).not.toHaveBeenCalled()
  })

  it('anti-vacuum: a good snapshot is returned with its source path and the particle cap', async () => {
    vi.mocked(getSnapshot).mockResolvedValueOnce({ equivalent: 'UAH', generated_at: 't', nodes: [], links: [] })

    const out = await loadActiveRunSnapshot({ ...INPUT, onStaleRun: vi.fn() })

    expect(out?.sourcePath).toBe('GET http://sim.test/api/v1/simulator/runs/run-1/graph/snapshot?equivalent=UAH')
    expect(out?.snapshot.limits).toEqual({ max_particles: 220 })
  })
})

describe('the scene loader and a thrown timeout', () => {
  it('keeps the scene already shown and reports the error in state.error', async () => {
    const noop = () => undefined
    const state = reactive({
      loading: false,
      error: '',
      sourcePath: '',
      snapshot: null as GraphSnapshot | null,
      selectedNodeId: null as string | null,
    })
    const shown: GraphSnapshot = { equivalent: 'UAH', generated_at: 't', nodes: [{ id: 'A' }], links: [] }
    let fail = false
    const s = useSceneState({
      eq: ref('UAH'),
      scene: ref<'A' | 'B' | 'C'>('A'),
      layoutMode: ref<'admin-force'>('admin-force'),
      allowEqDeepLink: () => true,
      isEqAllowed: () => true,
      effectiveEq: computed(() => 'UAH'),
      state,
      loadSnapshot: async () => {
        if (fail) throw new ApiError('GET /x -> timeout after 30000ms', { status: 0, code: API_TIMEOUT_CODE })
        return { snapshot: shown, sourcePath: 'GET /simulator/runs/run-1/graph/snapshot?equivalent=UAH' }
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
    expect(state.snapshot).toEqual(shown)

    fail = true
    await s.loadScene()

    expect(state.snapshot, 'the shown scene was dropped').toEqual(shown)
    expect(state.error).toContain('timeout')
  })
})
