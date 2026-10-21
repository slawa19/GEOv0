/**
 * BACKLOG 034-7 (d) - reproducer for the offline source (`?mode=fixtures`): the snapshot and playlist loader
 * (`fixtures.ts`, `loadJson`) waited for ever on a `fetch` that never answers and on a JSON body that never ends.
 * Target: both are bounded like every request of the REST client (`api/http.ts`, `DEFAULT_REQUEST_TIMEOUT_MS`) and the
 * failure reaches the existing error surface of the scene (`useSceneState.ts`, `state.error` / `errorLine`), whose
 * sentence for a timeout is the existing `failure.noAnswer`.
 *
 * Controlled clock only. The bound is not imported: any bound up to 120 s satisfies the test.
 */
import { computed, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api/http'
import { loadJson, loadSnapshot } from './fixtures'
import { useSceneState } from './composables/useSceneState'
import type { GraphSnapshot } from './types'

const BOUND_UPPER_MS = 120_000

type Outcome = { settled: false } | { settled: true; value: unknown } | { settled: true; error: unknown }

function track(call: Promise<unknown>): { outcome: () => Outcome } {
  let outcome: Outcome = { settled: false }
  call.then(
    (value) => {
      outcome = { settled: true, value }
    },
    (error: unknown) => {
      outcome = { settled: true, error }
    },
  )
  return { outcome: () => outcome }
}

function hangingFetch() {
  return vi.fn((_url: unknown, init?: RequestInit) => {
    return new Promise<Response>((_resolve, reject) => {
      const signal = init?.signal
      if (!signal) return
      const onAbort = () => reject(signal.reason ?? new DOMException('Aborted', 'AbortError'))
      if (signal.aborted) onAbort()
      else signal.addEventListener('abort', onAbort, { once: true })
    })
  })
}

/** Headers arrive, the JSON body starts and never ends. */
function endlessJsonFetch() {
  const enc = new TextEncoder()
  return vi.fn(async () => {
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(enc.encode('{"equivalent":"UAH","nodes":['))
      },
    })
    return new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
}

function expectTimeout(o: Outcome, message: string): void {
  expect(o.settled, message).toBe(true)
  const error = 'error' in o ? o.error : undefined
  expect(error).toBeInstanceOf(ApiError)
  expect((error as ApiError).code).toBe('TIMEOUT')
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('034-7 (d): the offline loader is bounded in time', () => {
  it('a fetch that never answers ends with ApiError TIMEOUT', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const call = track(loadJson('/simulator-fixtures/v1/UAH/snapshot.json'))

    await vi.advanceTimersByTimeAsync(BOUND_UPPER_MS)

    expectTimeout(call.outcome(), 'loadJson still waits for the answer after the bound: no bound exists')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('a JSON body that never ends ends with ApiError TIMEOUT', async () => {
    vi.stubGlobal('fetch', endlessJsonFetch())
    const call = track(loadJson('/simulator-fixtures/v1/UAH/snapshot.json'))

    await vi.advanceTimersByTimeAsync(BOUND_UPPER_MS)

    expectTimeout(call.outcome(), 'loadJson still reads the JSON body after the bound: no bound covers the body')
  })

  it('the timeout reaches the scene error surface and ends the loading state', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const state = reactive({
      loading: false,
      error: '',
      errorLine: null as unknown,
      sourcePath: '',
      snapshot: null as GraphSnapshot | null,
      selectedNodeId: null as string | null,
    })
    const eq = ref('UAH')
    const s = useSceneState({
      eq,
      layoutMode: ref('admin-force'),
      allowEqDeepLink: () => true,
      isEqAllowed: () => true,
      effectiveEq: computed(() => eq.value),
      state: state as Parameters<typeof useSceneState>[0]['state'],
      loadSnapshot,
      clearScheduledTimeouts: () => undefined,
      resetCamera: () => undefined,
      resetLayoutKeyCache: () => undefined,
      resetOverlays: () => undefined,
      resizeAndLayout: () => undefined,
      ensureRenderLoop: () => undefined,
      setupResizeListener: () => undefined,
      teardownResizeListener: () => undefined,
      stopRenderLoop: () => undefined,
    })

    const call = track(s.loadScene())
    await vi.advanceTimersByTimeAsync(0)
    expect(state.loading, 'the load did not start: the test drives nothing').toBe(true)

    await vi.advanceTimersByTimeAsync(BOUND_UPPER_MS)

    expect(call.outcome().settled, 'the scene load still waits after the bound').toBe(true)
    expect(state.loading, 'the loading overlay stays for ever').toBe(false)
    expect((state.errorLine as { key?: string } | null)?.key).toBe('failure.noAnswer')
    expect(state.error).not.toBe('')
  })
})
