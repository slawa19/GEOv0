import { getCurrentScope, onScopeDispose } from 'vue'

export type LatestRequest = {
  isCurrent: () => boolean
}

/**
 * Gives each async operation exclusive ownership of state application.
 * Starting a newer operation (or disposing the current scope) invalidates all older tokens.
 */
export function useLatestRequest() {
  let generation = 0

  function begin(): LatestRequest {
    const requestGeneration = ++generation
    return {
      isCurrent: () => requestGeneration === generation,
    }
  }

  function invalidate() {
    generation += 1
  }

  if (getCurrentScope()) onScopeDispose(invalidate)

  return { begin, invalidate }
}
