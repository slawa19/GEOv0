import { afterEach, vi } from 'vitest'

afterEach(() => {
  vi.restoreAllMocks()
})

// JSDOM may not implement execCommand; tests can override per-case.
if (!(document as any).execCommand) {
  ;(document as any).execCommand = () => false
}
