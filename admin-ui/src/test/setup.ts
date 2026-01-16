import { afterEach, vi } from 'vitest'

afterEach(() => {
  vi.restoreAllMocks()
})

// JSDOM may not implement execCommand; tests can override per-case.
if (typeof document.execCommand !== 'function') {
  Object.defineProperty(document, 'execCommand', {
    value: () => false,
    configurable: true,
  })
}
