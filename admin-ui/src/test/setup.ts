import { afterEach, vi } from 'vitest'

let nextAnimationFrameId = 0
const pendingAnimationFrames = new Map<number, FrameRequestCallback>()

Object.defineProperties(globalThis, {
  requestAnimationFrame: {
    configurable: true,
    value: (callback: FrameRequestCallback): number => {
      const frameId = ++nextAnimationFrameId
      pendingAnimationFrames.set(frameId, callback)
      queueMicrotask(() => {
        const pendingCallback = pendingAnimationFrames.get(frameId)
        if (!pendingCallback) return
        pendingAnimationFrames.delete(frameId)
        pendingCallback(performance.now())
      })
      return frameId
    },
  },
  cancelAnimationFrame: {
    configurable: true,
    value: (frameId: number): void => {
      pendingAnimationFrames.delete(frameId)
    },
  },
})

afterEach(() => {
  pendingAnimationFrames.clear()
  document.body.replaceChildren()
  vi.restoreAllMocks()
  // Vitest 4: restoreAllMocks no longer resets vi.fn() implementations (Vitest 2 did); reset keeps per-test isolation.
  vi.resetAllMocks()
  vi.clearAllMocks()
})

// JSDOM may not implement execCommand; tests can override per-case.
if (typeof document.execCommand !== 'function') {
  Object.defineProperty(document, 'execCommand', {
    value: () => false,
    configurable: true,
  })
}
